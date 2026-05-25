import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import aiomysql

from pagos.configuracion import obtener_configuracion_base_datos

_pool_mysql: Optional[aiomysql.Pool] = None
_bloqueo_pool = asyncio.Lock()


async def obtener_pool_mysql() -> aiomysql.Pool:
    """
    Crea o reutiliza el pool de conexiones MySQL del módulo de pagos.

    Se mantiene separado para no depender de una refactorización global.
    """
    global _pool_mysql

    if _pool_mysql is not None:
        return _pool_mysql

    async with _bloqueo_pool:
        if _pool_mysql is None:
            configuracion = obtener_configuracion_base_datos()

            _pool_mysql = await aiomysql.create_pool(
                host=configuracion.host,
                port=configuracion.puerto,
                user=configuracion.usuario,
                password=configuracion.clave,
                db=configuracion.base_datos,
                minsize=configuracion.pool_minimo,
                maxsize=configuracion.pool_maximo,
                autocommit=False,
            )

    return _pool_mysql


@asynccontextmanager
async def obtener_conexion_mysql():
    """
    Entrega una conexión del pool y la libera al finalizar.
    """
    pool = await obtener_pool_mysql()
    conexion = await pool.acquire()

    try:
        yield conexion
    finally:
        pool.release(conexion)
