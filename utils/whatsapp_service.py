
import os
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v20.0",
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str,
        body_params: List[str],
    ) -> Dict[str, Any]:
        """
        Envía una plantilla de WhatsApp usando Cloud API.

        - to: número en formato internacional, sin '+', ej: '573006803158'
        - template_name: nombre interno EXACTO de la plantilla en Meta
          (ej: 'post_llamada_renovacion')
        - language_code: código de idioma (para Spanish suele ser 'es')
        - body_params: lista de textos para {{1}}, {{2}}, ...
        """

        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": value}
                    for value in body_params
                ],
            }
        ]

        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components,
            },
        }

        logger.info(f"[WhatsApp] Enviando plantilla a {to}: {payload}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.base_url,
                headers=self.headers,
                json=payload,
            )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}

        if response.status_code >= 400:
            logger.error(
                "[WhatsApp] Error %s al enviar mensaje: %s",
                response.status_code,
                data,
            )
            return {
                "status": "error",
                "http_code": response.status_code,
                "message": data,
            }

        logger.info(f"[WhatsApp] Mensaje enviado OK: {data}")
        return {
            "status": "success",
            "http_code": response.status_code,
            "data": data,
        }


# Instancia global
whatsapp_client = WhatsAppClient(
    access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", "CAMBIA_ESTO"),
    phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "CAMBIA_ESTO"),
    api_version=os.getenv("WHATSAPP_API_VERSION", "v20.0"),
)


async def enviar_whatsapp_renovacion(
    telefono: str,
    nombre_cliente: str,
    link_redirect: str,
) -> Dict[str, Any]:
    """
    Usa la plantilla:

    Hola {{1}}, gracias por tu tiempo en la llamada.
    Tenemos información importante sobre tu renovación de crédito.
    Puedes seguir con tu proceso aquí: {{2}}, ó escribe a este mismo numero
    para hacerlo por aquí.

    - {{1}} = nombre_cliente
    - {{2}} = link_redirect
    """

    TEMPLATE_NAME = "post_llamada_renovacion"
    LANGUAGE_CODE = "es"  # Spanish

    body_params = [
        nombre_cliente,
        link_redirect,
    ]

    return await whatsapp_client.send_template(
        to=telefono,
        template_name=TEMPLATE_NAME,
        language_code=LANGUAGE_CODE,
        body_params=body_params,
    )
