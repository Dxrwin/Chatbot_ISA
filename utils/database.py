import aiomysql
import logging
from typing import Optional
from utils.notify_error import error_notify, get_cached_logs,send_log_email, send_log_telegram,info_notify
from utils.config import settings

db_config = {
    "host": settings.DB_HOST,
    "user": settings.DB_USER,
    "password": settings.DB_PASSWORD_RENOVACION,
    "database": settings.DB_NAME_RENOVACION
}


logger = logging.getLogger(__name__)

# --- DICCIONARIO DE MAPEO estado de creditos ---
# Clave (int): Valor (str - palabra en verde)
MAP_ESTADOS_CREDITO = {
    0: "EN PROCESO",
    1: "PENDIENTE",
    2: "APROBADO",
    3: "RECHAZADO",
    4: "FALLIDO",
    5: "FORMALIZADO",
    6: "DESEMBOLSANDO",
    7: "DESEMBOLSADO",
    8: "PAGADO",
    9: "DESISTIDO",
    10: "MORA",
    11: "NO RECLAMADO",
    12: "SIMULACION",
    13: "INCOMPLETO",
    14: "VERIFICACION",
    15: "CASTIGADO",
    16: "PAGO PENDIENTE",
    17: "ESPERANDO GARANTIAS",
    18: "REFINANCIANDO",
    19: "REFINANCIADO",
    20: "CONTRA PROPUESTA",
}


async def insertar_flujo_correo_post_agente(
    nombre_cliente: str,
    correo_enviado: str,
    numero_telefono: str,
    linea_universitaria: str,
) -> int:
    """
    Inserta (o actualiza) un registro en flujo_correo_post_agente.

    - numero_telefono es único, si ya existe se actualiza el registro
    y se refresca fecha_envio.
    """
    method_name = "insertar_flujo_correo_post_agente"

    try:
        connection = await aiomysql.connect(
            host= db_config["host"],
            user= db_config["user"],
            password= db_config["password"],
            db= db_config["database"]
        )

        try:
            async with connection.cursor() as cursor:
                query = """
                    INSERT INTO flujo_correo_post_agente (
                        nombre_cliente,
                        correo_enviado,
                        numero_telefono,
                        linea_universitaria
                    )
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        nombre_cliente = VALUES(nombre_cliente),
                        correo_enviado = VALUES(correo_enviado),
                        linea_universitaria = VALUES(linea_universitaria),
                        fecha_envio = CURRENT_TIMESTAMP;
                """

                await cursor.execute(
                    query,
                    (
                        nombre_cliente,
                        correo_enviado,
                        numero_telefono,
                        linea_universitaria,
                    ),
                )

                await connection.commit()

                insert_id = cursor.lastrowid
                logger.info(
                    f"[{method_name}] Registro insertado/actualizado. ID: {insert_id} | Teléfono: {numero_telefono}"
                )

                # Si fue UPDATE, lastrowid puede venir 0; a nivel de negocio
                # igual ya quedó registrado.
                return insert_id or 0

        finally:
            connection.close()

    except aiomysql.Error as db_error:
        logger.error(f"[{method_name}] Error de base de datos: {str(db_error)}", exc_info=True)
        # Aquí puedes llamar a error_notify si quieres
        # await error_notify(...)
        raise


