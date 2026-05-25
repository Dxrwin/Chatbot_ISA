from datetime import datetime
from typing import Optional


def convertir_fecha_payvalida_a_mysql(fecha: Optional[str]) -> Optional[str]:
    """
    Convierte una fecha en formato DD/MM/YYYY a YYYY-MM-DD para MySQL.

    Si la fecha no llega o no cumple el formato esperado, retorna None.
    """
    if not fecha:
        return None

    try:
        return datetime.strptime(fecha, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def obtener_fecha_actual_compacta() -> str:
    """
    Retorna la fecha actual en formato YYYYMMDD.
    Útil para construir códigos internos sin caracteres especiales.
    """
    return datetime.utcnow().strftime("%Y%m%d")
