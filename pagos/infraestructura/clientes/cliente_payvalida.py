import time
from typing import Any, Dict, Tuple

import httpx

from pagos.configuracion import obtener_configuracion_payvalida
from pagos.excepciones import ErrorProveedorPago


class ClientePayvalida:
    """
    Cliente HTTP para comunicarse con Payválida.
    """

    def __init__(self):
        self.configuracion = obtener_configuracion_payvalida()

    def _construir_timeout(self) -> httpx.Timeout:
        """
        Define timeouts explícitos para conexión, escritura, lectura y pool.
        """
        timeout = self.configuracion.timeout_segundos
        return httpx.Timeout(timeout=timeout, connect=min(10, timeout))

    def _extraer_json_seguro(self, respuesta: httpx.Response) -> Dict[str, Any]:
        """
        Retorna JSON si la respuesta lo permite; si no, conserva texto acotado.
        """
        try:
            datos = respuesta.json()
        except ValueError:
            return {
                "error": "RESPUESTA_NO_JSON",
                "status_code": respuesta.status_code,
                "content_type": respuesta.headers.get("content-type"),
                "texto": respuesta.text[:1000],
            }

        if isinstance(datos, dict):
            return datos

        return {"DATA": datos}

    def _construir_error_http(self, operacion: str, error: Exception) -> ErrorProveedorPago:
        """
        Normaliza errores de transporte para que el servicio los audite igual.
        """
        return ErrorProveedorPago(f"Error de conexión con Payválida al {operacion}: {error}")

    async def crear_orden(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
        """
        Envía a Payválida la solicitud para crear una orden de pago.

        Retorna:
            respuesta_json, codigo_http, duracion_ms
        """
        inicio = time.time()
        url = f"{self.configuracion.url_base}/api/v3/porders"

        try:
            async with httpx.AsyncClient(timeout=self._construir_timeout()) as cliente:
                respuesta = await cliente.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TimeoutException as error:
            raise self._construir_error_http("crear orden", error) from error
        except httpx.RequestError as error:
            raise self._construir_error_http("crear orden", error) from error

        duracion_ms = int((time.time() - inicio) * 1000)
        return self._extraer_json_seguro(respuesta), respuesta.status_code, duracion_ms

    async def consultar_orden(
        self,
        codigo_orden_interno: str,
        merchant: str,
        checksum: str,
    ) -> Tuple[Dict[str, Any], int, int]:
        """
        Consulta en Payválida el estado de una orden.
        """
        inicio = time.time()
        url = f"{self.configuracion.url_base}/api/v3/porders/{codigo_orden_interno}"

        try:
            async with httpx.AsyncClient(timeout=self._construir_timeout()) as cliente:
                respuesta = await cliente.get(
                    url,
                    params={
                        "merchant": merchant,
                        "checksum": checksum,
                    },
                )
        except httpx.TimeoutException as error:
            raise self._construir_error_http("consultar orden", error) from error
        except httpx.RequestError as error:
            raise self._construir_error_http("consultar orden", error) from error

        duracion_ms = int((time.time() - inicio) * 1000)
        return self._extraer_json_seguro(respuesta), respuesta.status_code, duracion_ms
