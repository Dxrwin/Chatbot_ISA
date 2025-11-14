from pydantic import BaseModel, Field
from typing import Optional, Any



class InputVariables(BaseModel):
    """
    Define la estructura y tipos de las variables de entrada.
    """
    NOMBRE_TITULAR: Optional[str] = None
    CORREO: Optional[str] = None
    PHONE_NUMBER: Optional[str] = Field(None, alias="PHONE_NUMBER") # Mapea el alias
    SEMESTRE: Optional[int] = None
    LINEA_CREDITO: Optional[str] = None
    ESTADO_CREDITO: Optional[str] = None
    LINK: Optional[str] = None
    CUOTAS_PENDIENTES: Optional[int] = None
    
    # Permite campos adicionales que no están explícitamente definidos
    class Config:
        extra = 'allow'

class ExtractedVariables(BaseModel):
    """
    Define la estructura y tipos de las variables extraídas.
    """
    estado: Optional[bool] = None
    resumen: Optional[str] = None
    mensaje: Optional[str] = None
    interes_renovar: Optional[str] = None
    comentario_libre: Optional[str] = None
    link_enviado_sms: Optional[bool] = None
    contesto_llamada: Optional[bool] = None
    correo_cliente: Optional[str] = None
    
    # Permite campos adicionales
    class Config:
        extra = 'allow'

class WebhookPayload(BaseModel):
    """
    Define la estructura principal del payload que llega al webhook.
    """
    input_variables: InputVariables
    extracted_variables: ExtractedVariables
    
    # Permite cualquier otro campo en el nivel superior del payload
    class Config:
        extra = 'allow'