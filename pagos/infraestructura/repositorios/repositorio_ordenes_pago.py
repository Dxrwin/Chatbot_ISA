import aiomysql
from typing import Any, Dict, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.infraestructura.repositorios.base_repositorio import convertir_a_json


class RepositorioOrdenesPago:
    """
    Repositorio para la tabla ordenes_pago.
    """

    async def crear(self, datos: Dict[str, Any]) -> int:
        consulta = """
            INSERT INTO ordenes_pago (
                codigo_orden_interno,
                id_aplicacion_consumidora,
                sistema_origen,
                referencia_externa,
                correo_cliente,
                tipo_documento_cliente,
                numero_documento_cliente,
                nombre_cliente,
                telefono_cliente,
                monto,
                moneda,
                codigo_pais,
                descripcion,
                iva,
                metodo_pago_solicitado,
                recurrente,
                estado,
                proveedor,
                fecha_expiracion,
                ip_origen_solicitud,
                ip_cliente_resuelta,
                user_agent_solicitud,
                x_forwarded_for,
                x_real_ip,
                metadatos
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
        """

        parametros = (
            datos.get("codigo_orden_interno"),
            datos.get("id_aplicacion_consumidora"),
            datos.get("sistema_origen"),
            datos.get("referencia_externa"),
            datos.get("correo_cliente"),
            datos.get("tipo_documento_cliente"),
            datos.get("numero_documento_cliente"),
            datos.get("nombre_cliente"),
            datos.get("telefono_cliente"),
            datos.get("monto"),
            datos.get("moneda"),
            datos.get("codigo_pais"),
            datos.get("descripcion"),
            datos.get("iva"),
            datos.get("metodo_pago_solicitado"),
            datos.get("recurrente"),
            datos.get("estado"),
            datos.get("proveedor"),
            datos.get("fecha_expiracion"),
            datos.get("ip_origen_solicitud"),
            datos.get("ip_cliente_resuelta"),
            datos.get("user_agent_solicitud"),
            datos.get("x_forwarded_for"),
            datos.get("x_real_ip"),
            convertir_a_json(datos.get("metadatos")),
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
                return cursor.lastrowid

    async def obtener_por_id(self, id_orden_pago: int) -> Optional[Dict[str, Any]]:
        consulta = "SELECT * FROM ordenes_pago WHERE id = %s LIMIT 1"

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, (id_orden_pago,))
                return await cursor.fetchone()

    async def obtener_por_codigo_interno(self, codigo_orden_interno: str) -> Optional[Dict[str, Any]]:
        consulta = "SELECT * FROM ordenes_pago WHERE codigo_orden_interno = %s LIMIT 1"

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, (codigo_orden_interno,))
                return await cursor.fetchone()

    async def obtener_por_sistema_y_referencia(
        self,
        sistema_origen: str,
        referencia_externa: str,
    ) -> Optional[Dict[str, Any]]:
        consulta = """
            SELECT *
            FROM ordenes_pago
            WHERE sistema_origen = %s
              AND referencia_externa = %s
            LIMIT 1
        """

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, (sistema_origen, referencia_externa))
                return await cursor.fetchone()

    async def actualizar_datos_proveedor(
        self,
        id_orden_pago: int,
        datos: Dict[str, Any],
    ) -> None:
        consulta = """
            UPDATE ordenes_pago
            SET id_orden_proveedor = %s,
                referencia_proveedor = %s,
                enlace_pago = %s,
                estado_proveedor = %s,
                respuesta_creacion_proveedor = %s
            WHERE id = %s
        """

        parametros = (
            datos.get("id_orden_proveedor"),
            datos.get("referencia_proveedor"),
            datos.get("enlace_pago"),
            datos.get("estado_proveedor"),
            convertir_a_json(datos.get("respuesta_creacion_proveedor")),
            id_orden_pago,
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()

    async def actualizar_estado(
        self,
        id_orden_pago: int,
        estado: str,
        estado_proveedor: str = None,
        metodo_pago_usado: str = None,
    ) -> None:
        consulta = """
            UPDATE ordenes_pago
            SET estado = %s,
                estado_proveedor = COALESCE(%s, estado_proveedor),
                metodo_pago_usado = COALESCE(%s, metodo_pago_usado),
                fecha_aprobacion = CASE WHEN %s = 'APROBADA' THEN NOW() ELSE fecha_aprobacion END,
                fecha_cancelacion = CASE WHEN %s IN ('CANCELADA', 'VENCIDA', 'ANULADA') THEN NOW() ELSE fecha_cancelacion END
            WHERE id = %s
        """

        parametros = (
            estado,
            estado_proveedor,
            metodo_pago_usado,
            estado,
            estado,
            id_orden_pago,
        )

        pool = await obtener_pool_mysql()
        async with pool.acquire() as conexion:
            async with conexion.cursor() as cursor:
                await cursor.execute(consulta, parametros)
                await conexion.commit()
