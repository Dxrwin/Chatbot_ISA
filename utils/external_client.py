import json
import logging
import traceback
from typing import Optional, Any

import httpx

from utils.database import insertar_log
from utils.servicios_externos_service import obtener_servicio_externo_por_codigo

logger = logging.getLogger(__name__)


class ExternalClient:
    """
    Cliente generico para ejecutar servicios externos.
    """

    def __init__(self, config: dict[str, Any], client_id: Optional[str] = None):
        self.client_id = client_id
        self.codigo = config.get("codigo")
        self.nombre_servicio = config.get("nombre_servicio")
        self.url = config.get("url")
        self.metodo = str(config.get("metodo", "")).upper()
        self.timeout_ms = int(config.get("timeout_ms", 10000))
        self.reintentos = int(config.get("reintentos", 0))
        self.activo = int(config.get("activo", 1)) == 1
        self.headers = config.get("header") or {}
        self.body = config.get("body") or {}
        self.path = ""

    @classmethod
    async def from_code(cls, codigo: str, client_id: Optional[str] = None) -> "ExternalClient":
        
        logger.info(f"Creando ExternalClient para el codigo de servicio: {codigo}")
        config = await obtener_servicio_externo_por_codigo(codigo)
        logger.info(f"Configuracion obtenida para {codigo}: {config}")
        if not config:
            raise ValueError("Service not found")
        return cls(config, client_id=client_id)

    def set_path(self, path: str):
        self.path = path or ""

    def set_headers(self, headers: dict):
        self.headers = headers or {}

    def set_body(self, body: dict):
        self.body = body or {}

    def set_method(self, method: str):
        self.metodo = str(method or "").upper()

    def set_url(self, url: str):
        self.url = url or ""

    async def run(self) -> dict[str, Any]:
        if not self.activo:
            return {"status": 409, "data": {"error": "Service inactive"}}

        url = f"{self.url}{self.path}"
        timeout_sec = max(self.timeout_ms / 1000.0, 1)
        timeout = httpx.Timeout(timeout_sec)
        response_payload: dict[str, Any] = {}
        last_exception: Optional[Exception] = None
        
        logger.info(f"Ejecutando servicio externo {self.codigo} en {url} con metodo {self.metodo}")

        for attempt in range(self.reintentos + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if self.metodo == "GET":
                        response = await client.get(url, headers=self.headers, params=self.body)
                    elif self.metodo == "POST":
                        response = await client.post(url, headers=self.headers, json=self.body)
                    elif self.metodo == "PUT":
                        response = await client.put(url, headers=self.headers, json=self.body)
                    elif self.metodo == "PATCH":
                        response = await client.patch(url, headers=self.headers, json=self.body)
                    elif self.metodo == "DELETE":
                        response = await client.delete(url, headers=self.headers, json=self.body)
                    else:
                        return {"status": 400, "data": {"error": "Unsupported method"}}

                response_payload = self._parse_response(response.text)
                if response.status_code >= 400:
                    await self._log_error(
                        status=response.status_code,
                        error_message="Non-success response",
                        response_text=response.text,
                    )
                return {"status": response.status_code, "data": response_payload}
            except httpx.TimeoutException as exc:
                last_exception = exc
                logger.warning("Timeout calling %s (attempt %s)", url, attempt + 1)
                await self._log_error(
                    status=504,
                    error_message="Timeout while calling external service",
                    response_text=str(exc),
                )
            except httpx.RequestError as exc:
                last_exception = exc
                logger.error("Request error calling %s", url, exc_info=True)
                await self._log_error(
                    status=503,
                    error_message="Request error while calling external service",
                    response_text=str(exc),
                )
            except Exception as exc:
                last_exception = exc
                logger.error("Unexpected error calling %s", url, exc_info=True)
                await self._log_error(
                    status=500,
                    error_message="Unexpected error while calling external service",
                    response_text=str(exc),
                )

        if last_exception:
            response_payload = {"error": str(last_exception)}
        return {"status": 503, "data": response_payload}

    def _parse_response(self, text: str) -> dict[str, Any]:
        content = (text or "").strip()
        if not content:
            return {}
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            return {"data": data}
        except json.JSONDecodeError:
            return {"message": content}

    async def _log_error(self, status: int, error_message: str, response_text: str):
        try:
            await insertar_log(
                method_name=f"external_client:{self.codigo}",
                client_id=self.client_id,
                error_message=error_message,
                http_code=status,
                tipo="error",
                nombre_archivo="utils/external_client.py",
                traceback_str=traceback.format_exc(),
                respuesta_api=response_text,
                payload_enviado=json.dumps(self.body) if self.body else None,
            )
        except Exception:
            logger.error("Failed to persist log for external_client", exc_info=True)
