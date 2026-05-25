import aiomysql
from typing import Any, Dict, List, Optional

from pagos.infraestructura.conexion_mysql import obtener_pool_mysql
from pagos.utilidades.serializacion_bd import normalizar_lista_bd, normalizar_registro_bd


class RepositorioDepuracionPagos:
    """
    Repositorio de solo lectura para endpoints de depuración, control y auditoría.
    """

    async def ejecutar_lista(self, consulta: str, parametros: tuple = ()) -> List[Dict[str, Any]]:
        pool = await obtener_pool_mysql()

        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, parametros)
                registros = await cursor.fetchall()

        return normalizar_lista_bd(list(registros))

    async def ejecutar_uno(self, consulta: str, parametros: tuple = ()) -> Optional[Dict[str, Any]]:
        pool = await obtener_pool_mysql()

        async with pool.acquire() as conexion:
            async with conexion.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(consulta, parametros)
                registro = await cursor.fetchone()

        return normalizar_registro_bd(registro)

    async def listar_ordenes(
        self,
        estado: Optional[str] = None,
        sistema_origen: Optional[str] = None,
        referencia_externa: Optional[str] = None,
        limite: int = 50,
    ):
        condiciones = []
        parametros = []

        if estado:
            condiciones.append("estado = %s")
            parametros.append(estado)

        if sistema_origen:
            condiciones.append("sistema_origen = %s")
            parametros.append(sistema_origen)

        if referencia_externa:
            condiciones.append("referencia_externa = %s")
            parametros.append(referencia_externa)

        where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

        consulta = f"""
            SELECT
              id,
              codigo_orden_interno,
              sistema_origen,
              referencia_externa,
              correo_cliente,
              monto,
              moneda,
              estado,
              estado_proveedor,
              proveedor,
              id_orden_proveedor,
              referencia_proveedor,
              enlace_pago,
              metodo_pago_usado,
              fecha_aprobacion,
              fecha_cancelacion,
              ip_cliente_resuelta,
              user_agent_solicitud,
              creado_en,
              actualizado_en
            FROM ordenes_pago
            {where}
            ORDER BY creado_en DESC
            LIMIT %s
        """

        parametros.append(limite)
        return await self.ejecutar_lista(consulta, tuple(parametros))

    async def obtener_orden(self, id_orden_pago: int):
        return await self.ejecutar_uno(
            "SELECT * FROM ordenes_pago WHERE id = %s",
            (id_orden_pago,),
        )

    async def obtener_orden_por_codigo(self, codigo_orden_interno: str):
        return await self.ejecutar_uno(
            "SELECT * FROM ordenes_pago WHERE codigo_orden_interno = %s",
            (codigo_orden_interno,),
        )

    async def eventos_por_orden(self, id_orden_pago: int):
        consulta = """
            SELECT
              id,
              id_orden_pago,
              tipo_evento,
              estado_anterior,
              estado_nuevo,
              origen_evento,
              descripcion,
              datos_evento,
              creado_en
            FROM eventos_orden_pago
            WHERE id_orden_pago = %s
            ORDER BY creado_en ASC
        """
        return await self.ejecutar_lista(consulta, (id_orden_pago,))

    async def solicitudes_proveedor(
        self,
        id_orden_pago: Optional[int] = None,
        exitoso: Optional[int] = None,
        limite: int = 50,
    ):
        condiciones = []
        parametros = []

        if id_orden_pago is not None:
            condiciones.append("id_orden_pago = %s")
            parametros.append(id_orden_pago)

        if exitoso is not None:
            condiciones.append("exitoso = %s")
            parametros.append(exitoso)

        where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

        consulta = f"""
            SELECT
              id,
              id_orden_pago,
              proveedor,
              operacion,
              metodo_http,
              url,
              payload_enviado,
              respuesta_recibida,
              codigo_http,
              exitoso,
              mensaje_error,
              duracion_ms,
              creado_en,
              actualizado_en
            FROM solicitudes_proveedor_pago
            {where}
            ORDER BY creado_en DESC
            LIMIT %s
        """

        parametros.append(limite)
        return await self.ejecutar_lista(consulta, tuple(parametros))

    async def webhooks(
        self,
        id_orden_pago: Optional[int] = None,
        checksum_valido: Optional[int] = None,
        procesado: Optional[int] = None,
        limite: int = 50,
    ):
        condiciones = []
        parametros = []

        if id_orden_pago is not None:
            condiciones.append("id_orden_pago = %s")
            parametros.append(id_orden_pago)

        if checksum_valido is not None:
            condiciones.append("checksum_valido = %s")
            parametros.append(checksum_valido)

        if procesado is not None:
            condiciones.append("procesado = %s")
            parametros.append(procesado)

        where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

        consulta = f"""
            SELECT
              id,
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
              procesado,
              duplicado,
              mensaje_procesamiento,
              ip_cliente_resuelta,
              user_agent_webhook,
              payload_recibido,
              creado_en,
              procesado_en
            FROM notificaciones_webhook_pago
            {where}
            ORDER BY creado_en DESC
            LIMIT %s
        """

        parametros.append(limite)
        return await self.ejecutar_lista(consulta, tuple(parametros))

    async def peticiones_modulo(
        self,
        exitoso: Optional[int] = None,
        operacion: Optional[str] = None,
        limite: int = 100,
    ):
        condiciones = []
        parametros = []

        if exitoso is not None:
            condiciones.append("exitoso = %s")
            parametros.append(exitoso)

        if operacion:
            condiciones.append("operacion = %s")
            parametros.append(operacion)

        where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

        consulta = f"""
            SELECT
              id,
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
              user_agent,
              payload_recibido,
              codigo_respuesta,
              exitoso,
              mensaje_error,
              creado_en
            FROM peticiones_modulo_pago
            {where}
            ORDER BY creado_en DESC
            LIMIT %s
        """

        parametros.append(limite)
        return await self.ejecutar_lista(consulta, tuple(parametros))

    async def ordenes_pendientes(self, minutos: Optional[int] = None, limite: int = 100):
        if minutos is None:
            consulta = """
                SELECT
                  id,
                  codigo_orden_interno,
                  sistema_origen,
                  referencia_externa,
                  monto,
                  moneda,
                  estado,
                  enlace_pago,
                  creado_en
                FROM ordenes_pago
                WHERE estado = 'PENDIENTE'
                ORDER BY creado_en ASC
                LIMIT %s
            """
            return await self.ejecutar_lista(consulta, (limite,))

        consulta = """
            SELECT
              id,
              codigo_orden_interno,
              sistema_origen,
              referencia_externa,
              monto,
              moneda,
              estado,
              enlace_pago,
              creado_en
            FROM ordenes_pago
            WHERE estado = 'PENDIENTE'
              AND creado_en < DATE_SUB(NOW(), INTERVAL %s MINUTE)
            ORDER BY creado_en ASC
            LIMIT %s
        """
        return await self.ejecutar_lista(consulta, (minutos, limite))

    async def ordenes_sin_link(self, limite: int = 100):
        consulta = """
            SELECT
              id,
              codigo_orden_interno,
              sistema_origen,
              referencia_externa,
              estado,
              estado_proveedor,
              enlace_pago,
              id_orden_proveedor,
              referencia_proveedor,
              creado_en
            FROM ordenes_pago
            WHERE enlace_pago IS NULL
               OR id_orden_proveedor IS NULL
               OR referencia_proveedor IS NULL
            ORDER BY creado_en DESC
            LIMIT %s
        """
        return await self.ejecutar_lista(consulta, (limite,))

    async def errores_payvalida(self, limite: int = 50):
        consulta = """
            SELECT
              id,
              id_orden_pago,
              operacion,
              url,
              codigo_http,
              exitoso,
              JSON_UNQUOTE(JSON_EXTRACT(respuesta_recibida, '$.CODE')) AS codigo_payvalida,
              JSON_UNQUOTE(JSON_EXTRACT(respuesta_recibida, '$.DESC')) AS descripcion_payvalida,
              mensaje_error,
              creado_en
            FROM solicitudes_proveedor_pago
            WHERE exitoso = 0
            ORDER BY creado_en DESC
            LIMIT %s
        """
        return await self.ejecutar_lista(consulta, (limite,))

    async def resumen_endpoints(self):
        consulta = """
            SELECT
              ruta,
              operacion,
              COUNT(*) AS total_peticiones,
              SUM(CASE WHEN exitoso = 1 THEN 1 ELSE 0 END) AS exitosas,
              SUM(CASE WHEN exitoso = 0 THEN 1 ELSE 0 END) AS fallidas,
              MIN(creado_en) AS primera_peticion,
              MAX(creado_en) AS ultima_peticion
            FROM peticiones_modulo_pago
            GROUP BY ruta, operacion
            ORDER BY ultima_peticion DESC
        """
        return await self.ejecutar_lista(consulta)

    async def resumen_estados(self):
        consulta = """
            SELECT
              estado,
              COUNT(*) AS total,
              SUM(monto) AS total_monto
            FROM ordenes_pago
            GROUP BY estado
            ORDER BY total DESC
        """
        return await self.ejecutar_lista(consulta)

    async def resumen_ambientes_proveedor(self):
        consulta = """
            SELECT
              CASE
                WHEN url LIKE '%api-test.payvalida.com%' THEN 'sandbox'
                WHEN url LIKE '%api.payvalida.com%' THEN 'produccion'
                ELSE 'desconocido'
              END AS ambiente_detectado,
              operacion,
              COUNT(*) AS total,
              SUM(CASE WHEN exitoso = 1 THEN 1 ELSE 0 END) AS exitosas,
              SUM(CASE WHEN exitoso = 0 THEN 1 ELSE 0 END) AS fallidas,
              MAX(creado_en) AS ultima_peticion
            FROM solicitudes_proveedor_pago
            GROUP BY ambiente_detectado, operacion
            ORDER BY ultima_peticion DESC
        """
        return await self.ejecutar_lista(consulta)

    async def resumen_integral_orden(self, id_orden_pago: int):
        consulta = """
            SELECT
              o.id AS id_orden_pago,
              o.codigo_orden_interno,
              o.sistema_origen,
              o.referencia_externa,
              o.estado,
              o.monto,
              o.moneda,
              o.enlace_pago,
              o.id_orden_proveedor,
              o.referencia_proveedor,
              o.creado_en AS orden_creada_en,

              COUNT(DISTINCT e.id) AS total_eventos,
              COUNT(DISTINCT s.id) AS total_solicitudes_proveedor,
              COUNT(DISTINCT w.id) AS total_webhooks,

              MAX(s.creado_en) AS ultima_solicitud_proveedor,
              MAX(w.creado_en) AS ultimo_webhook

            FROM ordenes_pago o
            LEFT JOIN eventos_orden_pago e
              ON e.id_orden_pago = o.id
            LEFT JOIN solicitudes_proveedor_pago s
              ON s.id_orden_pago = o.id
            LEFT JOIN notificaciones_webhook_pago w
              ON w.id_orden_pago = o.id

            WHERE o.id = %s

            GROUP BY
              o.id,
              o.codigo_orden_interno,
              o.sistema_origen,
              o.referencia_externa,
              o.estado,
              o.monto,
              o.moneda,
              o.enlace_pago,
              o.id_orden_proveedor,
              o.referencia_proveedor,
              o.creado_en
        """
        return await self.ejecutar_uno(consulta, (id_orden_pago,))