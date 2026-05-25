import json
from decimal import Decimal
from datetime import date, datetime
from typing import Any


def convertir_a_json(valor: Any) -> Any:
    """
    Convierte diccionarios/listas a JSON string para columnas JSON de MySQL.
    Si ya viene None, retorna None.
    """
    if valor is None:
        return None

    return json.dumps(valor, ensure_ascii=False, default=serializar_valor)


def serializar_valor(valor: Any) -> Any:
    """
    Serializa valores no compatibles directamente con JSON.
    """
    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    return str(valor)
