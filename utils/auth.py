from utils.config import AUTH_PAYLOAD_PROD, AUTH_URL, TOKEN_DATA
import time
import httpx
import logging
logger = logging.getLogger(__name__)

async def obtener_token(client: httpx.AsyncClient):
    """
    Obtiene o refresca el token usando AUTH_PAYLOAD_PROD y AUTH_URL.
    Guarda el token y su expiración en config.TOKEN_DATA.
    """
    # Si ya existe token válido, devolverlo
    if TOKEN_DATA.get("access_token") and TOKEN_DATA.get("expires_at", 0) > int(time.time()) + 30:
        return TOKEN_DATA["access_token"]

    payload = AUTH_PAYLOAD_PROD or {}
    try:
        resp = await client.post(AUTH_URL, json=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        access = data.get("access_token") or data.get("accessToken") or data.get("token")
        expires_in = data.get("expires_in") or data.get("expires") or 3600
        if access:
            TOKEN_DATA["access_token"] = access
            TOKEN_DATA["expires_at"] = int(time.time()) + int(expires_in)
            TOKEN_DATA["refresh_token"] = data.get("refresh_token")
            logger.info("Token obtenido y almacenado en TOKEN_DATA")
            return TOKEN_DATA["access_token"]
        raise Exception("Token no encontrado en respuesta de auth")
    except httpx.HTTPError as e:
        logger.error(f"Error al obtener token: {e}")
        raise