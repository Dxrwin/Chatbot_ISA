import asyncio
from typing import Any, Dict
import httpx
from fastapi import HTTPException, logger
from utils.auth import obtener_token
from utils.notify_error import error_notify
from utils.config import ORG_ID, API_URL


# --- Sistema de Caché en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

async def get_product_lines(parent_id: str):
    """
    Webhook asíncrono:
    1. Solicita token de autorización al AUTH_URL
    2. Usa el token para consultar API_URL
    3. Retorna solo id, parentId y name
    """
    
    MAX_RETRIES = 3  # número máximo de intentos
    RETRY_DELAY = 5  # segundos entre intentos (base)
    TIMEOUT = 15     # timeout en segundos por solicitud
    
    method_name = "product-lines"
    parent_id_notify_error = f"parent_id para la busqueda del la linea={parent_id}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            #Obtener token (una sola vez)
            access_token = await obtener_token(client)
            if not access_token:
                msg = "No se pudo obtener el token de acceso"
                await error_notify(method_name, parent_id_notify_error, msg)
                raise HTTPException(status_code=401, detail=msg)

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": f"{access_token}"
            }

            #Intentar la consulta a la API externa con reintentos
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = await client.get(API_URL, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    lines = data.get("data", {}).get("lines", [])

                    # Buscar la línea
                    for line in lines:
                        if line.get("parentId") == parent_id:
                            logger.info(f"Línea encontrada: {line}")
                            return {
                                "id": line.get("id"),
                                "name": line.get("name"),
                                "parentId": line.get("parentId"),
                                "principalMin": line.get("principalMin"),
                                "principalMax": line.get("principalMax"),
                                "timeMin": line.get("timeMin"),
                                "timeMax": line.get("timeMax"),
                            }

                    # Si no hay coincidencia
                    msg = f"No se encontró la línea con parentId: {parent_id}"
                    await error_notify(method_name, parent_id_notify_error, msg)
                    raise HTTPException(status_code=404, detail=msg)

                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                    logger.warning(f"Intento {attempt}/{MAX_RETRIES} fallido por timeout o conexión: {e}")
                    if attempt == MAX_RETRIES:
                        raise
                    await asyncio.sleep(RETRY_DELAY * attempt)  # reintento exponencial

                except httpx.HTTPStatusError as e:
                    # Si la API devuelve error, registrar y romper
                    logger.error(f"Error HTTP {e.response.status_code} en API externa: {e.response.text}")
                    await error_notify(method_name, parent_id_notify_error, f"Error en API externa: {e.response.text}")
                    if 500 <= e.response.status_code < 600 and attempt < MAX_RETRIES:
                        # Reintentar en errores 5xx
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    raise

            # Si todos los intentos fallan
            msg = "Error persistente al consultar API externa"
            await error_notify(method_name, parent_id_notify_error, msg)
            raise HTTPException(status_code=502, detail="Error persistente al consultar API externa")

    except Exception as e:
        logger.error(f"Error general en webhook_product_lines: {e}")
        await error_notify(method_name, parent_id_notify_error, f"Error general: {e}")
        raise HTTPException(status_code=500, detail=f"Error general: {e}")