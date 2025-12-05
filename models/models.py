from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputVariables(BaseModel):
    """
    Define la estructura y tipos de las variables de entrada.
    """

    NOMBRE_TITULAR: Optional[str] = None
    Nombre: Optional[str] = None
    CORREO: Optional[str] = None
    Contacto: Optional[str] = None
    Celular: Optional[str] = None  # Telefono libre usado por el flujo de renovaciones
    Universidad: Optional[str] = None
    EMAIL: Optional[str] = None
    PHONE_NUMBER: Optional[str] = Field(None, alias="PHONE_NUMBER")  # Mapea el alias
    SEMESTRE: Optional[int] = None
    LINEA_CREDITO: Optional[str] = None
    ESTADO_CREDITO: Optional[str] = None
    LINK: Optional[str] = None
    CUOTAS_PENDIENTES: Optional[int] = None

    # Permite campos adicionales y acepta alias/camelCase
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @field_validator("Contacto", "Celular", "PHONE_NUMBER", mode="before")
    @classmethod
    def _cast_phone_like_to_str(cls, v: Any) -> Optional[str]:
        """
        Convierte enteros u otros tipos sencillos a str para evitar 422 cuando llegan numeros.
        """
        if v is None:
            return None
        try:
            return str(v)
        except Exception:
            return v


class ExtractedVariables(BaseModel):
    """
    Define la estructura y tipos de las variables extraidas.
    """

    estado: Optional[bool] = None
    resumen: Optional[str] = None
    mensaje: Optional[str] = None
    interes_renovar: Optional[str] = None
    comentario_libre: Optional[str] = Field(None, alias="comentarioLibre")
    contesto_llamada: Optional[bool] = Field(None, alias="contestoLlamada")
    calidad_llamada: Optional[str] = Field(None, alias="calidadLlamada")
    correo_cliente: Optional[str] = Field(None, alias="correoCliente")
    primer_name: Optional[str] = Field(None, alias="primerName")
    desicion_correo: Optional[bool] = Field(None, alias="desicionCorreo")
    ambiguedad: Optional[bool] = Field(None, alias="ambig\u00fcedad")
    objetivo: Optional[str] = None
    interessolicitud: Optional[str] = Field(None, alias="interesSolicitud")

    # Permite campos adicionales y acepta alias/camelCase
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class WebhookPayload(BaseModel):
    """
    Define la estructura principal del payload que llega al webhook.
    """

    input_variables: InputVariables = Field(default_factory=InputVariables, alias="inputVariables")
    extracted_variables: ExtractedVariables = Field(default_factory=ExtractedVariables, alias="extractedVariables")

    # Permite cualquier otro campo en el nivel superior del payload y alias/camelCase
    model_config = ConfigDict(extra="allow", populate_by_name=True)
