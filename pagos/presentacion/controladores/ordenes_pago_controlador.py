from fastapi import APIRouter, HTTPException, Request, status
from pagos.utilidades.validaciones_payvalida import ErrorValidacionPayvalida
from pagos.aplicacion.servicios.servicio_ordenes_pago import ServicioOrdenesPago
from pagos.esquemas.esquema_orden_pago import (
    SolicitudActualizarExpiracionOrdenPago,
    SolicitudCrearOrdenPago,
)
from pagos.excepciones import ErrorOrdenNoEncontrada, ErrorProveedorPago
from pagos.utilidades.auditoria_http import construir_contexto_auditoria_http
from pagos.utilidades.modelos import modelo_a_diccionario
from pagos.utilidades.validaciones_payvalida import ErrorValidacionPayvalida

enrutador = APIRouter(prefix="/ordenes-pago", tags=["Órdenes de pago"])


@enrutador.post("", status_code=status.HTTP_201_CREATED)
async def crear_orden_pago(request: Request, solicitud: SolicitudCrearOrdenPago):
    """
    Crea una orden de pago interna y genera un enlace de pago en Payválida.
    """
    contexto_auditoria = construir_contexto_auditoria_http(
        request=request,
        operacion="crear_orden",
        payload_recibido=modelo_a_diccionario(solicitud),
    )

    try:
        servicio = ServicioOrdenesPago()
        return await servicio.crear_orden_pago(
            solicitud=solicitud,
            contexto_auditoria=contexto_auditoria,
        )
    except ErrorValidacionPayvalida as error:
        raise HTTPException(status_code=422, detail=error.a_respuesta(),)
    except ErrorProveedorPago as error:
        raise HTTPException(status_code=502, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@enrutador.get("/{id_orden_pago}")
async def consultar_orden_pago(request: Request, id_orden_pago: int):
    """
    Consulta una orden de pago registrada localmente.
    """
    contexto_auditoria = construir_contexto_auditoria_http(
        request=request,
        operacion="consultar_orden",
    )

    try:
        servicio = ServicioOrdenesPago()
        return await servicio.consultar_orden_pago(
            id_orden_pago=id_orden_pago,
            contexto_auditoria=contexto_auditoria,
        )
    except ErrorOrdenNoEncontrada as error:
        raise HTTPException(status_code=404, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@enrutador.post("/{id_orden_pago}/sincronizar")
async def sincronizar_estado_orden_pago(request: Request, id_orden_pago: int):
    """
    Consulta el estado de la orden en Payválida y actualiza la base local.
    """
    contexto_auditoria = construir_contexto_auditoria_http(
        request=request,
        operacion="sincronizar_orden",
    )

    try:
        servicio = ServicioOrdenesPago()
        return await servicio.sincronizar_estado_orden_pago(
            id_orden_pago=id_orden_pago,
            contexto_auditoria=contexto_auditoria,
        )
    except ErrorOrdenNoEncontrada as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ErrorProveedorPago as error:
        raise HTTPException(status_code=502, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@enrutador.patch("/{id_orden_pago}/expiracion")
async def actualizar_expiracion_orden_pago(
    request: Request,
    id_orden_pago: int,
    solicitud: SolicitudActualizarExpiracionOrdenPago,
):
    """
    Actualiza la fecha de expiración de una orden pendiente en Payválida.
    """
    contexto_auditoria = construir_contexto_auditoria_http(
        request=request,
        operacion="actualizar_expiracion_orden",
        payload_recibido=modelo_a_diccionario(solicitud),
    )

    try:
        servicio = ServicioOrdenesPago()
        return await servicio.actualizar_expiracion_orden_pago(
            id_orden_pago=id_orden_pago,
            fecha_expiracion=solicitud.fecha_expiracion,
            contexto_auditoria=contexto_auditoria,
        )
    except ErrorValidacionPayvalida as error:
        raise HTTPException(status_code=422, detail=error.a_respuesta())
    except ErrorOrdenNoEncontrada as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ErrorProveedorPago as error:
        raise HTTPException(status_code=502, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))
