def modelo_a_diccionario(modelo):
    """
    Convierte un modelo Pydantic a diccionario soportando Pydantic v1 y v2.
    """
    if hasattr(modelo, "model_dump"):
        return modelo.model_dump()

    return modelo.dict()
