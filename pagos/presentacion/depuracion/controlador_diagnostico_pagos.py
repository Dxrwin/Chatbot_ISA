from fastapi import APIRouter
from pydantic import BaseModel

from pagos.configuracion import obtener_configuracion_payvalida
from pagos.aplicacion.servicios.servicio_payvalida import ServicioPayvalida


enrutador = APIRouter(prefix="/diagnostico")


class SolicitudDiagnosticoChecksumWebhook(BaseModel):
    po_id: str
    status: str


@enrutador.get("/configuracion-payvalida")
async def diagnosticar_configuracion_payvalida():
    """
    Muestra ambiente y URL activa sin exponer secretos.
    """
    configuracion = obtener_configuracion_payvalida()

    return {
        "ambiente": configuracion.ambiente,
        "url_base_resuelta": configuracion.url_base,
        "merchant_preview": f"{configuracion.merchant[:4]}***" if configuracion.merchant else None,
        "timeout_segundos": configuracion.timeout_segundos,
        "fixed_hash_configurado": bool(configuracion.fixed_hash),
        "notification_hash_configurado": bool(configuracion.notification_hash),
    }


@enrutador.post("/checksum-webhook")
async def diagnosticar_checksum_webhook(solicitud: SolicitudDiagnosticoChecksumWebhook):
    """
    Calcula el checksum esperado para validar un webhook manual.

    Usar solo en desarrollo o pruebas controladas.
    No expone el NOTIFICATION_HASH.
    """
    servicio_payvalida = ServicioPayvalida()

    checksum = servicio_payvalida.generar_checksum_notificacion(
        codigo_orden_interno=solicitud.po_id,
        estado=solicitud.status,
    )

    return {
        "po_id": solicitud.po_id,
        "status": solicitud.status,
        "checksum_esperado": checksum,
        "formula": "SHA256(po_id + status + NOTIFICATION_HASH)",
        "advertencia": "No expone el NOTIFICATION_HASH. No dejar público sin autenticación.",
    }