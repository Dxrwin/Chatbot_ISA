from typing import Optional
from pydantic import BaseModel


class NotificacionWebhookPayvalida(BaseModel):
    """
    Payload esperado desde Payválida.

    Payválida envía campos como:
    - pv_po_id: ID de la orden en Payválida.
    - po_id: código de la orden en el comercio.
    - status: approved o cancelled.
    - pv_checksum: firma de validación.
    """

    pv_po_id: Optional[int] = None
    po_id: str
    status: str
    pv_checksum: str
    amount: str
    iso_currency: str
    pv_payment: Optional[str] = None
