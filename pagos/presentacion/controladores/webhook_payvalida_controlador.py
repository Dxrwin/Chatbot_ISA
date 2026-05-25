from fastapi import APIRouter, HTTPException, Request

from pagos.aplicacion.servicios.servicio_webhook_payvalida import ServicioWebhookPayvalida
from pagos.excepciones import ErrorOrdenNoEncontrada, ErrorWebhookPayvalida
from pagos.utilidades.auditoria_http import construir_contexto_auditoria_http

enrutador = APIRouter(prefix="/webhooks", tags=["Webhooks de pago"])


@enrutador.post("/payvalida")
async def recibir_webhook_payvalida(request: Request):
    """
    Recibe notificaciones de Payválida.

    Payválida debe configurar esta URL pública en su módulo comercial.
    """
    cuerpo = await request.json()

    contexto_auditoria = construir_contexto_auditoria_http(
        request=request,
        operacion="webhook_payvalida",
        payload_recibido=cuerpo,
    )

    try:
        servicio = ServicioWebhookPayvalida()
        return await servicio.procesar_notificacion_payvalida(
            payload=cuerpo,
            contexto_auditoria=contexto_auditoria,
        )
    except ErrorOrdenNoEncontrada as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ErrorWebhookPayvalida as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))
