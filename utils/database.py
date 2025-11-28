import aiomysql
import logging
from utils.config import settings

db_config = {
    "host": settings.DB_HOST,
    "user": settings.DB_USER,
    "password": settings.DB_PASSWORD_RENOVACION,
    "database": settings.DB_NAME_RENOVACION
}


logger = logging.getLogger(__name__)



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
