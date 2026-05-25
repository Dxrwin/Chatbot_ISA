from decimal import Decimal
from typing import Any, Dict, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.infraestructura.repositorios.base_repositorio import convertir_a_json


class RepositorioNotificacionesWebhook:
    """
    Repositorio para notificaciones_webhook_pago.
    """

    async def crear(
        self,
        payload: Dict[str, Any],
        contexto_auditoria: Dict[str, Any],
        id_orden_pago: Optional[int] = None,
        checksum_calculado: Optional[str] = None,
        checksum_valido: bool = False,
    ) -> int:
        consulta = """
            INSERT INTO notificaciones_webhook_pago (
                id_orden_pago,
                proveedor,
                id_orden_proveedor,
                codigo_orden_interno,
                estado_recibido,
                monto_recibido,
                moneda_recibida,
                metodo_pago_recibido,
                checksum_recibido,
                checksum_calculado,
                checksum_valido,
                ip_origen_webhook,
                ip_cliente_resuelta,
                user_agent_webhook,
                x_forwarded_for,
                x_real_ip,
                payload_recibido
            ) VALUES (%s, 'payvalida', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        monto = payload.get("amount")
        try:
            monto = Decimal(str(monto)) if monto is not None else None
        except Exception:
            monto = None

        parametros = (
            id_orden_pago,
            str(payload.get("pv_po_id")) if payload.get("pv_po_id") is not None else None,
            payload.get("po_id"),
            payload.get("status"),
            monto,
            payload.get("iso_currency"),
            payload.get("pv_payment"),
            payload.get("pv_checksum"),
            checksum_calculado,
            1 if checksum_valido else 0,
            contexto_auditoria.get("ip_origen"),
            contexto_auditoria.get("ip_cliente_resuelta"),
            contexto_auditoria.get("user_agent"),
            contexto_auditoria.get("x_forwarded_for"),
            contexto_auditoria.get("x_real_ip"),
            convertir_a_json(payload),
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
                return cursor.lastrowid

    async def actualizar_procesamiento(
        self,
        id_notificacion: int,
        procesado: bool,
        duplicado: bool,
        mensaje: str,
        id_orden_pago: Optional[int] = None,
        checksum_calculado: Optional[str] = None,
        checksum_valido: Optional[bool] = None,
    ) -> None:
        consulta = """
            UPDATE notificaciones_webhook_pago
            SET id_orden_pago = COALESCE(%s, id_orden_pago),
                checksum_calculado = COALESCE(%s, checksum_calculado),
                checksum_valido = COALESCE(%s, checksum_valido),
                procesado = %s,
                duplicado = %s,
                mensaje_procesamiento = %s,
                procesado_en = NOW()
            WHERE id = %s
        """

        parametros = (
            id_orden_pago,
            checksum_calculado,
            None if checksum_valido is None else (1 if checksum_valido else 0),
            1 if procesado else 0,
            1 if duplicado else 0,
            mensaje,
            id_notificacion,
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
