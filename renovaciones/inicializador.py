from fastapi import FastAPI

from renovaciones.perfil_kuenta import enrutador as enrutador_perfil_kuenta


def registrar_modulo_renovaciones(app: FastAPI) -> None:
    """Registra las rutas de renovaciones en la aplicacion principal."""
    app.include_router(enrutador_perfil_kuenta)
