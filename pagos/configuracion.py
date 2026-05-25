import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(".env.pagos")


@dataclass
class ConfiguracionPayvalida:
    """
    Configuración requerida para consumir Payválida.

    Las credenciales sensibles deben estar en variables de entorno.
    No deben quemarse en código ni guardarse en tablas visibles.
    """

    ambiente: str
    url_base: str
    merchant: str
    fixed_hash: str
    notification_hash: str
    timeout_segundos: int


def obtener_configuracion_payvalida() -> ConfiguracionPayvalida:
    """
    Carga la configuración de Payválida desde variables de entorno.
    """
    ambiente = os.getenv("PAYVALIDA_AMBIENTE", "sandbox").lower().strip()

    url_sandbox = os.getenv("PAYVALIDA_SANDBOX_BASE_URL", "https://api-test.payvalida.com")
    url_produccion = os.getenv("PAYVALIDA_PROD_BASE_URL", "https://api.payvalida.com")

    url_base = url_produccion if ambiente == "produccion" else url_sandbox

    return ConfiguracionPayvalida(
        ambiente=ambiente,
        url_base=url_base.rstrip("/"),
        merchant=os.getenv("PAYVALIDA_MERCHANT", ""),
        fixed_hash=os.getenv("PAYVALIDA_FIXED_HASH", ""),
        notification_hash=os.getenv("PAYVALIDA_NOTIFICATION_HASH", ""),
        timeout_segundos=int(os.getenv("PAYVALIDA_TIMEOUT_SEGUNDOS", "20")),
    )


@dataclass
class ConfiguracionBaseDatos:
    """
    Configuración de conexión MySQL para el módulo de pagos.
    Compatible con la base renovaciones_db.
    """

    host: str
    puerto: int
    usuario: str
    clave: str
    base_datos: str
    pool_minimo: int
    pool_maximo: int


def obtener_configuracion_base_datos() -> ConfiguracionBaseDatos:
    """
    Carga la configuración de MySQL desde variables de entorno.
    """
    return ConfiguracionBaseDatos(
        host=os.getenv("DB_HOST", "localhost"),
        puerto=int(os.getenv("DB_PORT", "3306")),
        usuario=os.getenv("DB_USER", ""),
        clave=os.getenv("DB_PASSWORD_RENOVACION", ""),
        base_datos=os.getenv("DB_NAME_RENOVACION", "renovaciones_db"),
        pool_minimo=int(os.getenv("DB_POOL_MINIMO", "1")),
        pool_maximo=int(os.getenv("DB_POOL_MAXIMO", "5")),
    )
