from decimal import Decimal
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class DatosClientePago(BaseModel):
    """
    Datos mínimos del cliente que realizará el pago.
    """

    correo: str
    tipo_documento: Optional[str] = None
    numero_documento: Optional[str] = None
    nombre: Optional[str] = None
    telefono: Optional[str] = None


class DatosPago(BaseModel):
    """
    Datos monetarios y operativos de la orden de pago.
    """

    monto: Decimal = Field(..., gt=0)
    moneda: str = "COP"
    codigo_pais: int = 343
    descripcion: str
    fecha_expiracion: str
    iva: str = "0"
    metodo_pago: Optional[str] = ""
    recurrente: bool = False


class SolicitudCrearOrdenPago(BaseModel):
    """
    Payload interno para crear una orden de pago reutilizable.

    sistema_origen:
        Identifica quién consume el módulo, por ejemplo chatbot_isa.

    referencia_externa:
        Identificador único del proceso en el sistema origen.
        Ejemplos: crédito, cuota, factura, cobro, renovación.
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
