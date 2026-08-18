import json
import logging
import traceback
import re
from typing import Optional, Any, Dict
from utils.database import consultar_servicio_externo  # Importar función de BD
from utils.notify_error import error_notify, get_cached_logs,send_log_email, send_log_telegram,info_notify
import httpx
from utils.database import insertar_log
from utils.servicios_externos_service import obtener_servicio_externo_por_codigo

logger = logging.getLogger(__name__)


def redactar_headers_sensibles(headers: Dict[str, Any]) -> Dict[str, Any]:
    """Crea una copia segura para logs sin exponer credenciales."""
    return {
        key: "[REDACTED]" if str(key).lower() == "authorization" else value
        for key, value in (headers or {}).items()
    }


class ExternalClient:
    """
    Cliente para ejecutar peticiones HTTP a servicios externos configurados en BD.
    Soporta reemplazo dinámico de variables en URL, headers y body.
    """

    # Patrón compilado para placeholders {variable}
    VAR_PATTERN = re.compile(r'\{([^}]+)\}')

    def __init__(
        self,
        nombre_servicio: str,
        codigo: str,
        url: str,
        metodo: str,
        timeout_ms: int = 10000,
        reintentos: int = 0,
        header: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ):
        self.nombre_servicio = nombre_servicio
        self.codigo = codigo
        self.url = url
        self.metodo = metodo.upper()
        self.timeout_ms = timeout_ms
        self.timeout_s = timeout_ms / 1000
        self.reintentos = reintentos
        self.header = header or {}
        self.body = body or {}
        self.dynamic_values = {}  # Variables dinámicas a reemplazar
        self.client_id = None  # Para logging

    @classmethod
    async def from_code(cls, codigo: str, client_id: Optional[str] = None) -> "ExternalClient":
        """
        Carga un servicio externo desde la BD usando su código.

        Args:
            codigo: Código del servicio (ej: "KUENTA_PAYABLE_CREATE")
            client_id: ID del cliente para logging

        Returns:
            Instancia de ExternalClient

        Raises:
            ValueError: Si el servicio no existe en la BD
        """

        servicio = await consultar_servicio_externo(codigo)
        
        logger.info(f"Servicio externo obtenido de BD para código '{codigo}': {servicio} \n")
        if not servicio:
            raise ValueError(f"Servicio externo '{codigo}' no encontrado en BD")

        # Crear instancia con datos de BD
        instance = cls(
            nombre_servicio=servicio.get("nombre_servicio"),
            codigo=servicio.get("codigo"),
            url=servicio.get("url"),
            metodo=servicio.get("metodo"),
            timeout_ms=servicio.get("timeout_ms", 10000),
            reintentos=servicio.get("reintentos", 0),
            header=servicio.get("header", {}),
            body=servicio.get("body", {}),
        )
        instance.client_id = client_id
        return instance

    def set_url(self, url: str) -> None:
        """Establece la URL (puede contener placeholders)"""
        self.url = url

    def set_path(self, path: str) -> None:
        """Agrega un path a la URL existente"""
        self.url = self.url.rstrip("/") + path

    def set_headers(self, headers: Dict[str, str]) -> None:
        """
        Establece headers adicionales.
        Se mergean con los headers de BD.
        """
        self.header.update(headers or {})

    def set_body(self, body: Dict[str, Any]) -> None:
        """Establece el body (puede contener placeholders)"""
        self.body = body or {}

    def set_dynamic_values(self, values: Dict[str, Any]) -> None:
        """
        Establece variables dinámicas para reemplazos.

        Args:
            values: Dict con pares clave-valor
                Ej: {"ORG_ID": "123", "access_token": "abc123"}
        """
        self.dynamic_values.update(values or {})

    def _replace_variables(self, text: str) -> str:
        """
        Reemplaza placeholders en texto.
        Busca patrones {variable} y los reemplaza con valores dinámicos.
        Siempre devuelve string.
        """
        if not isinstance(text, str):
            return text

        def replacer(match):
            var_name = match.group(1)
            # Primero buscar en valores dinámicos
            if var_name in self.dynamic_values:
                # En textos mezclados siempre devolvemos string
                return str(self.dynamic_values[var_name])
            # Si no existe, dejar el placeholder como está
            return match.group(0)

        return self.VAR_PATTERN.sub(replacer, text)

    def _resolve_value(self, value: Any) -> Any:
        """
        Resuelve un valor del body/headers conservando el tipo real
        cuando el valor es exactamente un placeholder: "{var}".
        """
        # Caso 1: string
        if isinstance(value, str):
            # ¿Es SOLO un placeholder? ej: "{meta}"
            full_match = self.VAR_PATTERN.fullmatch(value)
            if full_match:
                var_name = full_match.group(1)
                if var_name in self.dynamic_values:
                    # Devolvemos el valor como es (dict, None, int, etc)
                    return self.dynamic_values[var_name]
                # Si no está definido en dynamic_values, devolvemos el string tal cual
                return value

            # Si es un string con texto + placeholders mezclados → usamos reemplazo normal
            return self._replace_variables(value)

        # Caso 2: dict
        if isinstance(value, dict):
            return self._process_dict(value)

        # Caso 3: lista
        if isinstance(value, list):
            return [self._resolve_value(item) for item in value]

        # Otros tipos (int, float, bool, None)
        return value

    def _process_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """
        Procesa recursivamente un diccionario reemplazando variables,
        conservando el tipo real cuando corresponde.
        """
        if not isinstance(d, dict):
            return d

        result = {}
        for key, value in d.items():
            result[key] = self._resolve_value(value)
        return result

    async def run(self) -> Dict[str, Any]:
        """
        Ejecuta la petición HTTP al servicio externo.

        Returns:
            Dict con estructura: {"status": 200, "data": {...}}
        """
        final_body = {}
        try:
            # Reemplazar variables en URL
            final_url = self._replace_variables(self.url)

            # Reemplazar variables en headers
            final_headers = self._process_dict(self.header)

            # Reemplazar variables en body
            final_body = self._process_dict(self.body) if self.body else {}

            # Logs de depuración
            logger.info(f"[{self.codigo}] URL final: {final_url} \n")
            logger.info(
                f"[{self.codigo}] Headers: {redactar_headers_sensibles(final_headers)}\n"
            )
            logger.info(f"[{self.codigo}] Body final: {final_body}\n")
            
            await info_notify(
                method_name=f"external_client:{self.codigo}",
                client_id=self.client_id or "N/A",
                info_message=f"Ejecutando petición HTTP {self.metodo} al servicio externo '{self.nombre_servicio}'url final: {final_url} \n"
            )

            # Crear cliente HTTP
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:

                if self.metodo == "GET":
                    response = await client.get(final_url, headers=final_headers, params=final_body)
                elif self.metodo == "POST":
                    response = await client.post(final_url, headers=final_headers, json=final_body)
                elif self.metodo == "PATCH":
                    response = await client.patch(final_url, headers=final_headers, json=final_body)
                elif self.metodo == "PUT":
                    response = await client.put(final_url, headers=final_headers, json=final_body)
                elif self.metodo == "DELETE":
                    response = await client.delete(final_url, headers=final_headers, json=final_body)
                else:
                    return {
                        "status": 400,
                        "data": {"error": f"Método HTTP no soportado: {self.metodo}"}
                    }

                # Parsear respuesta
                try:
                    data = response.json()
                    #logger.info(f" respuesta de la peticion para obtener el servicio: [{self.codigo}] Respuesta JSON: {data} \n")
                    
                    status_code = response.status_code
                    logger.info(f" respuesta de la peticion para obtener el servicio: [{self.codigo}] Status code: {status_code} \n")
                    
                    await info_notify(
                        method_name=f"external_client:{self.codigo}",
                        client_id=self.client_id or "N/A",
                        info_message=f"Respuesta JSON recibida del servicio externo '{self.nombre_servicio}' para código '{self.codigo}', respuesta: {data} \n"
                    )
                except Exception:
                    data = {"raw_text": response.text}

                # Log de error si no es exitoso
                if response.status_code >= 400:
                    await self._log_error(
                        status=response.status_code,
                        error_message=f"Respuesta no exitosa: {response.status_code}",
                        response_text=response.text,
                        payload_enviado=final_body,
                    )

                return {
                    "status": response.status_code,
                    "data": data
                }

        except httpx.TimeoutException:
            await self._log_error(
                status=504,
                error_message="Timeout en solicitud HTTP",
                response_text="Timeout",
                payload_enviado=final_body,
            )
            return {
                "status": 504,
                "data": {"error": "Timeout en solicitud HTTP"}
            }

        except httpx.RequestError as e:
            await self._log_error(
                status=502,
                error_message=f"Error de conexión: {str(e)}",
                response_text=str(e),
                payload_enviado=final_body,
            )
            return {
                "status": 502,
                "data": {"error": f"Error de conexión: {str(e)}"}
            }

        except Exception as e:
            await self._log_error(
                status=500,
                error_message=f"Error interno: {str(e)}",
                response_text=str(e),
                payload_enviado=final_body,
            )
            return {
                "status": 500,
                "data": {"error": f"Error interno: {str(e)}"}
            }

    async def _log_error(
        self,
        status: int,
        error_message: str,
        response_text: str,
        payload_enviado: Optional[Dict[str, Any]] = None,
    ):
        """Registra errores en la BD"""
        try:
            await insertar_log(
                method_name=f"external_client:{self.codigo}",
                client_id=self.client_id or "N/A",
                error_message=error_message,
                http_code=status,
                tipo="error",
                nombre_archivo="utils/external_client.py",
                traceback_str=traceback.format_exc(),
                respuesta_api=response_text,
                payload_enviado=(
                    json.dumps(payload_enviado, default=str)
                    if payload_enviado is not None
                    else None
                ),
            )
        except Exception:
            logger.error("Failed to persist log for external_client", exc_info=True)

    def __dict__(self):
        """Para debugging"""
        return {
            "nombre_servicio": self.nombre_servicio,
            "codigo": self.codigo,
            "url": self.url,
            "metodo": self.metodo,
            "timeout_ms": self.timeout_ms,
            "reintentos": self.reintentos,
            "header": self.header,
            "body": self.body,
            "dynamic_values": self.dynamic_values
        }
