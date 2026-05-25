from typing import Optional

from fastapi import APIRouter, Query

from pagos.infraestructura.repositorios.repositorio_depuracion_pagos import (
    RepositorioDepuracionPagos,
)


enrutador = APIRouter(prefix="/auditoria")


@enrutador.get("/ordenes")
async def listar_ordenes(
    estado: Optional[str] = None,
    sistema_origen: Optional[str] = None,
    referencia_externa: Optional[str] = None,
    limite: int = Query(50, ge=1, le=500),
):
    """
    Lista órdenes de pago con filtros básicos.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.listar_ordenes(
        estado=estado,
        sistema_origen=sistema_origen,
        referencia_externa=referencia_externa,
        limite=limite,
    )


@enrutador.get("/ordenes/{id_orden_pago}")
async def obtener_orden(id_orden_pago: int):
    """
    Obtiene el detalle completo de una orden.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.obtener_orden(id_orden_pago)


@enrutador.get("/ordenes/codigo/{codigo_orden_interno}")
async def obtener_orden_por_codigo(codigo_orden_interno: str):
    """
    Obtiene una orden por el código interno enviado a Payválida como order.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.obtener_orden_por_codigo(codigo_orden_interno)


@enrutador.get("/ordenes/{id_orden_pago}/eventos")
async def eventos_por_orden(id_orden_pago: int):
    """
    Lista eventos internos de una orden.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.eventos_por_orden(id_orden_pago)


@enrutador.get("/ordenes/{id_orden_pago}/solicitudes-proveedor")
async def solicitudes_proveedor_por_orden(id_orden_pago: int):
    """
    Lista llamadas realizadas a Payválida para una orden.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.solicitudes_proveedor(id_orden_pago=id_orden_pago)


@enrutador.get("/ordenes/{id_orden_pago}/webhooks")
async def webhooks_por_orden(id_orden_pago: int):
    """
    Lista webhooks recibidos para una orden.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.webhooks(id_orden_pago=id_orden_pago)


@enrutador.get("/ordenes/{id_orden_pago}/resumen-integral")
async def resumen_integral_orden(id_orden_pago: int):
    """
    Retorna un resumen integral de una orden.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.resumen_integral_orden(id_orden_pago)


@enrutador.get("/peticiones")
async def listar_peticiones_modulo(
    exitoso: Optional[int] = Query(None, description="1 exitosas, 0 fallidas"),
    operacion: Optional[str] = None,
    limite: int = Query(100, ge=1, le=500),
):
    """
    Lista consumos HTTP del módulo de pagos.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.peticiones_modulo(
        exitoso=exitoso,
        operacion=operacion,
        limite=limite,
    )


@enrutador.get("/solicitudes-proveedor")
async def listar_solicitudes_proveedor(
    id_orden_pago: Optional[int] = None,
    exitoso: Optional[int] = Query(None, description="1 exitosas, 0 fallidas"),
    limite: int = Query(50, ge=1, le=500),
):
    """
    Lista llamadas salientes al proveedor de pagos.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.solicitudes_proveedor(
        id_orden_pago=id_orden_pago,
        exitoso=exitoso,
        limite=limite,
    )


@enrutador.get("/webhooks")
async def listar_webhooks(
    id_orden_pago: Optional[int] = None,
    checksum_valido: Optional[int] = Query(None, description="1 válido, 0 inválido"),
    procesado: Optional[int] = Query(None, description="1 procesado, 0 no procesado"),
    limite: int = Query(50, ge=1, le=500),
):
    """
    Lista webhooks recibidos.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.webhooks(
        id_orden_pago=id_orden_pago,
        checksum_valido=checksum_valido,
        procesado=procesado,
        limite=limite,
    )