import uuid
from datetime import date, datetime
from typing import Any, Dict

from pagos.constantes import (
    EstadosOrdenPago,
    EventosOrdenPago,
    OrigenEventoPago,
    ProveedoresPago,
)
from pagos.utilidades.validaciones_payvalida import (
    ErrorValidacionPayvalida,
    validar_fecha_expiracion_payvalida,
    validar_payload_creacion_antes_de_payvalida,
)
from pagos.excepciones import ErrorOrdenNoEncontrada, ErrorProveedorPago
from pagos.esquemas.esquema_orden_pago import SolicitudCrearOrdenPago
from pagos.infraestructura.clientes.cliente_payvalida import ClientePayvalida
from pagos.infraestructura.repositorios.repositorio_aplicaciones_pago import RepositorioAplicacionesPago
from pagos.infraestructura.repositorios.repositorio_auditoria_peticiones import RepositorioAuditoriaPeticiones
from pagos.infraestructura.repositorios.repositorio_ordenes_pago import RepositorioOrdenesPago
from pagos.infraestructura.repositorios.repositorio_solicitudes_proveedor import RepositorioSolicitudesProveedor
from pagos.aplicacion.servicios.servicio_eventos_pago import ServicioEventosPago
from pagos.aplicacion.servicios.servicio_payvalida import ServicioPayvalida
from pagos.utilidades.fechas import convertir_fecha_payvalida_a_mysql, obtener_fecha_actual_compacta
from pagos.utilidades.modelos import modelo_a_diccionario


