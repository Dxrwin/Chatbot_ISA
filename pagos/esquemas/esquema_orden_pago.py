from decimal import Decimal
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


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
