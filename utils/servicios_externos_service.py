import aiomysql
import json
import logging
from typing import Optional, Any

from utils.config import settings

logger = logging.getLogger(__name__)

db_config = {
    "host": settings.DB_HOST,
    "user": settings.DB_USER,
    "password": settings.DB_PASSWORD_RENOVACION,
    "database": settings.DB_NAME_RENOVACION,
}

ALLOWED_FIELDS = {
    "nombre_servicio",
    "codigo",
    "url",
    "metodo",
    "timeout_ms",
    "reintentos",
    "activo",
    "header",
    "body",
}


def _dump_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _load_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def crear_servicio_externo(data: dict) -> int:
    required = ["nombre_servicio", "codigo", "url", "metodo"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    timeout_ms = int(data.get("timeout_ms", 10000))
    reintentos = int(data.get("reintentos", 0))
    activo = int(data.get("activo", 1))
    metodo = str(data.get("metodo", "")).upper()

    query = """
        INSERT INTO servicios_externos (
            nombre_servicio,
            codigo,
            url,
            metodo,
            timeout_ms,
            reintentos,
            activo,
            header,
            body
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    params = (
        data["nombre_servicio"],
        data["codigo"],
        data["url"],
        metodo,
        timeout_ms,
        reintentos,
        activo,
        _dump_json(data.get("header")),
        _dump_json(data.get("body")),
    )

    try:
        connection = await aiomysql.connect(
            host=db_config["host"],
            user=db_config["user"],
            password=db_config["password"],
            db=db_config["database"],
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                await connection.commit()
                return cursor.lastrowid or 0
        finally:
            connection.close()
    except aiomysql.Error as exc:
        logger.error("Error creating servicio_externo: %s", exc, exc_info=True)
        raise


async def actualizar_servicio_externo(codigo: str, data: dict) -> bool:
    updates = []
    params = []

    for key, value in data.items():
        if key not in ALLOWED_FIELDS or key == "codigo":
            continue
        if key in ("header", "body"):
            value = _dump_json(value)
        if key == "metodo" and value is not None:
            value = str(value).upper()
        updates.append(f"{key} = %s")
        params.append(value)

    if not updates:
        return False

    query = f"""
        UPDATE servicios_externos
        SET {", ".join(updates)}
        WHERE codigo = %s
    """
    params.append(codigo)

    try:
        connection = await aiomysql.connect(
            host=db_config["host"],
            user=db_config["user"],
            password=db_config["password"],
            db=db_config["database"],
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                await connection.commit()
                return cursor.rowcount > 0
        finally:
            connection.close()
    except aiomysql.Error as exc:
        logger.error("Error updating servicio_externo: %s", exc, exc_info=True)
        raise


async def obtener_servicio_externo_por_codigo(codigo: str) -> Optional[dict[str, Any]]:
    query = """
        SELECT
            id,
            nombre_servicio,
            codigo,
            url,
            metodo,
            timeout_ms,
            reintentos,
            activo,
            header,
            body,
            creado_en,
            actualizado_en
        FROM servicios_externos
        WHERE codigo = %s
        LIMIT 1
    """

    try:
        connection = await aiomysql.connect(
            host=db_config["host"],
            user=db_config["user"],
            password=db_config["password"],
            db=db_config["database"],
        )
        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, (codigo,))
                result = await cursor.fetchone()
                if not result:
                    return None
                result["header"] = _load_json(result.get("header"))
                result["body"] = _load_json(result.get("body"))
                return result
        finally:
            connection.close()
    except aiomysql.Error as exc:
        logger.error("Error fetching servicio_externo: %s", exc, exc_info=True)
        raise


async def listar_servicios_externos(
    activo: Optional[bool] = None,
    limite: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            id,
            nombre_servicio,
            codigo,
            url,
            metodo,
            timeout_ms,
            reintentos,
            activo,
            header,
            body,
            creado_en,
            actualizado_en
        FROM servicios_externos
    """
    params = []

    if activo is not None:
        query += " WHERE activo = %s"
        params.append(1 if activo else 0)

    query += " ORDER BY actualizado_en DESC LIMIT %s OFFSET %s"
    params.extend([limite, offset])

    try:
        connection = await aiomysql.connect(
            host=db_config["host"],
            user=db_config["user"],
            password=db_config["password"],
            db=db_config["database"],
        )
        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()
                for row in rows:
                    row["header"] = _load_json(row.get("header"))
                    row["body"] = _load_json(row.get("body"))
                return rows or []
        finally:
            connection.close()
    except aiomysql.Error as exc:
        logger.error("Error listing servicios_externos: %s", exc, exc_info=True)
        raise
