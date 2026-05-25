from typing import Any, Dict, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.infraestructura.repositorios.base_repositorio import convertir_a_json


class RepositorioAuditoriaPeticiones:
    """
    Repositorio para peticiones_modulo_pago.
    Guarda cada consumo del módulo de pagos.
    """

    async def crear(
        self,
        contexto_auditoria: Dict[str, Any],
        id_orden_pago: Optional[int] = None,
        sistema_origen: Optional[str] = None,
        referencia_externa: Optional[str] = None,
        codigo_respuesta: Optional[int] = None,
        exitoso: Optional[bool] = None,
        mensaje_error: Optional[str] = None,
    ) -> int:
        consulta = """
            INSERT INTO peticiones_modulo_pago (
                id_orden_pago,
                metodo_http,
                ruta,
                operacion,
                sistema_origen,
                referencia_externa,
                ip_origen,
                ip_cliente_resuelta,
                x_forwarded_for,
                x_real_ip,
                forwarded,
                user_agent,
                payload_recibido,
                codigo_respuesta,
                exitoso,
                mensaje_error
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        parametros = (
            id_orden_pago,
            contexto_auditoria.get("metodo_http"),
            contexto_auditoria.get("ruta"),
            contexto_auditoria.get("operacion"),
            sistema_origen,
            referencia_externa,
            contexto_auditoria.get("ip_origen"),
            contexto_auditoria.get("ip_cliente_resuelta"),
            contexto_auditoria.get("x_forwarded_for"),
            contexto_auditoria.get("x_real_ip"),
            contexto_auditoria.get("forwarded"),
            contexto_auditoria.get("user_agent"),
            convertir_a_json(contexto_auditoria.get("payload_recibido")),
            codigo_respuesta,
            None if exitoso is None else (1 if exitoso else 0),
            mensaje_error,
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
                return cursor.lastrowid
