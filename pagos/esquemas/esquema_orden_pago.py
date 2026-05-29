import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator


def normalizar_monto_decimal(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return valor

    if valor is None:
        return valor

    texto = str(valor).strip()
    if not texto:
        return valor

    texto = re.sub(r"[^\d,.\-]", "", texto)
    if not texto:
        return valor

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes[-1]) <= 2 else texto.replace(",", "")
    elif "." in texto:
        partes = texto.split(".")
        if len(partes) > 2:
            texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) <= 2 else "".join(partes)
        elif len(partes[-1]) > 2:
            texto = texto.replace(".", "")

    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return valor


class DatosClientePago(BaseModel):
    """
    Datos mínimos del cliente que realizará el pago.

    Se permite que Pydantic reciba los valores y luego las validaciones
    del módulo devuelvan errores en español.
    """

    correo: Optional[str] = None
    tipo_documento: Optional[str] = None
    numero_documento: Optional[str] = None
    nombre: Optional[str] = None
    telefono: Optional[str] = None

    @field_validator("correo", mode="before")
    @classmethod
    def normalizar_correo(cls, valor: Any) -> Optional[str]:
        if valor is None:
            return None

        correo = str(valor).strip().lower()
        if not correo:
            return None

        return correo


class DatosPago(BaseModel):
    """
    Datos monetarios y operativos de la orden de pago.
    """

    monto: Any = Field(...)
    moneda: Optional[str] = "COP"
    codigo_pais: Optional[int] = 343
    descripcion: Optional[str] = None
    fecha_expiracion: Optional[str] = None
    iva: Optional[str] = "0"
    metodo_pago: Optional[str] = ""
    recurrente: bool = False

    @field_validator("monto", mode="before")
    @classmethod
    def normalizar_monto(cls, valor: Any) -> Any:
        return normalizar_monto_decimal(valor)


class SolicitudCrearOrdenPago(BaseModel):
    """
    Payload interno para crear una orden de pago reutilizable.
    """

    sistema_origen: str
    referencia_externa: str
    cliente: DatosClientePago
    pago: DatosPago
    metadatos: Dict[str, Any] = {}


class RespuestaOrdenPago(BaseModel):
    """
    Respuesta estándar del módulo de pagos.
    """

    id_orden_pago: int
    codigo_orden_interno: str
    sistema_origen: str
    referencia_externa: str
    proveedor: str
    estado: str
    monto: Decimal
    moneda: str
    enlace_pago: Optional[str] = None
    id_orden_proveedor: Optional[str] = None
    referencia_proveedor: Optional[str] = None
    idempotente: bool = False


class SolicitudActualizarExpiracionOrdenPago(BaseModel):
    """
    Payload para actualizar la fecha de expiración de una orden pendiente.
    """

    fecha_expiracion: str
