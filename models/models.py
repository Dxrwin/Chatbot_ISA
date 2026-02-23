from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    resumen: Optional[str] = None
    comentario_libre: Optional[str] = Field(None, alias="comentarioLibre")
    contesto_llamada: Optional[bool] = Field(None, alias="contestoLlamada")
    calidad_llamada: Optional[str] = Field(None, alias="calidadLlamada")
    mensaje: Optional[str] = None
    correo_cliente: Optional[str] = Field(None, alias="correoCliente")
    primer_name: Optional[str] = Field(None, alias="primerName")
    desicion_correo: Optional[bool] = Field(None, alias="desicionCorreo")
    ambiguedad: Optional[bool] = Field(None, alias="ambig\u00fcedad")
    interes_correo: Optional[str] = Field(None, alias="interesCorreo")
    estado: Optional[bool] = None
    acpt_info_email: Optional[bool] = Field(None, alias="acptInfoEmail")
    aceptoinfocorreo: Optional[str] = Field(None, alias="aceptoInfoCorreo")
    objetivo: Optional[str] = None
    refinanciar: Optional[str] = None
    refinanciar_bool: Optional[bool] = None
    agendo_asst_assr: Optional[str] = Field(None, alias="agendoAsstAssr")
    asst_assr_bool: Optional[bool] = Field(None, alias="asstAssrBool")
    renovacion: Optional[str] = None
    interes_renovar: Optional[str] = None
    envio_correo: Optional[bool] = Field(None, alias="envioCorreo")
    fecha_asst_assor: Optional[str] = Field(None, alias="fechaAsstAssor")
    interessolicitud: Optional[str] = Field(None, alias="interesSolicitud")
    intrsrenovarbool: Optional[bool] = Field(None, alias="intrsRenovarBool")
    
    # Nuevas variables de salida para el flujo de cobranzas
    confirmacion_interes: Optional[bool] = Field(None, alias="confirmacionInteres")
    tipo_interes: Optional[str] = Field(None, alias="tipoInteres")
    autorizacion_contacto: Optional[bool] = Field(None, alias="autorizacionContacto")
    mas_informacion: Optional[bool] = Field(None, alias="masInformacion")
    razon_cliente: Optional[str] = Field(None, alias="razonCliente")
    interes_futuro: Optional[str] = Field(None, alias="interesFuturo")
    estado_negativo: Optional[str] = Field(None, alias="estadoNegativo")
    motivo_negativa: Optional[str] = Field(None, alias="motivoNegativa")

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

    @model_validator(mode="before")
    @classmethod
    def normalize_extracted_variables(cls, data: Any) -> Any:
        """
        Normaliza extracted_variables si viene como lista vacía o None.
        Convierte listas vacías a diccionarios vacíos.
        """
        if isinstance(data, dict):
            # Intenta ambos nombres: alias y nombre real
            extracted = data.get("extractedVariables") or data.get("extracted_variables")
            
            # Si es una lista (incluso vacía), convertir a diccionario vacío
            if isinstance(extracted, list):
                data["extracted_variables"] = {}
                if "extractedVariables" in data:
                    data["extractedVariables"] = {}
            
            # Si es None, usa diccionario vacío
            elif extracted is None:
                data["extracted_variables"] = {}
                if "extractedVariables" in data:
                    data["extractedVariables"] = {}
        
        return data


class ServicioExternoSchema(BaseModel):
    """
    Schema base para config de servicios externos.
    """

    id: Optional[int] = None
    nombre_servicio: str
    codigo: str
    url: str
    metodo: str
    timeout_ms: int = 10000
    reintentos: int = 0
    activo: int = 1
    header: Optional[dict[str, Any]] = None
    body: Optional[dict[str, Any]] = None
    creado_en: Optional[str] = None
    actualizado_en: Optional[str] = None

    model_config = ConfigDict(extra="allow")
