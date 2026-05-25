from typing import Any, Dict, Optional
from fastapi import Request


def obtener_ip_origen(request: Request) -> Optional[str]:
    """
    Obtiene la IP directa detectada por FastAPI.

    Si la API está detrás de proxy, balanceador o gateway,
    esta IP puede ser la del proxy.
    """
    if request.client:
        return request.client.host

    return None


def obtener_ip_cliente_resuelta(request: Request) -> Optional[str]:
    """
    Intenta resolver la IP real del cliente.

    Orden:
    1. X-Forwarded-For
    2. X-Real-IP
    3. request.client.host

    Solo se debe confiar completamente en estas cabeceras si el proxy
    o balanceador es controlado por la organización.
    """
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        primera_ip = x_forwarded_for.split(",")[0].strip()
        if primera_ip:
            return primera_ip

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()

    return obtener_ip_origen(request)


def construir_contexto_auditoria_http(
    request: Request,
    operacion: str,
    payload_recibido: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Construye un diccionario con información útil para auditoría HTTP.
    """
    return {
        "metodo_http": request.method,
        "ruta": str(request.url.path),
        "operacion": operacion,
        "ip_origen": obtener_ip_origen(request),
        "ip_cliente_resuelta": obtener_ip_cliente_resuelta(request),
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
        "forwarded": request.headers.get("forwarded"),
        "user_agent": request.headers.get("user-agent"),
        "payload_recibido": payload_recibido,
    }
