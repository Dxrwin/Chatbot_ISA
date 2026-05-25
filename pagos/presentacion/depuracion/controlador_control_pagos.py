from typing import Optional

from fastapi import APIRouter, Query

from pagos.infraestructura.repositorios.repositorio_depuracion_pagos import (
    RepositorioDepuracionPagos,
)


enrutador = APIRouter(prefix="/control")


@enrutador.get("/ordenes-pendientes")
async def ordenes_pendientes(
    minutos: Optional[int] = Query(None, ge=1),
    limite: int = Query(100, ge=1, le=500),
):
    """
    Lista órdenes pendientes.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.ordenes_pendientes(minutos=minutos, limite=limite)


@enrutador.get("/ordenes-sin-link")
async def ordenes_sin_link(limite: int = Query(100, ge=1, le=500)):
    """
    Lista órdenes incompletas.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.ordenes_sin_link(limite=limite)


@enrutador.get("/fallas-proveedor")
async def fallas_proveedor(limite: int = Query(50, ge=1, le=500)):
    """
    Lista errores en llamadas a Payválida.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.errores_payvalida(limite=limite)


@enrutador.get("/webhooks-invalidos")
async def webhooks_invalidos(limite: int = Query(50, ge=1, le=500)):
    """
    Lista webhooks con checksum inválido.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.webhooks(checksum_valido=0, limite=limite)


@enrutador.get("/consumos-fallidos")
async def consumos_fallidos(limite: int = Query(100, ge=1, le=500)):
    """
    Lista consumos fallidos del módulo.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.peticiones_modulo(exitoso=0, limite=limite)


@enrutador.get("/resumen-endpoints")
async def resumen_endpoints():
    """
    Resumen por endpoint.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.resumen_endpoints()


@enrutador.get("/resumen-estados")
async def resumen_estados():
    """
    Resumen de órdenes por estado.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.resumen_estados()


@enrutador.get("/resumen-ambientes-proveedor")
async def resumen_ambientes_proveedor():
    """
    Detecta si las llamadas fueron a sandbox o producción.
    """
    repositorio = RepositorioDepuracionPagos()
    return await repositorio.resumen_ambientes_proveedor()