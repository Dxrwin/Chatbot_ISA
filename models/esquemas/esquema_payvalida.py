from typing import Any, Dict, Optional
from pydantic import BaseModel


class RespuestaCreacionPayvalida(BaseModel):
    """
    Respuesta general de Payválida al crear una orden.
    """

    CODE: str
    DESC: Optional[str] = None
    DATA: Dict[str, Any] = {}


class RespuestaConsultaPayvalida(BaseModel):
    """
    Respuesta general de Payválida al consultar una orden.
    """

    CODE: Optional[str] = None
    DESC: Optional[str] = None
    DATA: Dict[str, Any] = {}
