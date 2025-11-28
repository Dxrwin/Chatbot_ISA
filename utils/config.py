from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, Dict, Any

class Settings(BaseSettings):
    """
    Configuraciones de la aplicación, cargadas desde variables de entorno (.env)
    Pydantic maneja automáticamente la carga de .env, la validación de tipos
    e incluso el parseo de JSON strings a diccionarios.
    """
    
    # --- Configuración de Correo (de mi versión anterior) ---
    SMTP_SERVER: str = Field(..., env="SMTP_SERVER")
    SMTP_PORT: int = Field(465, env="SMTP_PORT")
    SMTP_USER: str = Field(..., env="SMTP_USER")
    SMTP_PASS: str = Field(..., env="SMTP_PASS")
    
    # --- Configuración de Base de Datos para renovaciones---
    DB_HOST: str = Field("localhost", env="DB_HOST")
    DB_USER: str = Field("root", env="DB_USER")
    DB_PASSWORD_RENOVACION: str = Field(..., env="DB_PASSWORD_RENOVACION")
    DB_NAME_RENOVACION: str = Field(..., env="DB_NAME_RENOVACION")
        

    # --- Variables de Autenticación y API ---
    AUTH_URL: Optional[str] = Field(None, env="AUTH_URL")
    API_URL: Optional[str] = Field(None, env="API_URL")
    ORG_ID: Optional[str] = Field(None, env="ORG_ID")
    PAYABLE_URL: Optional[str] = Field(None, env="PAYABLE_URL")
    GET_PAYABLE_URL: Optional[str] = Field(None, env="GET_PAYABLE_URL")

    # --- Payloads de Autenticación (JSON parseadas) ---
    AUTH_PAYLOAD_PROD: Dict[str, Any] = Field(default_factory=dict, env="AUTH_PAYLOAD_PROD")
    AUTH_PAYLOAD_DEMO: Dict[str, Any] = Field(default_factory=dict, env="AUTH_PAYLOAD_DEMO")

    # --- Configuración de Correo (alineado con .env) ---
    EMAIL_FROM: Optional[str] = Field(None, env="EMAIL_FROM")
    EMAIL_TO: Optional[str] = Field(None, env="EMAIL_TO")
    EMAIL_PASSWORD: Optional[str] = Field(None, env="EMAIL_PASSWORD")



    # --- Configuración Telegram ---
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(None, env="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = Field(None, env="TELEGRAM_CHAT_ID")
    

    class Config:
        # Nombre del archivo del cual cargar las variables
        env_file = ".env"
        env_file_encoding = "utf-8"

# --- Instancia Única de Configuración ---
settings = Settings()

# --- Estado Global (separado de la configuración estática) ---
TOKEN_DATA = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0
}