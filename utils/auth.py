import logging
import time
from typing import Optional

import httpx

from utils.config import settings, TOKEN_DATA
from utils.external_client import ExternalClient

logger = logging.getLogger(__name__)


def _store_token(data: dict) -> str:
    # Normaliza la respuesta de auth y persiste el token en cache local.
    access = data.get("access_token") or data.get("accessToken") or data.get("token")
    expires_in = data.get("expires_in") or data.get("expires") or 3600
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = 3600
    if expires_in <= 0:
        expires_in = 3600
    if access:
        access = str(access).strip()
    if access:
        TOKEN_DATA["access_token"] = access
        TOKEN_DATA["expires_at"] = int(time.time()) + expires_in
        TOKEN_DATA["refresh_token"] = data.get("refresh_token")
        logger.info("Token obtenido y almacenado en TOKEN_DATA")
        return TOKEN_DATA["access_token"]
    raise Exception("Token no encontrado en respuesta de auth")


async def _fallback_auth(
    auth_url: str,
    payload: dict,
    client: Optional[httpx.AsyncClient],
) -> str:
    # Ruta alterna si no existe el servicio en BD: usa settings y cliente httpx directo.
    if client is None:
        async with httpx.AsyncClient() as local_client:
            resp = await local_client.post(auth_url, json=payload, timeout=10.0)
    else:
        resp = await client.post(auth_url, json=payload, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        data = {}
    return _store_token(data)


async def obtener_token(client: Optional[httpx.AsyncClient] = None) -> str:
    """
    Obtiene o refresca el token contra la URL de auth usando las credenciales
    definidas en settings. Centraliza el estado en TOKEN_DATA para compartir
    cache entre servicios.
    """
    if TOKEN_DATA.get("access_token") and TOKEN_DATA.get("expires_at", 0) > int(time.time()) + 30:
        return TOKEN_DATA["access_token"]

    payload = settings.AUTH_PAYLOAD_PROD
    auth_url = settings.AUTH_URL

    try:
        # Obtiene la configuracion de auth desde la tabla de servicios externos.
        logger.info("Obteniendo configuracion de servicio externo AUTH_TOKEN")
        
        ext_client = await ExternalClient.from_code("AUTH_TOKEN")
        
        logger.info(f"Usando servicio externo AUTH_TOKEN para obtener token")
        # Inyecta la URL de auth si no esta definida en el servicio externo.
        if not ext_client.url:
            if not auth_url:
                raise Exception("AUTH_URL no configurada")
            ext_client.set_url(auth_url)
                
        # Inyecta el payload de auth si no esta definido en el servicio externo.
        if not ext_client.body:
            ext_client.set_body(payload)
        
        # Ejecuta la peticion via cliente generico y valida la respuesta.
        response = await ext_client.run()
        
        logger.info(f"Respuesta de auth obtenida: {response}")
        if not isinstance(response, dict):
            raise Exception("Respuesta invalida del servicio de auth")
        
        status = response.get("status")
        
        if not isinstance(status, int):
            raise Exception("Respuesta de auth sin status valido")
        if status >= 400:
            raise Exception(f"Auth error: {status}")
        data = response.get("data")
        if not isinstance(data, dict):
            raise Exception("Respuesta de auth no es JSON valido")
        return _store_token(data)
    except ValueError:
        if not auth_url:
            raise
        logger.warning("AUTH_TOKEN no configurado en BD, usando AUTH_URL de settings")
        return await _fallback_auth(auth_url, payload, client)
    except httpx.HTTPError as e:
        logger.error(f"Error al obtener token: {e}")
        raise
