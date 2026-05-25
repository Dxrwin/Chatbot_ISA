import aiomysql
from typing import Optional, Dict, Any

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql


class RepositorioAplicacionesPago:
    """
    Repositorio para aplicaciones_consumidoras_pago.
    """

    async def obtener_por_codigo(self, codigo: str) -> Optional[Dict[str, Any]]:
        consulta = """
            SELECT *
            FROM aplicaciones_consumidoras_pago
            WHERE codigo = %s
            LIMIT 1
        """

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, (codigo,))
                return await cursor.fetchone()

    async def crear_si_no_existe(self, codigo: str, nombre: str = None) -> int:
        """
        Crea la aplicación consumidora si no existe y retorna su ID.
        """
        existente = await self.obtener_por_codigo(codigo)
        if existente:
            return existente["id"]

        consulta = """
            INSERT INTO aplicaciones_consumidoras_pago (
                codigo,
                nombre,
                descripcion,
                activo
            ) VALUES (%s, %s, %s, 1)
        """

        nombre_final = nombre or codigo
        descripcion = f"Aplicación consumidora registrada automáticamente: {codigo}"

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, (codigo, nombre_final, descripcion))
                await conexion.commit()
                return cursor.lastrowid
