from fastapi import FastAPI
from pagos.enrutador import enrutador_pagos


def registrar_modulo_pagos(app: FastAPI) -> None:
    """
    Registra las rutas del módulo de pagos en la aplicación principal.

    Este archivo reemplaza el concepto de "bootstrap".
    En español lo llamamos "inicializador" porque su función es conectar
    el módulo de pagos con el objeto app que ya existe en logica.py.
    """
    app.include_router(enrutador_pagos)
