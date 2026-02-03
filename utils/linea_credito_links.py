# app/services/linea_credito_links.py

from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

# Mapea la linea de crédito EXACTA que viene en el payload al link correspondiente.
LINEA_CREDITO_LINKS: Dict[str, str] = {
    "Impulsa USB - Cartagena": "https://one2credit-app.kuenta.co/o/VAOKHSSSG",
    "Impulsa RAFAEL NUÑEZ": "https://one2credit-app.kuenta.co/o/02AXDRYIG",

    # ⬇⬇⬇ Aquí vas agregando las demás líneas manualmente
    # "Otra LINEA_CREDITO": "https://one2credit-app.kuenta.co/o/XXXXXXX",
}


def obtener_link_por_linea_credito(linea_credito: Optional[str]) -> Optional[str]:
    """
    Devuelve el link correspondiente a la linea de crédito.
    Si no se encuentra, devuelve None y loggea un warning.
    """
    if not linea_credito:
        logger.warning("LINEA_CREDITO vacío o None, no se puede mapear link.")
        return None

    link = LINEA_CREDITO_LINKS.get(linea_credito)

    if not link:
        logger.warning(
            "No se encontró link para LINEA_CREDITO='%s'. "
            "Revisa el diccionario LINEA_CREDITO_LINKS.",
            linea_credito,
        )

    return link