class ServicioOrdenesPago:
    """
    Caso de uso principal para crear, consultar y sincronizar órdenes de pago.
    """

    def __init__(self):
        self.repositorio_aplicaciones = RepositorioAplicacionesPago()
        self.repositorio_ordenes = RepositorioOrdenesPago()
        self.repositorio_solicitudes = RepositorioSolicitudesProveedor()
        self.repositorio_auditoria = RepositorioAuditoriaPeticiones()
        self.servicio_eventos = ServicioEventosPago()
        self.servicio_payvalida = ServicioPayvalida()
        self.cliente_payvalida = ClientePayvalida()

    def generar_codigo_orden_interno(self) -> str:
        """
        Genera un código interno compatible con Payválida.

        No usa guiones, espacios ni caracteres especiales.
        """
        fecha = obtener_fecha_actual_compacta()
        consecutivo = uuid.uuid4().hex[:10].upper()
        return f"OP{fecha}{consecutivo}"

    def obtener_id_deal_bitrix(self, solicitud: SolicitudCrearOrdenPago) -> int | None:
        """
        Extrae el ID del deal Bitrix desde metadatos, si existe.
        """
        id_deal = (solicitud.metadatos or {}).get("id_deal_bitrix")
        try:
            return int(id_deal)
        except (TypeError, ValueError):
            return None

    def obtener_clave_pago_bitrix(self, solicitud: SolicitudCrearOrdenPago) -> str | None:
        """
        Extrae la clave estable de pago Bitrix, si el mapper la incluyo.
        """
        clave = (solicitud.metadatos or {}).get("clave_pago_bitrix")
        if clave is None:
            return None

        texto = str(clave).strip()
        return texto or None

    def fecha_expiracion_no_vencida(self, valor) -> bool:
        """
        Valida si una fecha de expiración local sigue activa.
        """
        if not valor:
            return False

        if isinstance(valor, datetime):
            fecha = valor.date()
        elif isinstance(valor, date):
            fecha = valor
        else:
            texto = str(valor).strip()
            fecha = None
            for formato in ("%Y-%m-%d", "%d/%m/%Y"):
                try:
                    fecha = datetime.strptime(texto[:10], formato).date()
                    break
                except ValueError:
                    continue

            if fecha is None:
                return False

        return fecha >= date.today()

    def orden_tiene_link_activo(self, orden: Dict[str, Any]) -> bool:
        """
        Determina si una orden existente puede reutilizarse sin llamar Payválida.
        """
        return (
            orden.get("estado") == EstadosOrdenPago.PENDIENTE
            and bool(orden.get("enlace_pago"))
            and bool(orden.get("id_orden_proveedor"))
            and bool(orden.get("referencia_proveedor"))
            and self.fecha_expiracion_no_vencida(orden.get("fecha_expiracion"))
        )

    async def registrar_auditoria_idempotente(
        self,
        orden: Dict[str, Any],
        solicitud: SolicitudCrearOrdenPago,
        contexto_auditoria: Dict[str, Any],
    ) -> None:
        await self.repositorio_auditoria.crear(
            contexto_auditoria=contexto_auditoria,
            id_orden_pago=orden["id"],
            sistema_origen=solicitud.sistema_origen,
            referencia_externa=orden.get("referencia_externa") or solicitud.referencia_externa,
            codigo_respuesta=200,
            exitoso=True,
        )

    async def obtener_orden_reutilizable_por_clave_pago_bitrix(
        self,
        solicitud: SolicitudCrearOrdenPago,
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """
        Busca una orden previa del mismo cliente/acuerdo y sincroniza con Payvalida
        antes de decidir si se reutiliza o si se permite crear una nueva.
        """
        clave_pago_bitrix = self.obtener_clave_pago_bitrix(solicitud)
        if not clave_pago_bitrix:
            return None

        candidatas = await self.repositorio_ordenes.obtener_candidatas_por_clave_pago_bitrix(
            sistema_origen=solicitud.sistema_origen,
            clave_pago_bitrix=clave_pago_bitrix,
        )

        for candidata in candidatas:
            await self.sincronizar_estado_orden_pago(
                id_orden_pago=candidata["id"],
                contexto_auditoria=contexto_auditoria,
            )

            orden_actualizada = await self.repositorio_ordenes.obtener_por_id(candidata["id"])
            if not orden_actualizada:
                continue

            if self.orden_tiene_link_activo(orden_actualizada):
                await self.registrar_auditoria_idempotente(
                    orden=orden_actualizada,
                    solicitud=solicitud,
                    contexto_auditoria=contexto_auditoria,
                )
                return orden_actualizada

            if orden_actualizada.get("estado") == EstadosOrdenPago.APROBADA:
                await self.registrar_auditoria_idempotente(
                    orden=orden_actualizada,
                    solicitud=solicitud,
                    contexto_auditoria=contexto_auditoria,
                )
                return orden_actualizada

            if orden_actualizada.get("estado") == EstadosOrdenPago.DESCONOCIDA:
                mensaje = (
                    "Existe una orden previa para el mismo cliente/acuerdo, "
                    "pero Payvalida no permitio confirmar su estado. "
                    "No se crea una orden duplicada."
                )
                await self.repositorio_auditoria.crear(
                    contexto_auditoria=contexto_auditoria,
                    id_orden_pago=orden_actualizada["id"],
                    sistema_origen=solicitud.sistema_origen,
                    referencia_externa=orden_actualizada.get("referencia_externa"),
                    codigo_respuesta=409,
                    exitoso=False,
                    mensaje_error=mensaje,
                )
                raise ErrorProveedorPago(mensaje)

        return None

    async def crear_en_payvalida_y_actualizar_orden(
        self,
        id_orden_pago: int,
        datos_orden: Dict[str, Any],
        datos_solicitud: Dict[str, Any],
        contexto_auditoria: Dict[str, Any],
        descripcion_evento_inicial: str = "Solicitud de creacion enviada a Payvalida",
    ) -> Dict[str, Any]:
        """
        Ejecuta la creacion en Payvalida para una orden local existente.

        Se usa tanto para ordenes nuevas como para recuperar ordenes locales que
        quedaron incompletas antes de guardar link/proveedor.
        """
        payload_payvalida = self.servicio_payvalida.construir_payload_creacion_orden(datos_orden)

        url_creacion = f"{self.cliente_payvalida.configuracion.url_base}/api/v3/porders"
        id_solicitud_proveedor = await self.repositorio_solicitudes.crear(
            id_orden_pago=id_orden_pago,
            proveedor=ProveedoresPago.PAYVALIDA,
            operacion="crear_orden",
            metodo_http="POST",
            url=url_creacion,
            payload_enviado=payload_payvalida,
        )

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=id_orden_pago,
            tipo_evento=EventosOrdenPago.SOLICITUD_CREACION_ENVIADA_A_PAYVALIDA,
            origen_evento=OrigenEventoPago.API_INTERNA,
            descripcion=descripcion_evento_inicial,
            datos_evento={"url": url_creacion},
        )

        respuesta_proveedor_registrada = False

        try:
            respuesta_payvalida, codigo_http, duracion_ms = await self.cliente_payvalida.crear_orden(
                payload_payvalida
            )

            exitoso = codigo_http in (200, 201) and respuesta_payvalida.get("CODE") == "0000"

            await self.repositorio_solicitudes.actualizar_respuesta(
                id_solicitud=id_solicitud_proveedor,
                respuesta_recibida=respuesta_payvalida,
                codigo_http=codigo_http,
                exitoso=exitoso,
                mensaje_error=None if exitoso else str(respuesta_payvalida),
                duracion_ms=duracion_ms,
            )
            respuesta_proveedor_registrada = True

            if not exitoso:
                await self.repositorio_ordenes.actualizar_estado(
                    id_orden_pago,
                    EstadosOrdenPago.FALLIDA,
                )
                await self.servicio_eventos.registrar_evento(
                    id_orden_pago=id_orden_pago,
                    tipo_evento=EventosOrdenPago.CREACION_PAYVALIDA_FALLIDA,
                    origen_evento=OrigenEventoPago.PAYVALIDA,
                    descripcion="Payvalida no creo la orden correctamente",
                    estado_nuevo=EstadosOrdenPago.FALLIDA,
                    datos_evento=respuesta_payvalida,
                )
                raise ErrorProveedorPago(f"Payvalida respondio error: {respuesta_payvalida}")

            data = respuesta_payvalida.get("DATA", {})
            id_proveedor = str(data.get("PVordenID")) if data.get("PVordenID") else None
            referencia_proveedor = str(data.get("Referencia")) if data.get("Referencia") else None
            enlace_pago = data.get("checkout")

            if not id_proveedor or not referencia_proveedor or not enlace_pago:
                mensaje = (
                    "Payvalida respondio exitosamente, pero la respuesta esta incompleta: "
                    "faltan checkout, PVordenID o Referencia."
                )
                await self.repositorio_ordenes.actualizar_estado(
                    id_orden_pago,
                    EstadosOrdenPago.FALLIDA,
                )
                await self.servicio_eventos.registrar_evento(
                    id_orden_pago=id_orden_pago,
                    tipo_evento=EventosOrdenPago.CREACION_PAYVALIDA_FALLIDA,
                    origen_evento=OrigenEventoPago.PAYVALIDA,
                    descripcion=mensaje,
                    estado_nuevo=EstadosOrdenPago.FALLIDA,
                    datos_evento=respuesta_payvalida,
                )
                raise ErrorProveedorPago(mensaje)

            await self.repositorio_ordenes.actualizar_datos_proveedor(
                id_orden_pago=id_orden_pago,
                datos={
                    "id_orden_proveedor": id_proveedor,
                    "referencia_proveedor": referencia_proveedor,
                    "enlace_pago": enlace_pago,
                    "estado_proveedor": data.get("Operacion"),
                    "respuesta_creacion_proveedor": respuesta_payvalida,
                },
            )

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.ENLACE_PAGO_GENERADO,
                origen_evento=OrigenEventoPago.PAYVALIDA,
                descripcion="Payvalida creo la orden y retorno enlace de pago",
                datos_evento=respuesta_payvalida,
            )

            orden_actualizada = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)
            return self.construir_respuesta_orden(orden_actualizada)

        except Exception as error:
            if not respuesta_proveedor_registrada:
                await self.repositorio_solicitudes.actualizar_respuesta(
                    id_solicitud=id_solicitud_proveedor,
                    respuesta_recibida=None,
                    codigo_http=None,
                    exitoso=False,
                    mensaje_error=str(error),
                    duracion_ms=None,
                )

            await self.repositorio_ordenes.actualizar_estado(
                id_orden_pago,
                EstadosOrdenPago.FALLIDA,
            )

            if respuesta_proveedor_registrada and isinstance(error, ErrorProveedorPago):
                raise

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.CREACION_PAYVALIDA_FALLIDA,
                origen_evento=OrigenEventoPago.SISTEMA,
                descripcion="Error al crear la orden en Payvalida",
                estado_nuevo=EstadosOrdenPago.FALLIDA,
                datos_evento={"error": str(error)},
            )
            raise

    async def crear_orden_pago(
        self,
        solicitud: SolicitudCrearOrdenPago,
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Crea una orden local, la registra en Payválida y retorna el enlace de pago.
        """
        datos_solicitud = modelo_a_diccionario(solicitud)

        orden_reutilizable = await self.obtener_orden_reutilizable_por_clave_pago_bitrix(
            solicitud=solicitud,
            contexto_auditoria=contexto_auditoria,
        )
        if orden_reutilizable:
            return self.construir_respuesta_orden(orden_reutilizable, idempotente=True)

        try:
            validar_payload_creacion_antes_de_payvalida(solicitud)
        except ErrorValidacionPayvalida as error:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=None,
                sistema_origen=solicitud.sistema_origen,
                referencia_externa=solicitud.referencia_externa,
                codigo_respuesta=422,
                exitoso=False,
                mensaje_error=error.mensaje,
            )
            raise

        id_deal_bitrix = self.obtener_id_deal_bitrix(solicitud)
        if id_deal_bitrix is not None:
            orden_activa_deal = await self.repositorio_ordenes.obtener_link_activo_por_deal_bitrix(
                sistema_origen=solicitud.sistema_origen,
                id_deal_bitrix=id_deal_bitrix,
            )

            if orden_activa_deal:
                await self.registrar_auditoria_idempotente(
                    orden=orden_activa_deal,
                    solicitud=solicitud,
                    contexto_auditoria=contexto_auditoria,
                )
                return self.construir_respuesta_orden(orden_activa_deal, idempotente=True)

        orden_existente = await self.repositorio_ordenes.obtener_por_sistema_y_referencia(
            sistema_origen=solicitud.sistema_origen,
            referencia_externa=solicitud.referencia_externa,
        )

        if orden_existente:
            orden_tiene_enlace = bool(orden_existente.get("enlace_pago"))
            orden_tiene_id_proveedor = bool(orden_existente.get("id_orden_proveedor"))
            orden_tiene_referencia_proveedor = bool(orden_existente.get("referencia_proveedor"))

            if orden_tiene_enlace and orden_tiene_id_proveedor and orden_tiene_referencia_proveedor:
                if self.orden_tiene_link_activo(orden_existente):
                    await self.registrar_auditoria_idempotente(
                        orden=orden_existente,
                        solicitud=solicitud,
                        contexto_auditoria=contexto_auditoria,
                    )
                    return self.construir_respuesta_orden(orden_existente, idempotente=True)

                mensaje = (
                    "Ya existe una orden previa para este sistema_origen y referencia_externa, "
                    "pero no tiene un link activo reutilizable porque no está PENDIENTE o ya venció."
                )
                await self.repositorio_auditoria.crear(
                    contexto_auditoria=contexto_auditoria,
                    id_orden_pago=orden_existente["id"],
                    sistema_origen=solicitud.sistema_origen,
                    referencia_externa=solicitud.referencia_externa,
                    codigo_respuesta=409,
                    exitoso=False,
                    mensaje_error=mensaje,
                )
                raise ErrorProveedorPago(mensaje)

            mensaje = (
                "Ya existe una orden previa para este sistema_origen y referencia_externa, "
                "pero la orden esta incompleta porque no tiene enlace de pago o datos del proveedor. "
                "Se intentara completarla nuevamente con Payvalida."
            )

            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=orden_existente["id"],
                sistema_origen=solicitud.sistema_origen,
                referencia_externa=solicitud.referencia_externa,
                codigo_respuesta=409,
                exitoso=False,
                mensaje_error=mensaje,
            )
            datos_orden_existente = dict(orden_existente)
            datos_orden_existente["fecha_expiracion_payvalida"] = solicitud.pago.fecha_expiracion
            datos_orden_existente["monto"] = solicitud.pago.monto
            datos_orden_existente["moneda"] = solicitud.pago.moneda
            datos_orden_existente["codigo_pais"] = solicitud.pago.codigo_pais
            datos_orden_existente["descripcion"] = solicitud.pago.descripcion
            datos_orden_existente["iva"] = solicitud.pago.iva
            datos_orden_existente["metodo_pago_solicitado"] = solicitud.pago.metodo_pago
            datos_orden_existente["recurrente"] = 1 if solicitud.pago.recurrente else 0
            datos_orden_existente["correo_cliente"] = solicitud.cliente.correo
            datos_orden_existente["tipo_documento_cliente"] = solicitud.cliente.tipo_documento
            datos_orden_existente["numero_documento_cliente"] = solicitud.cliente.numero_documento
            datos_orden_existente["nombre_cliente"] = solicitud.cliente.nombre
            datos_orden_existente["telefono_cliente"] = solicitud.cliente.telefono

            return await self.crear_en_payvalida_y_actualizar_orden(
                id_orden_pago=orden_existente["id"],
                datos_orden=datos_orden_existente,
                datos_solicitud=datos_solicitud,
                contexto_auditoria=contexto_auditoria,
                descripcion_evento_inicial=(
                    "Reintento de creacion en Payvalida para orden local incompleta"
                ),
            )

        id_aplicacion = await self.repositorio_aplicaciones.crear_si_no_existe(
            codigo=solicitud.sistema_origen,
            nombre=solicitud.sistema_origen,
        )

        codigo_orden_interno = self.generar_codigo_orden_interno()

        datos_orden = {
            "codigo_orden_interno": codigo_orden_interno,
            "id_aplicacion_consumidora": id_aplicacion,
            "sistema_origen": solicitud.sistema_origen,
            "referencia_externa": solicitud.referencia_externa,

            "correo_cliente": solicitud.cliente.correo,
            "tipo_documento_cliente": solicitud.cliente.tipo_documento,
            "numero_documento_cliente": solicitud.cliente.numero_documento,
            "nombre_cliente": solicitud.cliente.nombre,
            "telefono_cliente": solicitud.cliente.telefono,

            "monto": solicitud.pago.monto,
            "moneda": solicitud.pago.moneda,
            "codigo_pais": solicitud.pago.codigo_pais,
            "descripcion": solicitud.pago.descripcion,
            "iva": solicitud.pago.iva,
            "metodo_pago_solicitado": solicitud.pago.metodo_pago,
            "recurrente": 1 if solicitud.pago.recurrente else 0,
            "estado": EstadosOrdenPago.PENDIENTE,
            "proveedor": ProveedoresPago.PAYVALIDA,
            "fecha_expiracion": convertir_fecha_payvalida_a_mysql(solicitud.pago.fecha_expiracion),

            "ip_origen_solicitud": contexto_auditoria.get("ip_origen"),
            "ip_cliente_resuelta": contexto_auditoria.get("ip_cliente_resuelta"),
            "user_agent_solicitud": contexto_auditoria.get("user_agent"),
            "x_forwarded_for": contexto_auditoria.get("x_forwarded_for"),
            "x_real_ip": contexto_auditoria.get("x_real_ip"),

            "metadatos": solicitud.metadatos,
            "fecha_expiracion_payvalida": solicitud.pago.fecha_expiracion,
        }

        id_orden_pago = await self.repositorio_ordenes.crear(datos_orden)

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=id_orden_pago,
            tipo_evento=EventosOrdenPago.ORDEN_CREADA_LOCALMENTE,
            origen_evento=OrigenEventoPago.API_INTERNA,
            descripcion="Orden de pago creada localmente",
            estado_nuevo=EstadosOrdenPago.PENDIENTE,
            datos_evento=datos_solicitud,
        )

        return await self.crear_en_payvalida_y_actualizar_orden(
            id_orden_pago=id_orden_pago,
            datos_orden=datos_orden,
            datos_solicitud=datos_solicitud,
            contexto_auditoria=contexto_auditoria,
        )

    async def consultar_orden_pago(
        self,
        id_orden_pago: int,
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Consulta una orden de pago local.
        """
        orden = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)

        if not orden:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=None,
                codigo_respuesta=404,
                exitoso=False,
                mensaje_error="Orden de pago no encontrada",
            )
            raise ErrorOrdenNoEncontrada("Orden de pago no encontrada")

        await self.repositorio_auditoria.crear(
            contexto_auditoria=contexto_auditoria,
            id_orden_pago=id_orden_pago,
            sistema_origen=orden.get("sistema_origen"),
            referencia_externa=orden.get("referencia_externa"),
            codigo_respuesta=200,
            exitoso=True,
        )

        return self.construir_respuesta_orden(orden)

    async def sincronizar_estado_orden_pago(
        self,
        id_orden_pago: int,
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Consulta Payválida y actualiza el estado local.
        """
        orden = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)

        if not orden:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=None,
                codigo_respuesta=404,
                exitoso=False,
                mensaje_error="Orden de pago no encontrada",
            )
            raise ErrorOrdenNoEncontrada("Orden de pago no encontrada")

        checksum = self.servicio_payvalida.generar_checksum_consulta(
            orden["codigo_orden_interno"]
        )

        url_consulta = (
            f"{self.cliente_payvalida.configuracion.url_base}"
            f"/api/v3/porders/{orden['codigo_orden_interno']}"
        )

        id_solicitud = await self.repositorio_solicitudes.crear(
            id_orden_pago=id_orden_pago,
            proveedor=ProveedoresPago.PAYVALIDA,
            operacion="consultar_orden",
            metodo_http="GET",
            url=url_consulta,
            payload_enviado={
                "order": orden["codigo_orden_interno"],
                "merchant": self.cliente_payvalida.configuracion.merchant,
            },
        )

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=id_orden_pago,
            tipo_evento=EventosOrdenPago.CONSULTA_ESTADO_SOLICITADA,
            origen_evento=OrigenEventoPago.CONCILIACION,
            descripcion="Consulta manual de estado enviada a Payválida",
        )

        try:
            respuesta, codigo_http, duracion_ms = await self.cliente_payvalida.consultar_orden(
                codigo_orden_interno=orden["codigo_orden_interno"],
                merchant=self.cliente_payvalida.configuracion.merchant,
                checksum=checksum,
            )
        except Exception as error:
            await self.repositorio_solicitudes.actualizar_respuesta(
                id_solicitud=id_solicitud,
                respuesta_recibida=None,
                codigo_http=None,
                exitoso=False,
                mensaje_error=str(error),
                duracion_ms=None,
            )
            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.CONSULTA_ESTADO_FALLIDA,
                origen_evento=OrigenEventoPago.SISTEMA,
                descripcion="Error al consultar estado en Payválida",
                datos_evento={"error": str(error)},
            )
            raise

        exitoso = codigo_http in (200, 201)
        await self.repositorio_solicitudes.actualizar_respuesta(
            id_solicitud=id_solicitud,
            respuesta_recibida=respuesta,
            codigo_http=codigo_http,
            exitoso=exitoso,
            mensaje_error=None if exitoso else str(respuesta),
            duracion_ms=duracion_ms,
        )

        if not exitoso:
            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.CONSULTA_ESTADO_FALLIDA,
                origen_evento=OrigenEventoPago.PAYVALIDA,
                descripcion="Payválida no respondió correctamente la consulta de estado",
                datos_evento=respuesta,
            )
            raise ErrorProveedorPago(f"Error consultando Payválida: {respuesta}")

        data = respuesta.get("DATA", {})
        estado_proveedor = data.get("STATE") or data.get("state")
        estado_interno = self.servicio_payvalida.mapear_estado_payvalida(estado_proveedor)

        await self.repositorio_ordenes.actualizar_estado(
            id_orden_pago=id_orden_pago,
            estado=estado_interno,
            estado_proveedor=estado_proveedor,
            metodo_pago_usado=data.get("PAYMNENT_METHOD") or data.get("PAYMENT_METHOD"),
        )

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=id_orden_pago,
            tipo_evento=EventosOrdenPago.ESTADO_SINCRONIZADO,
            origen_evento=OrigenEventoPago.CONCILIACION,
            descripcion="Estado sincronizado con Payválida",
            estado_anterior=orden.get("estado"),
            estado_nuevo=estado_interno,
            datos_evento=respuesta,
        )

        await self.repositorio_auditoria.crear(
            contexto_auditoria=contexto_auditoria,
            id_orden_pago=id_orden_pago,
            sistema_origen=orden.get("sistema_origen"),
            referencia_externa=orden.get("referencia_externa"),
            codigo_respuesta=200,
            exitoso=True,
        )

        orden_actualizada = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)
        return self.construir_respuesta_orden(orden_actualizada)

    async def actualizar_expiracion_orden_pago(
        self,
        id_orden_pago: int,
        fecha_expiracion: str,
        contexto_auditoria: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Actualiza la expiración de una orden pendiente en Payválida y en base local.
        """
        orden = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)

        if not orden:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=None,
                codigo_respuesta=404,
                exitoso=False,
                mensaje_error="Orden de pago no encontrada",
            )
            raise ErrorOrdenNoEncontrada("Orden de pago no encontrada")

        if orden.get("estado") != EstadosOrdenPago.PENDIENTE:
            mensaje = "Solo se puede actualizar la expiración de órdenes en estado PENDIENTE."
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=id_orden_pago,
                sistema_origen=orden.get("sistema_origen"),
                referencia_externa=orden.get("referencia_externa"),
                codigo_respuesta=409,
                exitoso=False,
                mensaje_error=mensaje,
            )
            raise ErrorProveedorPago(mensaje)

        try:
            validar_fecha_expiracion_payvalida(fecha_expiracion)
        except ErrorValidacionPayvalida as error:
            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=id_orden_pago,
                sistema_origen=orden.get("sistema_origen"),
                referencia_externa=orden.get("referencia_externa"),
                codigo_respuesta=422,
                exitoso=False,
                mensaje_error=error.mensaje,
            )
            raise

        datos_orden = dict(orden)
        datos_orden["fecha_expiracion_payvalida"] = fecha_expiracion
        payload_payvalida = self.servicio_payvalida.construir_payload_creacion_orden(datos_orden)
        url_actualizacion = f"{self.cliente_payvalida.configuracion.url_base}/api/v3/porders"

        id_solicitud = await self.repositorio_solicitudes.crear(
            id_orden_pago=id_orden_pago,
            proveedor=ProveedoresPago.PAYVALIDA,
            operacion="actualizar_orden",
            metodo_http="PATCH",
            url=url_actualizacion,
            payload_enviado=payload_payvalida,
        )

        await self.servicio_eventos.registrar_evento(
            id_orden_pago=id_orden_pago,
            tipo_evento=EventosOrdenPago.ACTUALIZACION_PAYVALIDA_SOLICITADA,
            origen_evento=OrigenEventoPago.API_INTERNA,
            descripcion="Solicitud de actualización enviada a Payválida",
            datos_evento={"fecha_expiracion": fecha_expiracion},
        )

        respuesta_registrada = False

        try:
            respuesta_payvalida, codigo_http, duracion_ms = await self.cliente_payvalida.actualizar_orden(
                payload_payvalida
            )

            exitoso = codigo_http in (200, 201) and respuesta_payvalida.get("CODE") == "0000"
            await self.repositorio_solicitudes.actualizar_respuesta(
                id_solicitud=id_solicitud,
                respuesta_recibida=respuesta_payvalida,
                codigo_http=codigo_http,
                exitoso=exitoso,
                mensaje_error=None if exitoso else str(respuesta_payvalida),
                duracion_ms=duracion_ms,
            )
            respuesta_registrada = True

            if not exitoso:
                await self.servicio_eventos.registrar_evento(
                    id_orden_pago=id_orden_pago,
                    tipo_evento=EventosOrdenPago.ACTUALIZACION_PAYVALIDA_FALLIDA,
                    origen_evento=OrigenEventoPago.PAYVALIDA,
                    descripcion="Payválida no actualizó la orden correctamente",
                    datos_evento=respuesta_payvalida,
                )
                raise ErrorProveedorPago(f"Payválida respondió error al actualizar orden: {respuesta_payvalida}")

            data = respuesta_payvalida.get("DATA", {})
            await self.repositorio_ordenes.actualizar_expiracion_y_datos_proveedor(
                id_orden_pago=id_orden_pago,
                fecha_expiracion=convertir_fecha_payvalida_a_mysql(fecha_expiracion),
                datos={
                    "id_orden_proveedor": str(data.get("PVordenID")) if data.get("PVordenID") else None,
                    "referencia_proveedor": str(data.get("Referencia")) if data.get("Referencia") else None,
                    "enlace_pago": data.get("checkout"),
                    "estado_proveedor": data.get("Operacion"),
                    "respuesta_creacion_proveedor": respuesta_payvalida,
                },
            )

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.ORDEN_ACTUALIZADA_PAYVALIDA,
                origen_evento=OrigenEventoPago.PAYVALIDA,
                descripcion="Payválida actualizó la orden correctamente",
                datos_evento=respuesta_payvalida,
            )

            await self.repositorio_auditoria.crear(
                contexto_auditoria=contexto_auditoria,
                id_orden_pago=id_orden_pago,
                sistema_origen=orden.get("sistema_origen"),
                referencia_externa=orden.get("referencia_externa"),
                codigo_respuesta=200,
                exitoso=True,
            )

            orden_actualizada = await self.repositorio_ordenes.obtener_por_id(id_orden_pago)
            return self.construir_respuesta_orden(orden_actualizada)

        except Exception as error:
            if not respuesta_registrada:
                await self.repositorio_solicitudes.actualizar_respuesta(
                    id_solicitud=id_solicitud,
                    respuesta_recibida=None,
                    codigo_http=None,
                    exitoso=False,
                    mensaje_error=str(error),
                    duracion_ms=None,
                )

            if respuesta_registrada and isinstance(error, ErrorProveedorPago):
                raise

            await self.servicio_eventos.registrar_evento(
                id_orden_pago=id_orden_pago,
                tipo_evento=EventosOrdenPago.ACTUALIZACION_PAYVALIDA_FALLIDA,
                origen_evento=OrigenEventoPago.SISTEMA,
                descripcion="Error al actualizar la orden en Payválida",
                datos_evento={"error": str(error)},
            )
            raise

    def construir_respuesta_orden(
        self,
        orden: Dict[str, Any],
        idempotente: bool = False,
    ) -> Dict[str, Any]:
        """
        Construye la respuesta estándar para endpoints internos.
        """
        return {
            "id_orden_pago": orden["id"],
            "codigo_orden_interno": orden["codigo_orden_interno"],
            "sistema_origen": orden["sistema_origen"],
            "referencia_externa": orden["referencia_externa"],
            "proveedor": orden["proveedor"],
            "estado": orden["estado"],
            "monto": orden["monto"],
            "moneda": orden["moneda"],
            "enlace_pago": orden.get("enlace_pago"),
            "id_orden_proveedor": orden.get("id_orden_proveedor"),
            "referencia_proveedor": orden.get("referencia_proveedor"),
            "idempotente": idempotente,
        }
