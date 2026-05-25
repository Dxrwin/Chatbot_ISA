from typing import Any, Dict, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.infraestructura.repositorios.base_repositorio import convertir_a_json


class RepositorioEventosPago:
    """
    Repositorio para eventos_orden_pago.
    """

    async def crear(
        self,
        id_orden_pago: int,
        tipo_evento: str,
        origen_evento: str,
        descripcion: Optional[str] = None,
        estado_anterior: Optional[str] = None,
        estado_nuevo: Optional[str] = None,
        datos_evento: Optional[Dict[str, Any]] = None,
    ) -> int:
        consulta = """
            INSERT INTO eventos_orden_pago (
                id_orden_pago,
                tipo_evento,
                estado_anterior,
                estado_nuevo,
                origen_evento,
                descripcion,
                datos_evento
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """

        parametros = (
            id_orden_pago,
            tipo_evento,
            estado_anterior,
            estado_nuevo,
            origen_evento,
            descripcion,
            convertir_a_json(datos_evento),
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
                return cursor.lastrowid
