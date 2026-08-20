from datetime import datetime
from typing import Optional


def normalizar_fecha_payvalida(fecha: Optional[str]) -> Optional[str]:
    """
    Normaliza fechas aceptadas por entrada humana al formato DD/MM/YYYY.

    Payvalida es sensible al formato; por eso 1/06/2026 se envia como
    01/06/2026.
    """
    if not fecha:
        return None

    texto = str(fecha).strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).strftime("%d/%m/%Y")
        except ValueError:
            continue

    return texto


def convertir_fecha_payvalida_a_mysql(fecha: Optional[str]) -> Optional[str]:
    """
    Convierte una fecha en formato DD/MM/YYYY a YYYY-MM-DD para MySQL.

    Si la fecha no llega o no cumple el formato esperado, retorna None.
    """
    if not fecha:
        return None

    fecha_normalizada = normalizar_fecha_payvalida(fecha)

    try:
        return datetime.strptime(fecha_normalizada, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def obtener_fecha_actual_compacta() -> str:
    """
    Retorna la fecha actual en formato YYYYMMDD.
    Útil para construir códigos internos sin caracteres especiales.
    """
    return datetime.utcnow().strftime("%Y%m%d")
