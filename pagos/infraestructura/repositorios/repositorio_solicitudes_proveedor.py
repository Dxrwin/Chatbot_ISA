from typing import Any, Dict, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.infraestructura.repositorios.base_repositorio import convertir_a_json


class RepositorioSolicitudesProveedor:
    """
    Repositorio para solicitudes_proveedor_pago.
    """

    async def crear(
        self,
        id_orden_pago: Optional[int],
        proveedor: str,
        operacion: str,
        metodo_http: str,
        url: str,
        payload_enviado: Optional[Dict[str, Any]] = None,
    ) -> int:
        consulta = """
            INSERT INTO solicitudes_proveedor_pago (
                id_orden_pago,
                proveedor,
                operacion,
                metodo_http,
                url,
                payload_enviado
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """

        parametros = (
            id_orden_pago,
            proveedor,
            operacion,
            metodo_http,
            url,
            convertir_a_json(payload_enviado),
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
                return cursor.lastrowid

    async def actualizar_respuesta(
        self,
        id_solicitud: int,
        respuesta_recibida: Optional[Dict[str, Any]],
        codigo_http: Optional[int],
        exitoso: bool,
        mensaje_error: Optional[str] = None,
        duracion_ms: Optional[int] = None,
    ) -> None:
        consulta = """
            UPDATE solicitudes_proveedor_pago
            SET respuesta_recibida = %s,
                codigo_http = %s,
                exitoso = %s,
                mensaje_error = %s,
                duracion_ms = %s
            WHERE id = %s
        """

        parametros = (
            convertir_a_json(respuesta_recibida),
            codigo_http,
            1 if exitoso else 0,
            mensaje_error,
            duracion_ms,
            id_solicitud,
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
