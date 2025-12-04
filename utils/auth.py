import logging
import time
import httpx
from utils.config import settings, TOKEN_DATA

logger = logging.getLogger(__name__)


async def obtener_token(client: httpx.AsyncClient):
    """
    Obtiene o refresca el token contra la URL de auth usando las credenciales
    definidas en settings. Centraliza el estado en TOKEN_DATA para compartir
    caché entre servicios.
    """
    # Regresa token vigente si no está próximo a vencer (margen de 30s)
    if TOKEN_DATA.get("access_token") and TOKEN_DATA.get("expires_at", 0) > int(time.time()) + 30:
        return TOKEN_DATA["access_token"]

    payload = settings.AUTH_PAYLOAD_PROD or {}
    auth_url = settings.AUTH_URL

    try:
        resp = await client.post(auth_url, json=payload, timeout=10.0)
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
