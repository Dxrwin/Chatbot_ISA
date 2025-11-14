import mysql.connector
import logging
from datetime import datetime

# Importa el modelo de Pydantic
from models.models import InputVariables

# Importa la configuración
from utils.config import settings

# Configuración de la BD desde el objeto de settings
db_config = {
    "host": settings.DB_HOST,
    "user": settings.DB_USER,
    "password": settings.DB_PASS,
    "database": settings.DB_NAME
}

def guardar_en_bd(input_variables: InputVariables):
    """
    Guarda las variables de entrada en la base de datos.
    Ahora usa el modelo pydantic para más seguridad.
    """
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        query = (
            "INSERT INTO Clientes "
            "(NOMBRE, CORREO, NUMERO, SEMESTRE, LINEA_CREDITO, ESTADO_CREDITO, LINK, CUOTAS_PENDIENTES, FECHA_INSERCION) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        
        # Extrae valores del modelo Pydantic
        valores = (
            input_variables.NOMBRE_TITULAR,
            input_variables.CORREO,
            input_variables.PHONE_NUMBER,
            input_variables.SEMESTRE,
            input_variables.LINEA_CREDITO,
            input_variables.ESTADO_CREDITO,
            input_variables.LINK,
            input_variables.CUOTAS_PENDIENTES,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        cursor.execute(query, valores)
        conn.commit()
        
        logging.info("Datos insertados en la base de datos correctamente")

    except Exception as e:
        logging.error(f"Error al guardar en BD: {e}", exc_info=True)
        # Relanza la excepción para que el servicio superior la maneje
        raise
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()