async def insertar_registro_completo(data_cliente: dict, data_credito: dict):
    try:
        connection = await aiomysql.connect(
            host=settings.DB_HOST,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD_RENOVACION,
            db=settings.DB_NAME_RENOVACION
        )
        
        async with connection.cursor() as cursor:
            await connection.begin()
            
            try:
                # --- VERIFICACIÓN DE DUPLICADOS ---
                # Verificar si ya existe un registro con el mismo numero_telefono, ID_credito_simulacion y referencia_simulacion
                numero_telefono_check = str(data_cliente.get('numero_telefono', ''))[:15]
                id_credito_check = str(data_credito.get('ID_Credito_simulacion', ''))[:50]
                referencia_check = str(data_credito.get('referencia_simulacion', ''))[:50]
                
                query_check = """
                    SELECT c.estado_credito_post_confirmado, c.estado_credito 
                    FROM renovaciones_clientes rc
                    INNER JOIN credito c ON rc.id_credito_simulacion = c.ID_Credito_simulacion
                    WHERE rc.numero_telefono = %s 
                    AND c.ID_Credito_simulacion = %s 
                    AND c.referencia_simulacion = %s
                """
                
                await cursor.execute(query_check, (numero_telefono_check, id_credito_check, referencia_check))
                existing_record = await cursor.fetchone()
                
                if existing_record:
                    # Ya existe un registro con esta combinación
                    estado_actual = existing_record[0]  # estado_credito_post_confirmado
                    estado_texto_actual = existing_record[1]  # estado_credito
                    
                    if estado_actual == estado_id:
                        # Estado igual, es duplicado
                        logger.warning(f"Registro duplicado detectado: numero_telefono={numero_telefono_check}, ID_credito={id_credito_check}, referencia={referencia_check}, estado={estado_id}")
                        await error_notify(
                            "insertar_registro_completo_en_db", 
                            numero_telefono_check, 
                            f"Intento de registro duplicado rechazado para número de teléfono {numero_telefono_check}, ID de crédito {id_credito_check}, referencia {referencia_check} y estado {estado_id}"
                        )
                        await connection.rollback()
                        return {
                            "status": "error",
                            "message": "Ya existe un registro con el mismo número de teléfono, ID de crédito, referencia de simulación y estado. No se puede registrar la renovación duplicada.",
                            "error_type": "DUPLICATE_RECORD",
                            "details": {
                                "numero_telefono": numero_telefono_check,
                                "ID_credito_simulacion": id_credito_check,
                                "referencia_simulacion": referencia_check,
                                "estado_actual": estado_actual,
                                "estado_nuevo": estado_id
                            }
                        }
                    else:
                        # Estado diferente, actualizar el registro existente
                        logger.info(f"Actualizando estado del registro existente: {estado_actual} -> {estado_id}")
                        
                        update_query = """
                            UPDATE credito 
                            SET estado_credito_post_confirmado = %s, estado_credito = %s
                            WHERE ID_Credito_simulacion = %s
                        """
                        await cursor.execute(update_query, (estado_id, estado_texto, id_credito_check))
                        await connection.commit()
                        await info_notify(
                            "insertar_registro_completo_en_db", 
                            numero_telefono_check, 
                            f"Registro existente actualizado para número de teléfono {numero_telefono_check}, ID de crédito {id_credito_check}, referencia {referencia_check} de estado {estado_actual} a {estado_id}"
                        )
                        return {
                            "status": "updated",
                            "message": f"Registro actualizado exitosamente. Estado cambiado de {estado_actual} ({estado_texto_actual}) a {estado_id} ({estado_texto})",
                            "id_credito": id_credito_check,
                            "estado_anterior": estado_actual,
                            "estado_nuevo": estado_id
                        }
                
                # --- Conversión de Tipos y Validaciones ---
                # Convertir cuota_inicial_simulacion a DECIMAL(15,2) - Redondear a 2 decimales
                cuota_inicial = None
                if data_credito.get('cuota_inicial_simulacion'):
                    try:
                        cuota_inicial = round(float(str(data_credito.get('cuota_inicial_simulacion')).strip()), 2)
                        # Limitar a 999999999.99 para evitar overflow
                        if cuota_inicial > 999999999.99:
                            logger.warning(f"cuota_inicial limitada: {cuota_inicial} -> 999999999.99")
                            cuota_inicial = 999999999.99
                    except (ValueError, TypeError) as e:
                        logger.warning(f"No se pudo convertir cuota_inicial: {e}")
                        cuota_inicial = None
                
                # Convertir estado_credito_post_confirmado a INT
                estado_id = None
                if data_credito.get('estado_credito_post_confirmado'):
                    try:
                        estado_id = int(str(data_credito.get('estado_credito_post_confirmado')).strip())
                    except (ValueError, TypeError) as e:
                        logger.warning(f"No se pudo convertir estado_id: {e}")
                        estado_id = None
                
                # Obtener el texto del mapa, o dejar "DESCONOCIDO" si el numero no existe
                estado_texto = None
                if estado_id is not None:
                    estado_texto = MAP_ESTADOS_CREDITO.get(estado_id, "DESCONOCIDO")
                
                # Convertir valor_solicitud_express a DECIMAL(15,2)
                valor_solicitud = None
                if data_credito.get('valor_solicitud_express'):
                    try:
                        valor_solicitud = round(float(str(data_credito.get('valor_solicitud_express')).strip()), 2)
                        # Limitar a 999999999.99 para evitar overflow
                        if valor_solicitud > 999999999.99:
                            logger.warning(f"valor_solicitud limitado: {valor_solicitud} -> 999999999.99")
                            valor_solicitud = 999999999.99
                    except (ValueError, TypeError) as e:
                        logger.warning(f"No se pudo convertir valor_solicitud: {e}")
                        valor_solicitud = None

                # 1. Insertar en CREDITO
                query_credito = """
                    INSERT INTO credito (
                        ID_Credito_simulacion, 
                        referencia_simulacion, 
                        nombre_linea_simulacion, 
                        cuota_inicial_simulacion, 
                        semestre_renovacion, 
                        estado_credito_post_confirmado, 
                        estado_credito, 
                        valor_solicitud_express
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
                # Truncar campos de texto para la tabla credito
                id_credito = str(data_credito.get('ID_Credito_simulacion', ''))[:50]
                referencia = str(data_credito.get('referencia_simulacion', ''))[:50]
                nombre_linea = str(data_credito.get('nombre_linea_simulacion', ''))[:100]
                semestre = str(data_credito.get('semestre_renovacion', ''))[:50]
                
                # Log si se truncaron valores
                if len(str(data_credito.get('ID_Credito_simulacion', ''))) > 50:
                    logger.warning(f"ID_Credito_simulacion truncado: {data_credito.get('ID_Credito_simulacion')} -> {id_credito}")
                if len(str(data_credito.get('referencia_simulacion', ''))) > 50:
                    logger.warning(f"referencia_simulacion truncada: {data_credito.get('referencia_simulacion')} -> {referencia}")
                if len(str(data_credito.get('nombre_linea_simulacion', ''))) > 100:
                    logger.warning(f"nombre_linea_simulacion truncado: {data_credito.get('nombre_linea_simulacion')} -> {nombre_linea}")
                
                valores_credito = (
                    id_credito,
                    referencia,
                    nombre_linea,
                    cuota_inicial,
                    semestre,
                    estado_id,
                    estado_texto,
                    valor_solicitud
                )
                logger.info(f"Insertando en credito con valores: {valores_credito}")
                await cursor.execute(query_credito, valores_credito)

                # 2. Insertar en RENOVACIONES_CLIENTES
                query_cliente = """
                    INSERT INTO renovaciones_clientes (
                        numero_telefono, 
                        correo_cliente, 
                        nombre_cliente,
                        cedula,
                        id_credito_simulacion
                    ) VALUES (%s, %s, %s, %s, %s)
                """
                # Truncar campos de texto para evitar errores de longitud
                numero_telefono = str(data_cliente.get('numero_telefono', ''))[:15]
                correo_cliente = str(data_cliente.get('correo_cliente', ''))[:100]
                nombre_cliente = str(data_cliente.get('nombre_cliente', ''))[:100]
                cedula_cliente = str(data_cliente.get('cedula_cliente', ''))[:20]
                
                # Log si se truncaron valores
                if len(str(data_cliente.get('numero_telefono', ''))) > 15:
                    logger.warning(f"numero_telefono truncado: {data_cliente.get('numero_telefono')} -> {numero_telefono}")
                if len(str(data_cliente.get('correo_cliente', ''))) > 100:
                    logger.warning(f"correo_cliente truncado: {data_cliente.get('correo_cliente')} -> {correo_cliente}")
                if len(str(data_cliente.get('nombre_cliente', ''))) > 100:
                    logger.warning(f"nombre_cliente truncado: {data_cliente.get('nombre_cliente')} -> {nombre_cliente}")
                if len(str(data_cliente.get('cedula_cliente', ''))) > 20:
                    logger.warning(f"cedula_cliente truncada: {data_cliente.get('cedula_cliente')} -> {cedula_cliente}")
                
                valores_cliente = (
                    numero_telefono,
                    correo_cliente,
                    nombre_cliente,
                    cedula_cliente,
                    data_credito['ID_Credito_simulacion']
                )
                await cursor.execute(query_cliente, valores_cliente)
                
                id_renovacion = cursor.lastrowid
                await connection.commit()
                
                logger.info(f"Registro exitoso. Estado num: {estado_id} -> Texto: {estado_texto}")
                await info_notify(
                    "insertar_registro_completo_en_db",
                    numero_telefono_check,
                    f"Renovación registrada exitosamente para número de teléfono {numero_telefono_check},ID de crédito {id_credito_check}, referencia {referencia_check} con estado {estado_id}"
                )
                
                return {
                    "status": "success", 
                    "id_renovacion": id_renovacion, 
                    "estado_mapeado": estado_texto
                }

            except Exception as e:
                await connection.rollback()
                logger.error(f"Error en transacción DB: {e}")
                raise e

    except Exception as e:
        logger.error(f"Error de conexión DB: {e}")
        raise e
    finally:
        if 'connection' in locals() and connection:
            connection.close()
            

# --- 2. SERVICIO DE BÚSQUEDA DINÁMICA ---
async def consultar_creditos_filtro(
    cedula: Optional[str] = None, 
    telefono: Optional[str] = None, 
    estado_id: Optional[int] = None, 
    fecha_inicio: Optional[str] = None, # Formato YYYY-MM-DD
    fecha_fin: Optional[str] = None     # Formato YYYY-MM-DD
):
    """
    Consulta dinámica que une las dos tablas y filtra según lo que se envíe.
    """
    try:
        connection = await aiomysql.connect(
            host=settings.DB_HOST, user=settings.DB_USER,
            password=settings.DB_PASSWORD_RENOVACION, db=settings.DB_NAME_RENOVACION
        )
        
        async with connection.cursor(aiomysql.DictCursor) as cursor: # DictCursor devuelve JSON directo
            
            # Base de la consulta: JOIN entre Cliente y Crédito
            sql = """
                SELECT 
                    rc.nombre_cliente,
                    rc.cedula,
                    rc.numero_telefono,
                    rc.correo_cliente,
                    rc.fecha_registro,
                    c.ID_Credito_simulacion,
                    c.estado_credito_post_confirmado AS estado_id,
                    c.estado_credito AS estado_texto,
                    c.cuota_inicial_simulacion,
                    c.valor_solicitud_express
                FROM renovaciones_clientes rc
                JOIN credito c ON rc.id_credito_simulacion = c.ID_Credito_simulacion
                WHERE 1=1 
            """
            
            params = []

            # --- Construcción Dinámica de Filtros ---
            if cedula:
                sql += " AND rc.cedula = %s"
                params.append(cedula)
            
            if telefono:
                sql += " AND rc.numero_telefono = %s"
                params.append(telefono)
                
            if estado_id is not None:
                sql += " AND c.estado_credito_post_confirmado = %s"
                params.append(estado_id)
                
            # Filtro de Fechas (Rango)
            if fecha_inicio and fecha_fin:
                # Busca entre el inicio del día 1 y el final del día 2
                sql += " AND rc.fecha_registro BETWEEN %s AND %s"
                params.append(f"{fecha_inicio} 00:00:00")
                params.append(f"{fecha_fin} 23:59:59")
            elif fecha_inicio:
                # Solo un día especifico
                sql += " AND DATE(rc.fecha_registro) = %s"
                params.append(fecha_inicio)

            # Ordenar por el más reciente y Limitar resultados (Seguridad de DB)
            sql += " ORDER BY rc.fecha_registro DESC LIMIT 100"

            await cursor.execute(sql, tuple(params))
            resultado = await cursor.fetchall()
            
            return resultado

    except Exception as e:
        logger.error(f"Error consultando créditos: {e}")
        raise e
    finally:
        if 'connection' in locals() and connection:
            connection.close()