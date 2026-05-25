from datetime import date, datetime
from decimal import Decimal
from typing import Any
import json


def serializar_valor_bd(valor: Any) -> Any:
    """
    Convierte valores de MySQL a tipos compatibles con JSON.
    """
    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    return valor


def intentar_parsear_json(valor: Any) -> Any:
    """
    Intenta convertir columnas JSON devueltas como texto a dict/list.
    """
    if not isinstance(valor, str):
        return valor

    texto = valor.strip()

    if not texto:
        return valor

    if not (
        (texto.startswith("{") and texto.endswith("}"))
        or (texto.startswith("[") and texto.endswith("]"))
    ):
        return valor

    try:
        return json.loads(texto)
    except Exception:
        return valor


def normalizar_registro_bd(registro: dict | None) -> dict | None:
    """
    Normaliza un registro para retornarlo por API.
    """
    if registro is None:
        return None

    normalizado = {}

    for clave, valor in registro.items():
        valor = intentar_parsear_json(valor)
        normalizado[clave] = serializar_valor_bd(valor)

    return normalizado


def normalizar_lista_bd(registros: list[dict]) -> list[dict]:
    """
    Normaliza una lista de registros.
    """
    return [normalizar_registro_bd(registro) for registro in registros]