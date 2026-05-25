from decimal import Decimal
from typing import Any, Dict

from pagos.constantes import (
    EstadosOrdenPago,
    EventosOrdenPago,
    OrigenEventoPago,
)
from pagos.excepciones import ErrorOrdenNoEncontrada, ErrorWebhookPayvalida
from pagos.esquemas.esquema_webhook_payvalida import NotificacionWebhookPayvalida
from pagos.infraestructura.repositorios.repositorio_auditoria_peticiones import RepositorioAuditoriaPeticiones
from pagos.infraestructura.repositorios.repositorio_notificaciones_webhook import RepositorioNotificacionesWebhook
from pagos.infraestructura.repositorios.repositorio_ordenes_pago import RepositorioOrdenesPago
from pagos.aplicacion.servicios.servicio_eventos_pago import ServicioEventosPago
from pagos.aplicacion.servicios.servicio_payvalida import ServicioPayvalida
from pagos.utilidades.modelos import modelo_a_diccionario


class ServicioWebhookPayvalida:
    """
    Servicio encargado de procesar notificaciones webhook de Payválida.
    """

    def __init__(self):
        self.repositorio_ordenes = RepositorioOrdenesPago()
        self.repositorio_notificaciones = RepositorioNotificacionesWebhook()
        self.repositorio_auditoria = RepositorioAuditoriaPeticiones()
        self.servicio_eventos = ServicioEventosPago()
        self.servicio_payvalida = ServicioPayvalida()

    async def procesar_notificacion_payvalida(
        self,
        payload: Dict[str, Any],
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Procesa una notificación enviada por Payválida.

        Flujo:
        1. Valida estructura.
        2. Calcula checksum esperado.
        3. Guarda notificación cruda.
        4. Busca orden local.
        5. Valida checksum, monto y moneda.
        6. Actualiza estado si corresponde.
        7. Registra auditoría y eventos.
        """
        notificacion = NotificacionWebhookPayvalida(**payload)
        datos_notificacion = modelo_a_diccionario(notificacion)

        checksum_calculado = self.servicio_payvalida.generar_checksum_notificacion(
            codigo_orden_interno=notificacion.po_id,
            estado=notificacion.status,
        )
        checksum_valido = checksum_calculado == notificacion.pv_checksum.upper()

        orden = await self.repositorio_ordenes.obtener_por_codigo_interno(notificacion.po_id)

        id_notificacion = await self.repositorio_notificaciones.crear(
            payload=datos_notificacion,
            contexto_auditoria=contexto_auditoria,
            id_orden_pago=orden["id"] if orden else None,
            checksum_calculado=checksum_calculado,
            checksum_valido=checksum_valido,
        )

        if not orden:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=None,
                sistema_origen=None,
                referencia_externa=None,
                codigo_respuesta=404,
                exitoso=False,
                mensaje_error="Orden de pago no encontrada para webhook",
            )
            raise ErrorOrdenNoEncontrada("Orden de pago no encontrada para webhook")

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=orden["id"],
            tipo_evento=EventosOrdenPago.WEBHOOK_PAYVALIDA_RECIBIDO,
            origen_evento=OrigenEventoPago.WEBHOOK,
            descripcion="Webhook de Payválida recibido",
            datos_evento=datos_notificacion,
        )

        if not checksum_valido:
            await self.repositorio_notificaciones.actualizar_procesamiento(
                id_notificacion=id_notificacion,
                procesado=False,
                duplicado=False,
                mensaje="Checksum inválido",
                id_orden_pago=orden["id"],
                checksum_calculado=checksum_calculado,
                checksum_valido=False,
            )

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=orden["id"],
                tipo_evento=EventosOrdenPago.WEBHOOK_PAYVALIDA_RECHAZADO,
                origen_evento=OrigenEventoPago.WEBHOOK,
                descripcion="Webhook rechazado por checksum inválido",
                datos_evento={
                    "checksum_recibido": notificacion.pv_checksum,
                    "checksum_calculado": checksum_calculado,
                },
            )
            raise ErrorWebhookPayvalida("Checksum inválido en webhook Payválida")

        monto_recibido = Decimal(str(notificacion.amount))
        monto_orden = Decimal(str(orden["monto"]))

        if monto_recibido != monto_orden or notificacion.iso_currency != orden["moneda"]:
            await self.repositorio_notificaciones.actualizar_procesamiento(
                id_notificacion=id_notificacion,
                procesado=False,
                duplicado=False,
                mensaje="Monto o moneda no coincide con la orden local",
                id_orden_pago=orden["id"],
                checksum_calculado=checksum_calculado,
                checksum_valido=True,
            )
            raise ErrorWebhookPayvalida("Monto o moneda no coincide con la orden local")

        estado_nuevo = self.servicio_payvalida.mapear_estado_payvalida(notificacion.status)
        duplicado = orden["estado"] == EstadosOrdenPago.APROBADA and estado_nuevo == EstadosOrdenPago.APROBADA

        if duplicado:
            await self.repositorio_notificaciones.actualizar_procesamiento(
                id_notificacion=id_notificacion,
                procesado=True,
                duplicado=True,
                mensaje="Webhook duplicado ignorado",
                id_orden_pago=orden["id"],
                checksum_calculado=checksum_calculado,
                checksum_valido=True,
            )

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=orden["id"],
                tipo_evento=EventosOrdenPago.WEBHOOK_DUPLICADO_IGNORADO,
                origen_evento=OrigenEventoPago.WEBHOOK,
                descripcion="Webhook aprobado duplicado ignorado",
                estado_anterior=orden["estado"],
                estado_nuevo=orden["estado"],
                datos_evento=datos_notificacion,
            )

            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=orden["id"],
                sistema_origen=orden.get("sistema_origen"),
                referencia_externa=orden.get("referencia_externa"),
                codigo_respuesta=200,
                exitoso=True,
            )

            return {"status": "OK", "mensaje": "Webhook duplicado ignorado"}

        await self.repositorio_ordenes.actualizar_estado(
            id_orden_pago=orden["id"],
            estado=estado_nuevo,
            estado_proveedor=notificacion.status,
            metodo_pago_usado=notificacion.pv_payment,
        )

        await self.repositorio_notificaciones.actualizar_procesamiento(
            id_notificacion=id_notificacion,
            procesado=True,
            duplicado=False,
            mensaje="Webhook procesado correctamente",
            id_orden_pago=orden["id"],
            checksum_calculado=checksum_calculado,
            checksum_valido=True,
        )

        tipo_evento = (
            EventosOrdenPago.ORDEN_APROBADA
            if estado_nuevo == EstadosOrdenPago.APROBADA
            else EventosOrdenPago.ORDEN_CANCELADA
        )

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=orden["id"],
            tipo_evento=tipo_evento,
            origen_evento=OrigenEventoPago.WEBHOOK,
            descripcion="Estado actualizado desde webhook de Payválida",
            estado_anterior=orden["estado"],
            estado_nuevo=estado_nuevo,
            datos_evento=datos_notificacion,
        )

        await self.repositorio_auditoria.crear(
            contexto_auditoria=contexto_auditoria,
            id_orden_pago=orden["id"],
            sistema_origen=orden.get("sistema_origen"),
            referencia_externa=orden.get("referencia_externa"),
            codigo_respuesta=200,
            exitoso=True,
        )

        return {"status": "OK", "mensaje": "Webhook procesado correctamente"}
