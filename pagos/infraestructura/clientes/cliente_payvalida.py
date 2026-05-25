import time
from typing import Any, Dict, Tuple

import httpx

from pagos.configuracion import obtener_configuracion_payvalida


class ClientePayvalida:
    """
    Cliente HTTP para comunicarse con Payválida.
    """

    def __init__(self):
        self.configuracion = obtener_configuracion_payvalida()

    async def crear_orden(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
        """
        Envía a Payválida la solicitud para crear una orden de pago.

        Retorna:
            respuesta_json, codigo_http, duracion_ms
        """
        inicio = time.time()
        url = f"{self.configuracion.url_base}/api/v3/porders"

        async with httpx.AsyncClient(timeout=self.configuracion.timeout_segundos) as cliente:
            respuesta = await cliente.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        duracion_ms = int((time.time() - inicio) * 1000)
        return respuesta.json(), respuesta.status_code, duracion_ms

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

        async with httpx.AsyncClient(timeout=self.configuracion.timeout_segundos) as cliente:
            respuesta = await cliente.get(
                url,
                params={
                    "merchant": merchant,
                    "checksum": checksum,
                },
            )

        duracion_ms = int((time.time() - inicio) * 1000)
        return respuesta.json(), respuesta.status_code, duracion_ms
