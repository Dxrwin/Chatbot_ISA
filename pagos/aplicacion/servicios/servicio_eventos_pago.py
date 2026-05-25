from typing import Any, Dict, Optional

from pagos.infraestructura.repositorios.repositorio_eventos_pago import RepositorioEventosPago


class ServicioEventosPago:
    """
    Servicio para registrar eventos de una orden de pago.
    """

    def __init__(self):
        self.repositorio_eventos = RepositorioEventosPago()

    async def registrar_evento(
        self,
        id_orden_pago: int,
        tipo_evento: str,
        origen_evento: str,
        descripcion: Optional[str] = None,
        estado_anterior: Optional[str] = None,
        estado_nuevo: Optional[str] = None,
        datos_evento: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Registra un evento en eventos_orden_pago.
        """
        return await self.repositorio_eventos.crear(
            id_orden_pago=id_orden_pago,
            tipo_evento=tipo_evento,
            origen_evento=origen_evento,
            descripcion=descripcion,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_nuevo,
            datos_evento=datos_evento,
        )
