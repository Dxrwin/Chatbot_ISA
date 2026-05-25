from pydantic import BaseModel, ValidationError, field_validator, Field
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
from utils.notify_error import (
    error_notify,
    get_cached_logs,
    send_log_email,
    send_log_telegram,
    info_notify,
)
from utils.enviar_correo_IA import (
    procesar_webhook_renovacion,
    procesar_webhook_webinar,
    procesar_llamada_renovacion_Y_refinanciamiento,
    procesar_webhook_cobranzas,
)
from utils.auth import obtener_token
from utils.external_client import ExternalClient
from fastapi.encoders import jsonable_encoder
from utils.export_service import generar_excel_memoria
from utils.database import insertar_registro_completo, consultar_creditos_filtro
from utils.database import insertar_log, consultar_logs_filtrados
from utils.servicios_externos_service import (
    crear_servicio_externo,
    actualizar_servicio_externo,
    obtener_servicio_externo_por_codigo,
)
from utils.whatsapp_service import enviar_whatsapp_renovacion
from utils.linea_credito_links import obtener_link_por_linea_credito
from models.bitrix_call_models import (
    BitrixCallCompletedRequest,
    BitrixCallCompletedResponse,
    BitrixDebugSearchClientRequest,
)
from dataclasses import dataclass
import json
from models.models import WebhookPayload
import traceback
import httpx
import logging
import asyncio
from fastapi import Request
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from utils.config import settings
import re
import unicodedata
import aiomysql
import os

# Obtener parametros de configuración DE LA BASE DE DATOS
db_host = settings.DB_HOST
db_user = settings.DB_USER
db_pass = settings.DB_PASSWORD_RENOVACION
db_name = settings.DB_NAME_RENOVACION


class ExtractedVariables(BaseModel):
    estado: Optional[bool] = None
    resumen: Optional[str] = None
    mensaje: Optional[str] = None
    interes_renovar: Optional[str] = None
    comentario_libre: Optional[str] = None
    link_enviado_sms: Optional[str] = None
    contesto_llamada: Optional[bool] = None
    correo_cliente: Optional[str] = None


class SendEmailRequest(BaseModel):
    extracted_variables: ExtractedVariables
    destinatario: Optional[str] = None  # Email principal (opcional)


class RenovacionPayload(BaseModel):
    estado_final_renovacion: str
    estado_pago_payvalida: str
    nombre_cliente: str


# id credito mora
class MoraData(BaseModel):
    id_credito: str


class CreditoData(BaseModel):
    ID_Credito_simulacion: str
    referencia_simulacion: Optional[str] = None
    nombre_linea_simulacion: Optional[str] = None
    cuota_inicial_simulacion: Optional[str] = None
    semestre_renovacion: Optional[str] = None

    # IMPORTANTE: Aquí definimos que esperamos un entero
    estado_credito_post_confirmado: Optional[str] = None

    valor_solicitud_express: Optional[str] = None


class RenovacionRefactorRequest(BaseModel):
    numero_telefono: str
    correo_cliente: str
    nombre_cliente: str
    cedula_cliente: str
    datos_credito: CreditoData


# 1. Modelo para los filtros de búsqueda
class ConsultaCreditoRequest(BaseModel):
    cedula: Optional[str] = None
    telefono: Optional[str] = None
    estado_id: Optional[int] = None
    fecha_inicio: Optional[str] = None  # Ej: "2026-01-09"
    fecha_fin: Optional[str] = None  # Ej: "2026-01-10"

    exportar_excel: Optional[bool] = False


# Modelo para los filtros de consulta de logs
class ConsultaLogsRequest(BaseModel):
    fecha: Optional[str] = None  # Ej: "12-02-2025"
    fecha_inicio: Optional[str] = None  # Ej: "01-01-2025"
    fecha_fin: Optional[str] = None  # Ej: "31-12-2025"
    log_id: Optional[int] = None
    metodo: Optional[str] = None  # Nombre del método, búsqueda parcial
    client_id: Optional[str] = None  # ID del cliente, búsqueda parcial
    codigo_http: Optional[int] = None  # Ej: 500, 401, 409
    tipo: Optional[str] = None  # "error" o "info"
    limite: int = 100  # Cantidad máxima de registros (default 100, máximo 1000)
    offset: int = 0  # Para paginación

    class Config:
        json_schema_extra = {
            "example": {
                "fecha": "12-02-2025",
                "metodo": "create_payable",
                "tipo": "error",
                "limite": 50,
                "offset": 0,
            }
        }


class ServicioExternoCreateRequest(BaseModel):
    nombre_servicio: str
    codigo: str
    url: str
    metodo: str
    timeout_ms: int = 10000
    reintentos: int = 0
    activo: int = 1
    header: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None


class ServicioExternoUpdateRequest(BaseModel):
    nombre_servicio: Optional[str] = None
    url: Optional[str] = None
    metodo: Optional[str] = None
    timeout_ms: Optional[int] = None
    reintentos: Optional[int] = None
    activo: Optional[int] = None
    header: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None


class PayableRequest(BaseModel):
    creditLineId: str
    principal: float
    time: int
    paymentFrequency: int
    initialFee: float
    disbursementMethod: Optional[str] = None
    source: Optional[str] = None
    redirectUrl: Optional[str] = None
    callbackUrl: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    @field_validator("principal", "initialFee", mode="before")
    @classmethod
    def validate_floats(cls, v):
        """Convierte strings a float si es necesario"""
        if isinstance(v, str):
            if v.strip() == "":
                raise ValueError("El campo no puede estar vacío")
            try:
                return float(v.replace(",", ".").strip())
            except ValueError:
                raise ValueError(f"No se puede convertir '{v}' a número decimal")
        return v

    @field_validator("time", "paymentFrequency", mode="before")
    @classmethod
    def validate_ints(cls, v):
        """Convierte strings a int si es necesario"""
        if isinstance(v, str):
            if v.strip() == "":
                raise ValueError("El campo no puede estar vacío")
            try:
                return int(v.strip())
            except ValueError:
                raise ValueError(f"No se puede convertir '{v}' a número entero")
        return v

    @field_validator("disbursementMethod", mode="before")
    @classmethod
    def validate_disbursement(cls, v):
        """Valida disbursementMethod, rechaza strings vacíos"""
        if v == "" or v is None:
            return None
        if isinstance(v, str):
            return v.strip() if v.strip() else None
        return v

    @field_validator("creditLineId", mode="before")
    @classmethod
    def validate_creditlineId(cls, v):
        """Valida creditLineId, no puede estar vacío"""
        if not v or (isinstance(v, str) and v.strip() == ""):
            raise ValueError("creditLineId no puede estar vacío")
        return str(v).strip()


class InstallmentOrderCreateRequest(BaseModel):
    amount: int | float
    provider: str
    collectionCosts: int | float = 0


# Modelos para las solicitudes
class ClienteRequest(BaseModel):
    id_cliente: str


# Modelo para la solicitud de detalle de cuota
class DetalleCuotaRequest(BaseModel):
    id_cliente: str
    numero_cuota: int


# Modelo para probar notificaciones
class TestNotifyRequest(BaseModel):
    method_name: str = "test_method"
    client_id: str = "test_client"
    message: str = "Mensaje de prueba para notificación"


class ConfirmarTOTPRequest(BaseModel):
    codigo_totp: str
    id_debtor: str
    id_asistance: str


# ===== MAPEO DE ERRORES TOTP =====
ERRORES_TOTP = {
    "InvalidRequest": {
        "mensaje": "El código TOTP es inválido o ha expirado.",
        "detalles": "Por favor verifica el código y vuelve a intentar.",
    },
    "ExpiredCode": {
        "mensaje": "El código TOTP ha expirado.",
        "detalles": "Solicita un nuevo código e intenta nuevamente.",
    },
    "MaxAttemptsExceeded": {
        "mensaje": "Has excedido el número máximo de intentos.",
        "detalles": "Por seguridad, tu sesión ha sido bloqueada temporalmente. Intenta más tarde.",
    },
    "UserNotFound": {
        "mensaje": "No se encontró el usuario.",
        "detalles": "Por favor verifica los datos e intenta nuevamente.",
    },
    "UnauthorizedRequest": {
        "mensaje": "No tienes permiso para realizar esta acción.",
        "detalles": "Por favor contacta con soporte técnico.",
    },
}


print("🔥 BOOT VERSION 8693665")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manejador de eventos del ciclo de vida del servidor usando el nuevo sistema Lifespan.
    """
    try:
        message = (
            "Servidor iniciado correctamente\n"
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Ambiente: Producción\n"
            "Estado: ONLINE"
        )
        await info_notify(
            method_name="startup_server", client_id="system", info_message=message
        )
        logger.info("Servidor iniciado y notificaciones enviadas correctamente")

        yield

        message = (
            "Servidor detenido\n"
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Ambiente: Producción\n"
            "Estado: OFFLINE"
        )
        await error_notify(
            method_name="shutdown_server", client_id="system", error_message=message
        )
        logger.info("Servidor detenido y notificaciones enviadas correctamente")

    except Exception as e:
        logger.error(f"Error en el ciclo de vida del servidor: {e}")


app = FastAPI(lifespan=lifespan)

# El middleware para detectar reinicios
@app.middleware("http")
async def check_server_restart(request: Request, call_next):
    """
    Middleware que detecta reinicios del servidor por cambios en el codigo
    """
    try:
        if not hasattr(app.state, "server_started"):
            app.state.server_started = True
            message = (
                "Servidor actualizado y reiniciado\n"
                f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"Ambiente: Producción\n"
                "Estado: RELOADED"
            )
            await info_notify(
                method_name="server_reload", client_id="system", info_message=message
            )
            logger.info("Servidor reiniciado y notificaciones enviadas")
    except Exception as e:
        logger.error(f"Error al enviar notificación de reinicio: {e}")

    return await call_next(request)


# Mensajes amigables para el cliente
MENSAJES_CLIENTE = {
    "error_conexion": "En este momento tenemos intermitencias con nuestro proveedor. Por favor, dale unos minutos y vuelve a intentarlo.",
    "error_servicio": "Nuestro sistema está en mantenimiento o temporalmente fuera de servicio. No eres tú, somos nosotros. Intenta más tarde.",
    "error_datos": "Parece que hay un error en los montos ingresados. Verifica que el valor del crédito y la cuota inicial sean números válidos.",
    "perfil_incompleto": "Para poder crear tu crédito, necesitamos que completes tu perfil. Faltan datos obligatorios.",
    "error_simulacion": "Logramos registrar tu solicitud, pero tuvimos un hipo al generar tu plan de pagos. Por favor, actualiza la página o intenta nuevamente.",
    "cuotas_no_encontradas": "El crédito se generó, pero el proveedor no nos devolvió tu tabla de cuotas. Nuestro equipo de soporte ya fue notificado.",
    "error_general": "Ocurrió un error inesperado al procesar tu crédito. Por favor, contacta a soporte si el problema persiste.",
}

MENSAJES_USUARIO = {
    "valor_invalido": "El monto ingresado no es válido. Por favor ingresa un valor numérico, por ejemplo: 2500000 o $2.500.000",
    "linea_no_existe": "Lo sentimos, el producto financiero seleccionado no está disponible en este momento. Por favor intenta nuevamente más tarde.",
    "semestre_invalido": "El semestre ingresado no es válido. Por favor selecciona una opción entre 'primer semestre' y 'décimo semestre'.",
    "plazo_invalido": "El plazo seleccionado no es válido. Por favor escoge entre 1 y 6 meses.",
    "error_conexion": "En este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
    "error_calculo": "Hubo un problema al calcular tu financiamiento. Por favor verifica los valores ingresados e intenta nuevamente.",
    "datos_faltantes": "Por favor completa todos los campos requeridos para calcular tu financiamiento.",
}

# Configuración de logs
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# Variables de configuración usadas en los endpoints principales
API_URL = settings.API_URL
ORG_ID = settings.ORG_ID
PAYABLE_URL = settings.PAYABLE_URL
GET_PAYABLE_URL = settings.GET_PAYABLE_URL
ASSISTANCE_URL = settings.ASSISTANCE_URL

# ============================================================
# Configuración Bitrix24 - Cobranzas / llamadas automatizadas
# ============================================================

BITRIX_BASE_URL = os.getenv("BITRIX_BASE_URL", "").rstrip("/")
BITRIX_DEAL_CATEGORY_ID = int(os.getenv("BITRIX_DEAL_CATEGORY_ID", "0"))
BITRIX_DEAL_STAGE_ID = os.getenv("BITRIX_DEAL_STAGE_ID", "UC_L3QC2Y")

# Campo confirmado en Bitrix24:
# UF_CRM_1697774324 = Documento de identidad
BITRIX_CONTACT_CEDULA_FIELD = os.getenv(
    "BITRIX_CONTACT_CEDULA_FIELD", "UF_CRM_1697774324"
)

# Embudo HOR CARTERA y etapa CARTERA2024 según consulta realizada.
BITRIX_DEAL_CATEGORY_ID = int(os.getenv("BITRIX_DEAL_CATEGORY_ID", "0"))
BITRIX_DEAL_STAGE_ID = os.getenv("BITRIX_DEAL_STAGE_ID", "NEW")

# Responsable opcional.
BITRIX_ASSIGNED_BY_ID_RAW = os.getenv("BITRIX_ASSIGNED_BY_ID")
BITRIX_ASSIGNED_BY_ID = (
    int(BITRIX_ASSIGNED_BY_ID_RAW)
    if BITRIX_ASSIGNED_BY_ID_RAW and BITRIX_ASSIGNED_BY_ID_RAW.isdigit()
    else None
)

CREATE_DEAL_ON_VALID_PAYMENT = os.getenv(
    "CREATE_DEAL_ON_VALID_PAYMENT", "false"
).strip().lower() in ["true", "1", "yes", "si", "sí"]

CONTACT_ENTITY_TYPE_ID = 3
DEAL_ENTITY_TYPE_ID = 2

CONTACT_LOOKUP_SELECT = [
    "id",
    "name",
    "lastName",
    "email",
    "phone",
]


def build_contact_lookup_select() -> List[str]:
    """
    Select fijo para buscar contactos.

    Importante:
    No incluir UF_CRM_1697774324 en el select de búsqueda, porque en las pruebas
    reales Bitrix respondió solo ese campo y omitió id/name/phone.

    El campo UF_CRM_1697774324 se usa únicamente en filter cuando se busca
    por cédula.
    """

    return CONTACT_LOOKUP_SELECT.copy()

# --- Sistema de Cache en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5


# Normaliza nombres/titulos a un slug estable (sin tildes, minusculas y sin simbolos)
def slugify_nombre(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFD", value)
    ascii_str = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    ascii_str = re.sub(r"[^a-zA-Z0-9\s-]", "", ascii_str)
    ascii_str = re.sub(r"\s+", " ", ascii_str).strip().lower()
    return ascii_str.replace(" ", "-")


def formatear_fecha_legible(fecha_iso: str) -> str:
    """
    Convierte una fecha ISO 8601 a formato legible en español.
    Ejemplo: 2026-01-12T05:00:00Z -> lunes, enero 12, 2026
    """
    try:
        # Mapeo de días de la semana en español
        dias_semana = {
            0: "lunes",
            1: "martes",
            2: "miércoles",
            3: "jueves",
            4: "viernes",
            5: "sábado",
            6: "domingo",
        }

        # Mapeo de meses en español
        meses = {
            1: "enero",
            2: "febrero",
            3: "marzo",
            4: "abril",
            5: "mayo",
            6: "junio",
            7: "julio",
            8: "agosto",
            9: "septiembre",
            10: "octubre",
            11: "noviembre",
            12: "diciembre",
        }

        # Parsear la fecha ISO
        fecha_obj = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))

        # Obtener componentes
        dia_semana = dias_semana[fecha_obj.weekday()]
        mes = meses[fecha_obj.month]
        dia = fecha_obj.day
        año = fecha_obj.year

        return f"{dia_semana}, {mes} {dia}, {año}"
    except Exception as e:
        logger.error(f"Error al formatear fecha {fecha_iso}: {str(e)}")
        return fecha_iso


def obtener_fecha_iso_bogota() -> str:
    """Retorna la fecha/hora actual en zona America/Bogota sin microsegundos."""
    return datetime.now(ZoneInfo("America/Bogota")).isoformat(timespec="seconds")


def formatear_valor_moneda(valor: float) -> str:
    """
    Formatea un número como moneda COP sin decimales.
    Ejemplo: 12527818.528192377 -> $12.527.819
    """
    try:
        # Redondear a entero
        valor_redondeado = round(valor)
        # Formatear con separadores de miles
        return f"${valor_redondeado:,}".replace(",", ".")
    except Exception as e:
        logger.error(f"Error al formatear valor {valor}: {str(e)}")
        return str(valor)


# confirmar codigo totp realizando un bucle para confirmar en cada intento
# recibe el codigo, id_debtor, id_asistance
@app.post("/confirmar-totp/{codigo_totp}")
async def confirmar_totp(codigo_totp: str, ConfirmarTOTPRequest: ConfirmarTOTPRequest):
    """
    Confirma el código TOTP enviado por el usuario con reintentos inteligentes.

    Parámetros:
    - codigo_totp: Código TOTP proporcionado por el usuario.
    - id_debtor: ID del deudor asociado al TOTP.
    - id_asistance: ID de la asistencia asociada al TOTP.

    Retorna:
    - Éxito (status 200): {"estado": "success", "data": {...}, "mensaje": "Código confirmado exitosamente"}
    - Error (status 400/412): {"estado": "error", "codigo_error": "...", "mensaje": "...", "detalles": "..."}
    - Error interno (status 500): HTTPException
    """
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # segundos entre reintentos
    TIMEOUT = 15

    method_name = "confirmar-totp"
    client_id = ConfirmarTOTPRequest.id_debtor
    last_error_data = None
    last_error_type = None  # Rastrear qué tipo de error se capturó

    try:
        # Obtener token de autenticación
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            access_token = await obtener_token(client)
            if not access_token:
                logger.error("No se pudo obtener el token de acceso")
                await error_notify(
                    method_name, client_id, "No se pudo obtener el token de acceso"
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "estado": "error",
                        "codigo_error": "TokenError",
                        "mensaje": "No se pudo autenticar la solicitud.",
                        "detalles": "Por favor intenta nuevamente en unos minutos.",
                    },
                )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": access_token,
            }

            # Construir payload correctamente con interpolación
            payload_totp = {"totp": codigo_totp}  # payload para confirmacion TOTP
            ext_client_totp = None  # cliente externo opcional para TOTP
            try:  # intenta cargar configuracion desde BD
                ext_client_totp = await ExternalClient.from_code(
                    "KUENTA_TOTP_APPROVE", client_id=client_id
                )  # codigo de servicio en BD
            except ValueError:  # si no existe en BD
                ext_client_totp = None  # fallback a settings

            # validacion de la URL de asistencia, SI ext_client_totp tiene URL, usa esa, sino usa ASSISTANCE_URL de settings
            assistance_base_url = (
                ext_client_totp.url
                if ext_client_totp and ext_client_totp.url
                else ASSISTANCE_URL
            )  # base desde BD o settings
            assistance_base_url = (
                assistance_base_url.rstrip("/") + "/"
            )  # asegura slash final
            assistance_url_template = f"{assistance_base_url}{{id_debtor}}/assistances/{{id_asistance}}/approve"  # plantilla con placeholders, se concatena la url base con el path dinamico
            assistance_url = assistance_url_template.format(  # construye URL final, asignando parametros
                id_debtor=ConfirmarTOTPRequest.id_debtor,  # parametro id_debtor
                id_asistance=ConfirmarTOTPRequest.id_asistance,  # parametro id_asistance
            )  # URL final de confirmacion

            logger.info(
                f"Iniciando confirmación de TOTP para deudor: {ConfirmarTOTPRequest.id_debtor} \n con url: {assistance_url}"
            )

            # ===== REINTENTOS CON MANEJO INTELIGENTE DE ERRORES =====

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    logger.info(f"Intento {attempt}/{MAX_RETRIES} para confirmar TOTP")

                    response_data = {}  # contenedor de respuesta

                    if ext_client_totp:  # usa servicio externo si existe
                        # Inyectar variables dinámicas
                        ext_client_totp.set_dynamic_values(
                            {
                                "ORG_ID": ORG_ID,
                                "access_token": access_token,
                                "codigo_totp": codigo_totp,
                            }
                        )

                        # Asignar URL final
                        ext_client_totp.set_url(assistance_url)

                        # imprimir configuracion del cliente externo para debug
                        logger.info(
                            f"Configuración ExternalClient TOTP para ejecutar la peticion: {ext_client_totp.__dict__}"
                        )

                        response = (
                            await ext_client_totp.run()
                        )  # ejecuta peticion externa

                        if not isinstance(response, dict):  # valida tipo de respuesta
                            await error_notify(
                                method_name,
                                client_id,
                                "Respuesta invalida del servicio externo TOTP",
                            )
                            raise Exception(
                                "Respuesta invalida del servicio externo"
                            )  # error si no es dict

                        status_code = response.get(
                            "status", 500
                        )  # status HTTP simulado

                        response_data = response.get("data") or {}  # data de respuesta
                        if not isinstance(response_data, dict):  # valida data
                            response_data = {}  # fallback a dict vacio
                        response = type(
                            "Resp", (), {"status_code": status_code}
                        )()  # wrapper para status_code
                    else:  # fallback a httpx directo
                        response = await client.post(  # request httpx original
                            assistance_url,  # URL de confirmacion
                            headers=headers,  # headers con token
                            json=payload_totp,  # payload TOTP
                        )
                        status_code = response.status_code  # status HTTP real
                        # Intentar parsear la respuesta como JSON
                        try:
                            response_data = response.json()  # parse JSON
                        except Exception as json_err:
                            logger.error(f"Error al parsear JSON: {json_err}")
                            # insertar log del error de parseo en la base de datos
                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"Error al parsear JSON en intento {attempt}: {str(json_err)}",
                                http_code=status_code,
                                tipo="error",
                            )

                            response_data = {}

                    logger.info(
                        f"Respuesta de API (status {response.status_code}): {response_data}"
                    )

                    # ===== CASO EXITOSO: status 200 y "status": "success" =====
                    if response.status_code == 200:
                        if response_data.get("status") == "success":
                            logger.info(
                                f"TOTP confirmado exitosamente en intento {attempt}"
                            )
                            await info_notify(
                                method_name=method_name,
                                client_id=client_id,
                                info_message=f"TOTP confirmado exitosamente",
                            )
                            return JSONResponse(
                                status_code=200,
                                content={
                                    "estado": "success",
                                    "mensaje": "Código TOTP confirmado exitosamente.",
                                    "data": response_data.get("data", {}),
                                    "detalles": "Tu identidad ha sido verificada correctamente.",
                                },
                            )
                        else:
                            error_traceback = traceback.format_exc()
                            # Status 200 pero "status": "fail" en la respuesta
                            error_code = response_data.get("data", {}).get(
                                "code", "UnknownError"
                            )
                            error_msg = response_data.get("data", {}).get(
                                "error", "Error desconocido"
                            )
                            logger.warning(
                                f"TOTP rechazado (status 200 pero fail): {error_code} - {error_msg}"
                            )
                            last_error_data = response_data
                            last_error_type = "STATUS_200_BUT_FAIL"

                            # insertar log del error en la base de datos
                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"TOTP rechazado en intento {attempt}: {error_code} - {error_msg}, error completo: {last_error_data}",
                                http_code=response.status_code,
                                tipo="error",
                                traceback_str=error_traceback,
                            )

                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(RETRY_DELAY * attempt)
                                continue
                            else:
                                # Agotados los reintentos
                                return await _handle_totp_error(
                                    error_code, error_msg, method_name, client_id
                                )

                    # ===== CASO DE ERROR CON DETALLES: status 412 u otro error =====
                    elif response.status_code in [400, 412, 422]:
                        error_code = response_data.get("data", {}).get(
                            "code", "InvalidRequest"
                        )
                        error_msg = response_data.get("data", {}).get(
                            "error", "Error en la solicitud"
                        )
                        logger.warning(
                            f"Intento {attempt}: Error {response.status_code} - {error_code}: {error_msg}"
                        )
                        last_error_data = response_data
                        last_error_type = f"HTTP_{response.status_code}_CLIENT_ERROR"

                        # Estos errores no se reintenten (son errores del cliente)
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error {response.status_code} al confirmar TOTP: {error_code} - {error_msg}",
                        )
                        return await _handle_totp_error(
                            error_code, error_msg, method_name, client_id
                        )

                    # ===== CASO DE ERROR DEL SERVIDOR: 500+ =====
                    elif response.status_code >= 500:
                        logger.warning(
                            f"Intento {attempt}: Error del servidor ({response.status_code})"
                        )
                        last_error_data = response_data
                        last_error_type = f"HTTP_{response.status_code}_SERVER_ERROR"

                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue
                        else:
                            await error_notify(
                                method_name,
                                client_id,
                                f"Error del servidor después de {MAX_RETRIES} intentos: {response.status_code}",
                            )
                            return JSONResponse(
                                status_code=503,
                                content={
                                    "estado": "error",
                                    "codigo_error": "ServerError",
                                    "mensaje": "El servicio no está disponible en este momento.",
                                    "detalles": "Por favor intenta nuevamente más tarde.",
                                },
                            )

                    # ===== OTROS CASOS DE ERROR HTTP =====
                    else:
                        logger.warning(
                            f"Intento {attempt}: Error HTTP inesperado ({response.status_code})"
                        )
                        last_error_data = response_data
                        last_error_type = (
                            f"HTTP_{response.status_code}_UNEXPECTED_ERROR"
                        )

                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue
                        else:
                            await error_notify(
                                method_name,
                                client_id,
                                f"Error HTTP inesperado: {response.status_code}",
                            )
                            return JSONResponse(
                                status_code=500,
                                content={
                                    "estado": "error",
                                    "codigo_error": "UnknownError",
                                    "mensaje": "Ocurrió un error inesperado.",
                                    "detalles": "Por favor intenta nuevamente más tarde.",
                                },
                            )

                # ===== MANEJO DE EXCEPCIONES DE CONEXIÓN =====
                except httpx.ConnectTimeout:
                    logger.warning(f"Intento {attempt}: Timeout de conexión")
                    last_error_type = "EXCEPTION_CONNECT_TIMEOUT"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(
                            f"[{last_error_type}] Timeout de conexión agotado después de {MAX_RETRIES} intentos"
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Timeout de conexión después de {MAX_RETRIES} intentos",
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "estado": "error",
                                "codigo_error": "ConnectionTimeout",
                                "mensaje": "No se pudo conectar con el servicio.",
                                "detalles": "Por favor intenta nuevamente más tarde.",
                            },
                        )

                except httpx.ReadTimeout:
                    logger.warning(f"Intento {attempt}: Timeout de lectura")
                    last_error_type = "EXCEPTION_READ_TIMEOUT"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(
                            f"[{last_error_type}] Timeout de lectura agotado después de {MAX_RETRIES} intentos"
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Timeout de lectura después de {MAX_RETRIES} intentos",
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "estado": "error",
                                "codigo_error": "ReadTimeout",
                                "mensaje": "El servicio tardó demasiado en responder.",
                                "detalles": "Por favor intenta nuevamente más tarde.",
                            },
                        )

                except httpx.ConnectError as e:
                    logger.warning(f"Intento {attempt}: Error de conexión: {e}")
                    last_error_type = "EXCEPTION_CONNECT_ERROR"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(
                            f"[{last_error_type}] Error de conexión agotado después de {MAX_RETRIES} intentos: {str(e)}"
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error de conexión después de {MAX_RETRIES} intentos",
                        )
                        return JSONResponse(
                            status_code=502,
                            content={
                                "estado": "error",
                                "codigo_error": "ConnectionError",
                                "mensaje": "No se pudo alcanzar el servicio.",
                                "detalles": "Por favor intenta nuevamente en unos minutos.",
                            },
                        )

                except Exception as e:
                    logger.error(
                        f"Intento {attempt}: Error inesperado: {str(e)}", exc_info=True
                    )
                    last_error_type = f"EXCEPTION_GENERAL_{type(e).__name__}"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(
                            f"[{last_error_type}] Error general agotado después de {MAX_RETRIES} intentos: {str(e)}"
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error inesperado después de {MAX_RETRIES} intentos: {str(e)}",
                        )
                        return JSONResponse(
                            status_code=500,
                            content={
                                "estado": "error",
                                "codigo_error": "InternalError",
                                "mensaje": "Ocurrió un error interno.",
                                "detalles": "Por favor intenta nuevamente más tarde.",
                            },
                        )

            # ===== SI SE AGOTAN TODOS LOS REINTENTOS =====
            logger.error(
                f"Se agotaron todos los reintentos ({MAX_RETRIES}) para confirmar TOTP"
            )

            # Logging detallado del último error capturado
            if last_error_data and last_error_type:
                logger.error(
                    f"[ÚLTIMO ERROR CAPTURADO - {last_error_type}] "
                    f"Respuesta API: {last_error_data}"
                )
                # Extrae detalles específicos si están disponibles
                error_code = last_error_data.get("data", {}).get("code", "N/A")
                error_msg = last_error_data.get("data", {}).get("error", "N/A")
                logger.error(
                    f"[DETALLES DEL ERROR] "
                    f"Código: {error_code}, Mensaje: {error_msg}, Tipo: {last_error_type}"
                )

            await error_notify(
                method_name,
                client_id,
                f"Se agotaron todos los reintentos para confirmar TOTP",
            )
            return JSONResponse(
                status_code=500,
                content={
                    "estado": "error",
                    "codigo_error": "MaxRetriesExceeded",
                    "mensaje": "No se pudo confirmar el código después de varios intentos.",
                    "detalles": "Por favor solicita un nuevo código e intenta nuevamente.",
                },
            )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error general en confirmar_totp: {str(e)}", exc_info=True)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error general: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name, client_id, f"Error general en confirmar_totp: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Error interno al confirmar TOTP")


# ===== FUNCIÓN AUXILIAR PARA MANEJAR ERRORES TOTP =====
async def _handle_totp_error(
    error_code: str, error_msg: str, method_name: str, client_id: str
) -> JSONResponse:
    """
    Mapea los códigos de error de la API a mensajes personalizados en español.

    Args:
        error_code: Código de error desde la API (ej: "InvalidRequest")
        error_msg: Mensaje de error desde la API
        method_name: Nombre del método para logging
        client_id: ID del cliente para notificaciones

    Returns:
        JSONResponse con mensaje de error personalizado
    """
    # Determinar el HTTP status basado en el código de error
    status_code_map = {
        "InvalidRequest": 400,
        "ExpiredCode": 400,
        "MaxAttemptsExceeded": 429,
        "UserNotFound": 404,
        "UnauthorizedRequest": 403,
    }
    http_status = status_code_map.get(error_code, 412)

    # Obtener mensaje personalizado o usar el de la API
    error_info = ERRORES_TOTP.get(
        error_code,
        {
            "mensaje": f"Error al confirmar el código: {error_code}",
            "detalles": "Por favor intenta nuevamente o contacta con soporte.",
        },
    )
    error_traceback = traceback.format_exc()
    logger.error(f"Error TOTP - Código: {error_code}, Mensaje: {error_msg}")
    await error_notify(
        method_name, client_id, f"Error TOTP: {error_code} - {error_msg}"
    )
    await insertar_log(
        method_name=method_name,
        client_id=client_id,
        error_message=f"Error TOTP: {error_code} - {error_msg}",
        http_code=http_status,
        tipo="error",
        traceback_str=error_traceback,
    )
    return JSONResponse(
        status_code=http_status,
        content={
            "estado": "error",
            "codigo_error": error_code,
            "mensaje": error_info["mensaje"],
            "detalles": error_info["detalles"],
            "error_original": error_msg,
        },
    )


@app.get("/product-lines/{parent_id}")
async def webhook_product_lines(
    parent_id: str,
    name: Optional[str] = None,
    entity_id: Optional[str] = None,
    product_type: Optional[int] = None,
    tipo: Optional[int] = None,
):
    """
    Webhook asincrono para recuperar una linea de producto.
    Busca por parentId y tambien por un slug derivado de name/title para soportar cambios de version.
    Filtros opcionales: entity_id, product_type, tipo y archived=false por defecto.
    """
    MAX_RETRIES = 3  # numero maximo de intentos
    RETRY_DELAY = 5  # segundos entre intentos (base)
    TIMEOUT = 15.0  # timeout en segundos por solicitud

    method_name = "product-lines"
    parent_id_notify_error = f"parent_id para la busqueda del la linea={parent_id}"
    target_slug = slugify_nombre(name) if name else ""

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            access_token = await obtener_token(client)
            if not access_token:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "estado": "error",
                        "mensaje": MENSAJES_CLIENTE["error_conexion"],
                        "detalles_usuario": "No se pudo obtener el token de acceso interno.",
                    },
                )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": access_token,
            }

            # Servicio externo para listar lineas (configurado en BD)
            ext_client = None
            try:
                ext_client = await ExternalClient.from_code(
                    "KUENTA_LINES_LIST", client_id=parent_id
                )
            except ValueError:
                ext_client = None

            lines_data = []
            fetch_exitoso = False

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if ext_client:
                        # Inyectar variables dinámicas
                        ext_client.set_dynamic_values(
                            {
                                "ORG_ID": ORG_ID,
                                "Config-Organization-ID": ORG_ID,
                                "access_token": access_token,
                            }
                        )
                        # Asignar URL si no está definida
                        if not ext_client.url:
                            ext_client.set_url(API_URL)

                        response = await ext_client.run()

                        if not isinstance(response, dict):
                            raise Exception("Respuesta inválida del ExternalClient")

                        status_code = response.get("status", 500)
                        data = response.get("data", {})
                    else:
                        response = await client.get(API_URL, headers=headers)
                        status_code = response.status_code
                        data = response.json() if response.text else {}

                    if status_code in (200, 201):
                        fetch_exitoso = True
                        lines_data = (
                            data.get("data", {}).get("lines", [])
                            if ext_client
                            else data.get("lines", [])
                        )
                        break

                    # CASO TIMEOUT O ERROR DE SERVIDOR
                    elif status_code in (504, 503, 502, 500):
                        # notificaciones
                        await error_notify(
                            method_name,
                            parent_id_notify_error,
                            f"Error del servidor (HTTP {status_code}) en intento {attempt}",
                        )
                        await insertar_log(
                            method_name=method_name,
                            client_id=parent_id_notify_error,
                            error_message=f"Error del servidor (HTTP {status_code}) en intento {attempt}",
                            http_code=status_code,
                            tipo="error",
                        )

                        logger.warning(
                            f"Intento {attempt}: Kuenta no responde (HTTP {status_code})."
                        )
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue

                    # ERROR 401/403 (AUTH) O 400 (CLIENTE)
                    else:
                        # notificaciones
                        await error_notify(
                            method_name,
                            parent_id_notify_error,
                            f"Error HTTP {status_code} en API externa: {data}",
                        )

                        await insertar_log(
                            method_name=method_name,
                            client_id=parent_id_notify_error,
                            error_message=f"Error HTTP {status_code} en API externa: {data}",
                            http_code=status_code,
                            tipo="error",
                        )

                        logger.error(f"Error HTTP {status_code} en API externa: {data}")
                        if attempt < MAX_RETRIES and status_code == 401:
                            # Podrías renovar el token aquí si quisieras
                            await asyncio.sleep(RETRY_DELAY)
                            continue
                        raise HTTPException(
                            status_code=502,  # Es un 502 Bad Gateway porque el proveedor falló
                            detail={
                                "estado": "error",
                                "mensaje": MENSAJES_CLIENTE["error_servicio"],
                            },
                        )

                except (httpx.TimeoutException, httpx.RequestError, Exception) as e:
                    # notifiaciones
                    await error_notify(
                        method_name,
                        parent_id_notify_error,
                        f"Error de red en intento {attempt}: {str(e)}",
                    )
                    await insertar_log(
                        method_name=method_name,
                        client_id=parent_id_notify_error,
                        error_message=f"Error de red en intento {attempt}: {str(e)}",
                        http_code=500,
                        tipo="error",
                        traceback_str=traceback.format_exc(),
                    )

                    logger.warning(f"Intento {attempt} fallido por error de red: {e}")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue

            if not fetch_exitoso:
                msg = f"Timeout persistente o caída al consultar API externa tras {MAX_RETRIES} intentos."
                logger.error(msg)
                await error_notify(method_name, parent_id, msg)

                raise HTTPException(
                    status_code=504,  # Gateway Timeout
                    detail={
                        "estado": "error",
                        "mensaje": MENSAJES_CLIENTE["error_conexion"],
                        "detalles_usuario": "El servicio tardó demasiado en responder. Intenta más tarde.",
                    },
                )

            # LÓGICA DE FILTRADO Y BÚSQUEDA

            candidates = []
            for line in lines_data:
                if line.get("archived"):
                    continue
                if entity_id and line.get("entityID") != entity_id:
                    continue
                if product_type is not None and line.get("productType") != product_type:
                    continue
                if tipo is not None and line.get("type") != tipo:
                    continue

                parent_match = line.get("parentId") == parent_id
                slug_name = slugify_nombre(line.get("name", ""))
                slug_title = slugify_nombre(line.get("title", ""))
                slug_match = bool(target_slug) and (
                    slug_name == target_slug or slug_title == target_slug
                )

                if parent_match or slug_match:
                    candidates.append(
                        {
                            "line": line,
                            "matched_by": "parentId" if parent_match else "slug",
                        }
                    )

            #  Rescate por coincidencia parcial si no hay exactos
            if not candidates and target_slug:
                for line in lines_data:
                    if line.get("archived") or (
                        entity_id and line.get("entityID") != entity_id
                    ):
                        continue
                    slug_name = slugify_nombre(line.get("name", ""))
                    slug_title = slugify_nombre(line.get("title", ""))

                    if target_slug in slug_name or target_slug in slug_title:
                        candidates.append({"line": line, "matched_by": "partial-slug"})

            #  No encontramos la línea
            if not candidates:
                sugerencias = [slugify_nombre(l.get("name", "")) for l in lines_data][
                    :10
                ]
                # notificaciones
                await error_notify(
                    method_name,
                    parent_id_notify_error,
                    f"Línea de producto no encontrada para parent_id={parent_id} con name='{name}' y entity_id='{entity_id}'. Sugerencias: {sugerencias}",
                )
                await insertar_log(
                    method_name=method_name,
                    client_id=parent_id_notify_error,
                    error_message=f"Línea de producto no encontrada para parent_id={parent_id} con name='{name}' y entity_id='{entity_id}'. Sugerencias: {sugerencias}",
                    http_code=404,
                    tipo="error",
                )
                # Lanzamos un 404 (Not Found) real
                raise HTTPException(
                    status_code=404,
                    detail={
                        "estado": "error",
                        "mensaje": "Línea de producto no encontrada.",
                        "detalles_usuario": "Verifica el nombre o ID ingresado.",
                        "sugerencias": sugerencias,
                    },
                )

            # ORDENAR Y RETORNAR EL MEJOR CANDIDATO

            def parse_updated(val: str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min

            candidates.sort(
                key=lambda item: (
                    parse_updated(item["line"].get("updatedAt", "")),
                    item["line"].get("version", 0),
                ),
                reverse=True,
            )

            selected = candidates[0]
            line = selected["line"]

            logger.info(
                f"Línea encontrada vía {selected['matched_by']}: {line.get('id')}"
            )

            # Este SÍ es un 200 OK legítimo
            return {
                "id": line.get("id"),
                "name": line.get("name"),
                "title": line.get("title"),
                "parentId": line.get("parentId"),
                "version": line.get("version"),
                "matched_by": selected["matched_by"],
                "principalMin": line.get("principalMin"),
                "principalMax": line.get("principalMax"),
                "timeMin": line.get("timeMin"),
                "timeMax": line.get("timeMax"),
                "timeDefault": line.get("timeDefault"),
                "paymentFrequency": line.get("paymentFrequency"),
            }

    # MANEJO GLOBAL DE ERRORES PARA EL ENDPOINT

    except HTTPException as http_exc:
        # Pasa de largo las excepciones controladas para que FastAPI ponga el código HTTP correcto
        # notificaciones
        await error_notify(
            method_name,
            parent_id_notify_error,
            f"HTTPException lanzada: {http_exc.detail}",
        )
        await insertar_log(
            method_name=method_name,
            client_id=parent_id_notify_error,
            error_message=f"HTTPException lanzada: {http_exc.detail}",
            http_code=http_exc.status_code,
            tipo="error",
        )
        raise http_exc

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error general en webhook_product_lines: {e}", exc_info=True)
        await error_notify(method_name, parent_id, f"Error general: {e}")
        await insertar_log(
            method_name=method_name,
            client_id=parent_id,
            error_message=str(e),
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_general"],
                "detalles_usuario": "Ocurrió un error inesperado buscando las líneas. Intenta más tarde.",
            },
        )


def format_currency(value: float) -> str:
    """Formatea un número como moneda COP: sin decimales, con $ y separadores de miles."""
    return f"${value:,.0f}"


# ===== FUNCIÓN AUXILIAR: EXTRAER INFORMACIÓN DE CAMPOS FALTANTES =====
def extract_missing_fields_info(missing_fields: list) -> dict:
    """
    Extrae información legible de los campos faltantes en la respuesta de Kuenta.

    Retorna un diccionario con:
    - total: Cantidad total de campos faltantes
    - required_count: Campos obligatorios faltantes
    - optional_count: Campos opcionales faltantes
    - required_labels: Lista de etiquetas de campos obligatorios
    - optional_labels: Lista de etiquetas de campos opcionales
    - by_category: Campos agrupados por categoría
    """
    required_labels = []
    optional_labels = []
    by_category = {}

    for field in missing_fields:
        label = field.get("label", "Campo desconocido")
        is_required = field.get("required", False)
        category = field.get("field", {}).get("category", 0)

        # Mapear categoría a nombre legible
        category_name = {
            1: "Documentos",
            3: "Identificación",
            4: "Contacto",
            7: "Económico",
        }.get(category, "Otro")

        # Crear información del campo
        field_info = {
            "label": label,
            "type": field.get("field", {}).get("type", "text"),
            "id": field.get("ID"),
        }

        # Inicializar categoría si no existe
        if category_name not in by_category:
            by_category[category_name] = {"required": [], "optional": []}

        # Clasificar como obligatorio u opcional
        if is_required:
            required_labels.append(label)
            by_category[category_name]["required"].append(field_info)
        else:
            optional_labels.append(label)
            by_category[category_name]["optional"].append(field_info)

    return {
        "total": len(missing_fields),
        "required_count": len(required_labels),
        "optional_count": len(optional_labels),
        "required_labels": required_labels,
        "optional_labels": optional_labels,
        "by_category": by_category,
    }


# Endpoint para crear un nuevo payable o crédito después de la simulación


# ===== FUNCIÓN AUXILIAR: EXTRAER INFORMACIÓN DE CAMPOS FALTANTES =====
def extract_missing_fields_info(missing_fields: list) -> dict:
    """
    Extrae información legible de los campos faltantes en la respuesta de Kuenta.
    """
    required_labels = []
    optional_labels = []
    by_category = {}

    for field in missing_fields:
        label = field.get("label", "Campo desconocido")
        is_required = field.get("required", False)
        category = field.get("field", {}).get("category", 0)

        category_name = {
            1: "Documentos",
            3: "Identificación",
            4: "Contacto",
            7: "Económico",
        }.get(category, "Otro")

        field_info = {
            "label": label,
            "type": field.get("field", {}).get("type", "text"),
            "id": field.get("ID"),
        }

        if category_name not in by_category:
            by_category[category_name] = {"required": [], "optional": []}

        if is_required:
            required_labels.append(label)
            by_category[category_name]["required"].append(field_info)
        else:
            optional_labels.append(label)
            by_category[category_name]["optional"].append(field_info)

    return {
        "total": len(missing_fields),
        "required_count": len(required_labels),
        "optional_count": len(optional_labels),
        "required_labels": required_labels,
        "optional_labels": optional_labels,
        "by_category": by_category,
    }


def extraer_campos_faltantes(kuenta_response: dict) -> dict:
    """
    Parsea la respuesta 400 de Kuenta y extrae los campos faltantes
    para armar un mensaje dinámico y masticable para el frontend.
    """
    data = kuenta_response.get("data", {})
    missing_fields_raw = data.get("missingFields", [])

    campos_faltantes = []

    for item in missing_fields_raw:
        if item.get("required") is True:
            field_info = item.get("field", {})
            nombre_tecnico = field_info.get("name", "desconocido")
            nombre_amigable = field_info.get("label", item.get("label", nombre_tecnico))
            campos_faltantes.append(nombre_amigable)

    mensaje_directo = kuenta_response.get("message", "Error de validación")
    mensaje_front = (
        f"Faltan completar estos datos: {', '.join(campos_faltantes)}."
        if campos_faltantes
        else "Faltan datos en tu perfil."
    )

    return {
        "mensaje_crudo": mensaje_directo,
        "nombres_amigables": campos_faltantes,
        "mensaje_frontend": mensaje_front,
    }


@app.post("/payables/{client_id}")
async def create_payable(client_id: str, payload: PayableRequest):
    """
    Endpoint para crear un nuevo payable con manejo inteligente de errores:
    1. POST a Kuenta para crear payable (3 reintentos)
    2. Detecta perfil incompleto → HTTP 409
    3. Detecta errores de conexión → HTTP 502
    4. Si éxito (201) → GET para obtener simulación
    5. Procesa y retorna datos formateados
    """
    method_name = "create_payable"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            logger.info(f"Iniciando creación de payable para cliente: {client_id}")
            logger.info(f"   creditLineId: {payload.creditLineId}")
            logger.info(f"   principal: ${payload.principal:,.2f}")
            logger.info(f"   initialFee: ${payload.initialFee:,.2f}")
            logger.info(f"   time: {payload.time} días")

            principal = payload.principal
            initial_fee = payload.initialFee

            token = await obtener_token(client)

            # Construir payload para Kuenta
            new_payload = {
                "creditLineId": payload.creditLineId,
                "principal": principal,
                "time": payload.time,
                "disbursementMethod": payload.disbursementMethod,
                "initialFee": initial_fee,
                "paymentFrequency": payload.paymentFrequency,
                "source": payload.source,
                "redirectUrl": payload.redirectUrl,
                "callbackUrl": payload.callbackUrl,
                "meta": payload.meta,
            }

            # Eliminar campos None
            new_payload = {k: v for k, v in new_payload.items() if v is not None}

            logger.info(f"Payload para Kuenta: {new_payload}")

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": client_id,
                "Authorization": token,
            }

            # Servicio externo para crear payable (configurado en BD)
            ext_client_post = None
            try:
                # Intentar cargar configuración de servicio externo consultando la base de datos
                ext_client_post = await ExternalClient.from_code(
                    "KUENTA_PAYABLE_CREATE", client_id=client_id
                )
            except ValueError:
                ext_client_post = None

            # ===== REINTENTOS PARA POST PAYABLE =====
            max_retries = 3
            response_credit_id = None
            # last_error_response = None

            for attempt in range(max_retries):
                try:
                    logger.info(
                        f"Intento {attempt + 1}/{max_retries}: POST a {PAYABLE_URL}"
                    )
                    response_data = {}

                    # valida las configuraciones del servicio externo que se obtuvieron de la base de datos
                    if ext_client_post:
                        # Inyectar variables dinámicas ANTES de ejecutar
                        # IMPORTANTE: Estas variables se usarán tanto para headers como para body
                        dynamic_vars = {
                            "creditLineId": payload.creditLineId,  # Corregido: minúscula
                            "principal": principal,
                            "time": payload.time,
                            "paymentFrequency": payload.paymentFrequency,
                            "initialFee": initial_fee,
                            "disbursementMethod": payload.disbursementMethod,
                            "source": payload.source,
                            "redirectUrl": payload.redirectUrl,
                            "callbackUrl": payload.callbackUrl,
                            "meta": payload.meta,
                        }

                        logger.info(f"Variables dinámicas a inyectar: {dynamic_vars}")
                        ext_client_post.set_dynamic_values(dynamic_vars)

                        # header dinámicos
                        ext_client_post.set_headers(headers)

                        # Asignar URL si no está definida en la configuración
                        if not ext_client_post.url:
                            ext_client_post.set_url(PAYABLE_URL)

                        # Ejecutar el servicio externo
                        response = await ext_client_post.run()

                        if not isinstance(response, dict):
                            raise Exception("Respuesta invalida del servicio externo")
                        status_code = response.get("status", 500)
                        response_data = response.get("data") or "no se accedio a data"
                        if not isinstance(response_data, dict):
                            response_data = {}
                    else:
                        response = await client.post(
                            PAYABLE_URL, json=new_payload, headers=headers
                        )
                        response_data = response.json()
                        status_code = response.status_code

                    # logger.info(f"   Response Data: {str(response_data)}")

                    # ===== CASO 1: ÉXITO (200 o 201) =====
                    if status_code in (200, 201):
                        credit = response_data.get("data", {}).get("credit", {})
                        response_credit_id = credit.get("ID")
                        logger.info(
                            f"Response: HTTP {status_code}, \n ID del credito: {response_credit_id}"
                        )
                        break  # ¡Salió bien! Rompemos el ciclo.

                    # ===== CASO 2: ERROR DEL CLIENTE O REGLA DE NEGOCIO (400, 409, 422) =====
                    elif status_code in (400, 409, 422):
                        error_code = response_data.get("data", {}).get(
                            "code", "Unknown"
                        )
                        error_message = response_data.get("data", {}).get(
                            "message", "Unknown"
                        )

                        logger.error(
                            f"HTTP {status_code} - Code: {error_code}, Msg: {error_message}"
                        )

                        # Lógica de traducción para el Frontend
                        mensaje_amigable = MENSAJES_CLIENTE["error_datos"]
                        detalles_usuario = error_message

                        # Si es IncompleteProfile, usamos nuestra navaja suiza utilitaria
                        if error_code == "IncompleteProfile":
                            mensaje_amigable = MENSAJES_CLIENTE["perfil_incompleto"]
                            analisis = extraer_campos_faltantes(response_data)
                            detalles_usuario = analisis["mensaje_frontend"]

                            logger.error(
                                f"HTTP {status_code} Error: code={error_code}. Mensaje: {error_message}"
                            )

                            await error_notify(
                                method_name,
                                client_id,
                                f"Error {status_code}: {error_message}, el cliente tiene el perfil incompleto, id del cliente: {client_id}",
                            )

                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"Error HTTP {status_code}: {error_message}",
                                http_code=status_code,
                                tipo="error",
                                traceback_str=None,
                            )

                            return JSONResponse(
                                status_code=200,  # Le decimos 200 OK para que el nodo pase feliz
                                content={
                                    "estado": "error",  # Pero aquí le decimos la cruda verdad
                                    "http_real": status_code,  # El código real del error para que el frontend pueda reaccionar
                                    "codigo_tecnico": error_code,
                                    "mensaje": mensaje_amigable,
                                    "detalles_usuario": detalles_usuario,
                                },
                            )

                        # # FAIL FAST: NO TIENE SENTIDO REINTENTAR ESTO.
                        # # Lanzamos la excepción de una vez para que el usuario corrija los datos.
                        # raise HTTPException(
                        #     status_code=400,
                        #     detail={
                        #         "estado": "error",
                        #         "codigo_tecnico": error_code,
                        #         "mensaje": mensaje_amigable,
                        #         "detalles_usuario": detalles_usuario
                        #     }
                        # )

                    # ===== CASO 3: ERROR DE AUTENTICACIÓN (401) =====
                    elif status_code in (401, 403):
                        logger.warning(
                            "Error de autorización. Intentando renovar token..."
                        )
                        await insertar_log(
                            method_name=method_name,
                            client_id=client_id,
                            error_message="Error de autorización",
                            http_code=status_code,
                            tipo="error",
                            traceback_str=None,
                        )

                        if attempt < max_retries - 1:
                            token = await obtener_token(client)
                            headers["Authorization"] = token
                            if ext_client_post:
                                ext_client_post.set_headers(headers)
                            await asyncio.sleep(2**attempt)
                            continue  # Reintentamos con el nuevo token
                        else:
                            await error_notify(
                                method_name,
                                client_id,
                                "Fallo de autorización tras 3 intentos",
                            )
                            raise HTTPException(
                                status_code=401,
                                detail="Fallo de autorización tras 3 intentos",
                            )

                    # ===== CASO 4: ERRORES DE SERVIDOR O DESCONOCIDOS (5XX) =====
                    else:
                        logger.error(
                            f"Error del servidor Kuenta HTTP {status_code}: {response_data}"
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error del servidor Kuenta HTTP {status_code}: {response_data}",
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2**attempt)
                            continue

                except httpx.TimeoutException as e:
                    if attempt < max_retries - 1:
                        wait_time = 2**attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)

                except (httpx.TimeoutException, httpx.RequestError) as e:
                    # Errores puros de red. Los registramos y reintentamos.
                    error_traceback = traceback.format_exc()
                    logger.error(f"Error de red en intento {attempt + 1}: {str(e)}")
                    await error_notify(
                        method_name,
                        client_id,
                        "Timeout en API Kuenta, excepción: " + str(e),
                    )
                    # insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=client_id,
                        error_message=f"Timeout en API Kuenta, excepción: {str(e)}",
                        http_code=504,
                        tipo="error",
                        traceback_str=error_traceback,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                        continue

            # Si el ciclo for terminó y no obtuvimos un ID, significa que se agotaron los reintentos
            # y todo falló. Matamos el proceso aquí para no hacer un GET a `/payables/None`.
            if not response_credit_id:
                raise HTTPException(
                    status_code=status_code or 500,
                    detail={
                        "estado": "error",
                        "codigo": error_code,
                        "detalles_usuario": error_message,
                    },
                )

            # realizar consulta inmediata para obtener la simulación del crédito creado
            logger.info(
                f"Payable creado con ID: {response_credit_id}, procediendo a obtener simulación..."
            )

            # ===== LOOP DE REINTENTOS PARA GET SIMULACIÓN (MÁXIMO 2 INTENTOS) =====
            max_retries_simulacion = 2
            simulacion_data = {}
            status_code_simulacion = None

            # Tratamos de armar el cliente externo para el GET
            ext_client_get = None
            try:
                ext_client_get = await ExternalClient.from_code(
                    "KUENTA_PAYABLE_GET", client_id=client_id
                )
            except ValueError:
                pass

            for attempt_get in range(max_retries_simulacion):
                try:
                    if ext_client_get:
                        # Armamos la ruta correcta inyectando el ID del crédito
                        ext_client_get.set_path(f"/{response_credit_id}")
                        ext_client_get.set_headers(headers)
                        if not ext_client_get.url:
                            ext_client_get.set_url(
                                f"{PAYABLE_URL}"
                            )  # Asegurar URL base

                        res_simulacion = await ext_client_get.run()

                        if not isinstance(res_simulacion, dict):
                            raise Exception("Respuesta inválida en simulación externa")

                        status_code_simulacion = res_simulacion.get("status", 500)
                        simulacion_data = res_simulacion.get("data", {})
                    else:
                        res_simulacion = await client.get(
                            f"{PAYABLE_URL}/{response_credit_id}", headers=headers
                        )
                        status_code_simulacion = res_simulacion.status_code
                        simulacion_data = (
                            res_simulacion.json() if res_simulacion.text else {}
                        )

                    # Éxito en la simulación
                    if status_code_simulacion in (200, 201):
                        logger.info(
                            f"GET simulación exitoso en intento {attempt_get + 1}"
                        )
                        break

                    # Error en la simulación, dormimos 5s y reintentamos (tal vez Kuenta no la generó tan rápido)
                    else:
                        logger.warning(
                            f"GET simulación status {status_code_simulacion}. Reintentando..."
                        )
                        if attempt_get < max_retries_simulacion - 1:
                            await asyncio.sleep(5)
                            continue

                except (httpx.TimeoutException, httpx.RequestError, Exception) as e:
                    logger.error(f"Fallo de conexión en GET simulación: {str(e)}")
                    if attempt_get < max_retries_simulacion - 1:
                        await asyncio.sleep(5)
                        continue

            if status_code_simulacion not in (200, 201) or not simulacion_data:
                await error_notify(
                    method_name, client_id, "Falló obtención de simulación"
                )
                raise HTTPException(
                    status_code=502,
                    detail={
                        "estado": "error",
                        "mensaje": MENSAJES_CLIENTE["error_simulacion"],
                        "detalles_usuario": "No pudimos descargar tu tabla de pagos.",
                    },
                )

            logger.info(f"Status code de la simulación: {status_code_simulacion} \n")

            # ===== PROCESAR RESPUESTA EXITOSA DE SIMULACIÓN =====
            try:
                # Extraer datos de crédito de la respuesta
                credits_data = simulacion_data.get("data", {}).get("credit")
                # tipo de dato puede ser lista o diccionario
                logger.info(
                    f"Tipo de dato de credits en la simulación: {type(credits_data)}"
                )
                if isinstance(credits_data, list):
                    if not credits_data:
                        raise ValueError("Empty credits list in simulation response")
                    credit_data = credits_data[0]
                elif isinstance(credits_data, dict):
                    credit_data = credits_data
                else:
                    logger.error(f"Formato inesperado de credits: {type(credits_data)}")
                    raise ValueError("Unexpected credits format in simulation response")

                # logger.info(f"Datos del crédito obtenidos: {credit_data} \n")

                # Los installments están dentro de cada crédito
                installments = credit_data.get("installments", [])

                # logger.info(f"Installments obtenidas: {installments} \n")
                cuota_inicial = credit_data.get("initialFee")
                ID_credito = credit_data.get("ID")
                logger.info(f"ID del crédito obtenido: {ID_credito} \n")
                referencia_credito = credit_data.get("reference")
                id_cliente = credit_data.get("debtorID")

                if not installments:
                    logger.error("No se encontraron installments en la respuesta")
                    await error_notify(
                        method_name,
                        client_id,
                        "No se encontraron cuotas en la simulación",
                    )
                    raise HTTPException(
                        status_code=404,
                        detail="No se encontraron cuotas en la simulación",
                    )

                # Tomar el primer installment
                first_installment = installments[0]

                # Extraer y redondear valores
                payment = round(float(first_installment.get("payment", 0)))
                capital = round(float(first_installment.get("capital", 0)))
                interest = round(float(first_installment.get("interest", 0)))
                costs = round(float(first_installment.get("costs", 0)))
                taxes = round(float(first_installment.get("taxes", 0)))
                # cuota inicial redondeada
                cuota_inicial_rounded = round(float(cuota_inicial))

                # Formatear valores para lectura humana
                formatted_values = {
                    "payment_formatted": f"${payment:,}",
                    "capital_formatted": f"${capital:,}",
                    "interest_formatted": f"${interest:,}",
                    "costs_formatted": f"${costs:,}",
                    "taxes_formatted": f"${taxes:,}",
                    "cuota_inicial_formatted": f"${cuota_inicial_rounded:,}",
                }

                # Agregar valores originales y formateados a la respuesta
                response_data = {
                    "status": "success",
                    "ID del credito creado": response_credit_id,
                    "valores_originales": {
                        "payment": payment,
                        "capital": capital,
                        "interest": interest,
                        "costs": costs,
                        "taxes": taxes,
                    },
                    "valores_formateados": formatted_values,
                }

                logger.info("Valores extraidos y formateados exitosamente")
                logger.info(f"Valores formateados: {formatted_values}")
                # Cacheamos las cuotas simuladas para servirlas rapido en /detalle_cuota_vencida
                if id_cliente and installments:
                    cuotas_cache[id_cliente] = {
                        "cuotas": installments,
                        "timestamp": datetime.now(timezone.utc),
                    }

                # Notificación informativa
                info_message = (
                    f"Crédito creado y registrado en kuenta correctamente\n"
                    f"ID del crédito: {ID_credito}\n"
                    f"Referencia del crédito: {referencia_credito}\n"
                    f"ID del cliente: {id_cliente}\n"
                    f"Valor total crédito: {formatted_values['payment_formatted']}"
                )

                # envia notificacion informativa (email + telegram) con id para seguimiento
                await info_notify(
                    method_name, client_id, info_message, entity_id=str(id_cliente)
                )

                return response_data

            except Exception as e:
                # Este bloque catch atrapa si la data de Kuenta cambió y nos rompe la extracción
                logger.error(f"Error procesando JSON de simulación: {str(e)}")

                logger.error(f"Error extrayendo datos de simulación: {str(e)}")
                await error_notify(
                    method_name,
                    client_id,
                    f"Error procesando respuesta de simulación: {str(e)}",
                )
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=f"Error procesando respuesta de simulación: {str(e)}",
                    http_code=500,
                    tipo="error",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error mapeando datos de simulación: {str(e)}",
                )

    # ==========================================================
    # MANEJADORES GLOBALES DE EXCEPCIONES
    # ==========================================================
    #  MUY IMPORTANTE: Si ya lanzamos un HTTPException arriba (ej. el del error 400),
    # debemos dejar que pase de largo, si no, caerá en el 'except Exception' y se volverá un 500
    except HTTPException as http_exc:
        # notificaciones
        await error_notify(
            method_name, client_id, f"HTTPException lanzada: {http_exc.detail}"
        )
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"HTTPException lanzada: {http_exc.detail}",
            http_code=http_exc.status_code,
            tipo="error",
        )
        return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)

    except ValueError as e:
        logger.error(f"Error de conversión: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conversión: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=str(e),
            http_code=400,
            tipo="error",
        )
        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_datos"],
                "detalles_usuario": "Se detectó un valor numérico incorrecto en el proceso.",
            },
        )
    except Exception as e:
        # El catch-all final por si se nos cuela un IndexError, AttributeError, etc no previsto.
        error_traceback = traceback.format_exc()
        logger.error(f"Catástrofe interna: {str(e)}", exc_info=True)
        await error_notify(method_name, client_id, f"Error inesperado: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=str(e),
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_general"],
                "detalles_usuario": "El equipo técnico ya fue notificado. Disculpa las molestias.",
            },
        )


@app.post(
    "/payables/{id_credito_mora}/installments/{id_cuota_pendiente}/orders",
    tags=["Payables"],
    summary="Crear orden de pago de una cuota",
)
async def create_installment_order(
    id_credito_mora: str,
    id_cuota_pendiente: str,
    payload: InstallmentOrderCreateRequest,
):
    method_name = "create_installment_order"
    client_id = id_credito_mora

    payables_base_url = (
        PAYABLE_URL.rstrip("/")
        if PAYABLE_URL and "payables" in PAYABLE_URL.lower()
        else "https://api.kuenta.co/v1/payables"
    )
    external_url = f"{payables_base_url}/{id_credito_mora}/installments/{id_cuota_pendiente}/orders"
    outbound_payload = {
        "amount": payload.amount,
        "provider": payload.provider,
        "date": obtener_fecha_iso_bogota(),
        "collectionCosts": payload.collectionCosts,
    }

    logger.info(
        f"Creando orden de cuota. credit_id={id_credito_mora}, "
        f"installment_id={id_cuota_pendiente}, url={external_url}, "
        f"payload={outbound_payload}"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                token = await obtener_token(client)
            except httpx.HTTPStatusError as e:
                auth_status = e.response.status_code if e.response else 500
                error_message = f"Error HTTP obteniendo token: {str(e)}"
                logger.error(error_message)
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=error_message,
                    http_code=auth_status,
                    tipo="error",
                )
                return JSONResponse(
                    status_code=401 if auth_status in (401, 403) else 500,
                    content={"error": "No se pudo obtener el token de autenticacion"},
                )
            except Exception as e:
                error_message = f"Error obteniendo token: {str(e)}"
                logger.error(error_message)
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=error_message,
                    http_code=500,
                    tipo="error",
                )
                return JSONResponse(
                    status_code=500,
                    content={"error": "No se pudo obtener el token de autenticacion"},
                )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": token,
            }

            response = await client.post(
                external_url, json=outbound_payload, headers=headers
            )

            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw_response": response.text}

            log_type = "info" if response.status_code < 400 else "error"
            log_message = (
                f"Respuesta Kuenta crear orden cuota. "
                f"status={response.status_code}, url={external_url}"
            )
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=log_message,
                http_code=response.status_code,
                tipo=log_type,
                payload_enviado=str(outbound_payload),
                respuesta_api=str(response_data),
            )

            return JSONResponse(
                status_code=response.status_code,
                content=jsonable_encoder(response_data),
            )
    except httpx.TimeoutException as e:
        error_message = f"Timeout al crear orden de cuota: {str(e)}"
        logger.error(error_message)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=error_message,
            http_code=504,
            tipo="error",
            traceback_str=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=504, content={"error": "Timeout al consumir la API de Kuenta"}
        )
    except httpx.RequestError as e:
        error_message = f"Error de conexion al crear orden de cuota: {str(e)}"
        logger.error(error_message)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=error_message,
            http_code=502,
            tipo="error",
            traceback_str=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=502, content={"error": "Error de conexion con la API de Kuenta"}
        )
    except Exception as e:
        error_message = f"Error inesperado al crear orden de cuota: {str(e)}"
        logger.error(error_message, exc_info=True)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=error_message,
            http_code=500,
            tipo="error",
            traceback_str=traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Error interno al crear la orden de cuota"},
        )


# confirmar credito calculado y creado en simulacion usando peticion payables
@app.post(
    "/confirmar-credito/{credit_id}",
    tags=["Payables"],
    summary="Confirmar crédito/payable",
)
async def confirm_payable(credit_id: str):
    """
    Endpoint para confirmar un crédito existente en Kuenta.

    Realiza una petición PATCH a la API externa:
    https://api.kuenta.co/v1/payables/{credit_id}/confirm

    Parámetros:
    - credit_id: ID del crédito a confirmar (se pasa en la URL)

    Retorna:
    - Status 200: {"estado": "success", "mensaje": "Crédito confirmado exitosamente", "data": {...}}
    - Status 401: {"estado": "error", "mensaje": "Error de autenticación"}
    - Status 404: {"estado": "error", "mensaje": "Crédito no encontrado"}
    - Status 500: {"estado": "error", "mensaje": "Error interno"}

    Ejemplo de uso:
    PATCH /payables/8c082794-796c-4987-ac28-e4918bea590d/confirm
    """
    method_name = "confirm_payable"

    try:
        logger.info(f"Iniciando confirmación del payable: {credit_id}")

        # Validación del ID del crédito
        if not credit_id or not credit_id.strip():
            logger.warning("ID de crédito vacío o inválido")
            await error_notify(method_name, credit_id, "ID de crédito vacío")
            await insertar_log(
                method_name=method_name,
                client_id=credit_id,
                error_message="ID de crédito vacío o inválido",
                http_code=400,
                tipo="error",
            )
            return JSONResponse(
                status_code=400,
                content={
                    "estado": "error",
                    "mensaje": "El ID del crédito no puede estar vacío",
                    "detalles": "Por favor proporciona un ID válido de crédito",
                },
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Obtener token de autenticación
            access_token = await obtener_token(client)
            if not access_token:
                logger.error("No se pudo obtener el token de acceso")
                await error_notify(
                    method_name, credit_id, "No se pudo obtener token de acceso"
                )
                await insertar_log(
                    method_name=method_name,
                    client_id=credit_id,
                    error_message="No se pudo obtener token de acceso",
                    http_code=401,
                    tipo="error",
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "estado": "error",
                        "codigo_error": "AuthenticationError",
                        "mensaje": "No se pudo obtener el token de acceso",
                        "detalles": "Por favor intenta nuevamente más tarde",
                    },
                )

            # Construir headers
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": access_token,
            }

            # Construir URL final
            confirm_url = f"{PAYABLE_URL.rstrip('/')}/{credit_id}/confirm"
            logger.info(f"URL de confirmación: {confirm_url}")

            # Intentar cargar servicio externo desde BD (opcional)
            ext_client_confirm = None
            try:
                ext_client_confirm = await ExternalClient.from_code(
                    "KUENTA_PAYABLE_CONFIRM", client_id=credit_id
                )
                logger.info(f"Servicio externo KUENTA_PAYABLE_CONFIRM cargado desde BD")
            except ValueError:
                ext_client_confirm = None
                logger.info("Usando configuración por defecto para confirmación")

            # ===== REINTENTOS PARA PATCH CONFIRM =====
            max_retries = 3
            last_error_response = None

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(
                        f"Intento {attempt}/{max_retries}: PATCH a {confirm_url}"
                    )

                    response_data = {}
                    status_code = 500

                    # Usar servicio externo si existe
                    if ext_client_confirm:
                        # Inyectar variables dinámicas
                        ext_client_confirm.set_dynamic_values(
                            {
                                "ORG_ID": ORG_ID,
                                "access_token": access_token,
                                "credit_id": credit_id,
                            }
                        )

                        # Asignar URL si no está definida
                        if not ext_client_confirm.url:
                            ext_client_confirm.set_url(confirm_url)

                        logger.info(
                            f"Ejecutando servicio externo KUENTA_PAYABLE_CONFIRM"
                        )
                        response = await ext_client_confirm.run()

                        if not isinstance(response, dict):
                            raise Exception("Respuesta inválida del servicio externo")

                        status_code = response.get("status", 500)
                        response_data = response.get("data") or {}

                        if not isinstance(response_data, dict):
                            response_data = {}

                    else:
                        # Fallback a httpx directo (método PATCH)
                        logger.info(f"Usando cliente HTTP directo para PATCH")
                        response = await client.patch(confirm_url, headers=headers)
                        status_code = response.status_code

                        try:
                            response_data = response.json()
                        except Exception as json_err:
                            logger.warning(f"No se pudo parsear JSON: {json_err}")
                            response_data = {"raw_response": response.text}

                    logger.info(
                        f"Respuesta del servidor (status {status_code}): {response_data}"
                    )

                    # ===== CASO EXITOSO: HTTP 200 OK =====
                    if status_code == 200:
                        logger.info(
                            f"✅ Payable confirmado exitosamente en intento {attempt}"
                        )

                        # Notificación informativa
                        info_message = (
                            f"Crédito confirmado exitosamente en Kuenta\n"
                            f"ID del crédito: {credit_id}\n"
                            f"Fecha: {datetime.now(timezone.utc).isoformat()}"
                        )
                        await info_notify(
                            method_name, credit_id, info_message, entity_id=credit_id
                        )

                        # Log de éxito
                        await insertar_log(
                            method_name=method_name,
                            client_id=credit_id,
                            error_message=f"Crédito confirmado exitosamente",
                            http_code=200,
                            tipo="info",
                        )

                        return JSONResponse(
                            status_code=200,
                            content={
                                "estado": "success",
                                "mensaje": "Crédito confirmado exitosamente",
                                "credit_id": credit_id,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "data": response_data,
                            },
                        )

                    # ===== CASO 404: CRÉDITO NO ENCONTRADO =====
                    elif status_code == 404:
                        logger.error(f"Crédito no encontrado: {credit_id}")
                        error_traceback = traceback.format_exc()

                        await error_notify(
                            method_name,
                            credit_id,
                            f"Crédito no encontrado en API: {credit_id}",
                        )

                        await insertar_log(
                            method_name=method_name,
                            client_id=credit_id,
                            error_message=f"Crédito no encontrado: {response_data}",
                            http_code=404,
                            tipo="error",
                            traceback_str=error_traceback,
                        )

                        return JSONResponse(
                            status_code=404,
                            content={
                                "estado": "error",
                                "codigo_error": "CreditNotFound",
                                "mensaje": "El crédito no fue encontrado en el sistema",
                                "credit_id": credit_id,
                                "detalles": "Verifica que el ID del crédito sea correcto",
                            },
                        )

                    # ===== CASO 403: FORBIDDEN (TOKEN INVÁLIDO) =====
                    elif status_code == 403:
                        logger.error(f"Error 403: Acceso prohibido o token inválido")
                        error_traceback = traceback.format_exc()
                        last_error_response = response_data

                        await error_notify(
                            method_name,
                            credit_id,
                            f"Error 403: Token inválido o sin autorización",
                        )

                        await insertar_log(
                            method_name=method_name,
                            client_id=credit_id,
                            error_message=f"Error 403: {response_data}",
                            http_code=403,
                            tipo="error",
                            traceback_str=error_traceback,
                        )

                        # Intentar renovar token en siguiente intento
                        if attempt < max_retries:
                            logger.info(
                                f"Obteniendo nuevo token e intentando nuevamente..."
                            )
                            try:
                                access_token = await obtener_token(client)
                                headers["Authorization"] = access_token
                                logger.info("Token renovado")
                            except Exception as token_error:
                                logger.error(f"Error al renovar token: {token_error}")

                            await asyncio.sleep(2**attempt)
                            continue

                        return JSONResponse(
                            status_code=401,
                            content={
                                "estado": "error",
                                "codigo_error": "UnauthorizedToken",
                                "mensaje": "No autorizado para confirmar este crédito",
                                "detalles": "Por favor verifica las credenciales de la API",
                            },
                        )

                    # ===== CASO 400: BAD REQUEST =====
                    elif status_code == 400:
                        logger.error(f"Error 400: Solicitud inválida")
                        error_traceback = traceback.format_exc()
                        last_error_response = response_data

                        await error_notify(
                            method_name, credit_id, f"Error 400 en API: {response_data}"
                        )

                        await insertar_log(
                            method_name=method_name,
                            client_id=credit_id,
                            error_message=f"Error 400: {response_data}",
                            http_code=400,
                            tipo="error",
                            traceback_str=error_traceback,
                        )

                        return JSONResponse(
                            status_code=400,
                            content={
                                "estado": "error",
                                "codigo_error": "BadRequest",
                                "mensaje": "Solicitud inválida",
                                "credit_id": credit_id,
                                "detalles": response_data.get(
                                    "message", "Error en los parámetros"
                                ),
                            },
                        )

                    # ===== CASOS 500+: ERRORES DEL SERVIDOR =====
                    elif status_code >= 500:
                        logger.warning(
                            f"Intento {attempt}: Error del servidor ({status_code})"
                        )
                        last_error_response = response_data

                        await error_notify(
                            method_name, credit_id, f"Error {status_code} en API Kuenta"
                        )

                        await insertar_log(
                            method_name=method_name,
                            client_id=credit_id,
                            error_message=f"Error {status_code}: {response_data}",
                            http_code=status_code,
                            tipo="error",
                        )

                        if attempt < max_retries:
                            wait_time = 2**attempt
                            logger.info(f"Reintentando en {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue

                        return JSONResponse(
                            status_code=503,
                            content={
                                "estado": "error",
                                "codigo_error": "ServiceUnavailable",
                                "mensaje": "El servicio no está disponible temporalmente",
                                "detalles": "Por favor intenta nuevamente en unos minutos",
                            },
                        )

                    # ===== OTROS ERRORES HTTP =====
                    else:
                        logger.warning(
                            f"Intento {attempt}: Error HTTP inesperado ({status_code})"
                        )
                        last_error_response = response_data

                        if attempt < max_retries:
                            wait_time = 2**attempt
                            logger.info(f"Reintentando en {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue

                        return JSONResponse(
                            status_code=status_code,
                            content={
                                "estado": "error",
                                "codigo_error": "UnexpectedError",
                                "mensaje": f"Error inesperado ({status_code})",
                                "detalles": "Por favor contacta con soporte técnico",
                            },
                        )

                except httpx.TimeoutException:
                    logger.warning(f"Intento {attempt}: Timeout")

                    if attempt < max_retries:
                        wait_time = 2**attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    await error_notify(method_name, credit_id, "Timeout en API Kuenta")
                    await insertar_log(
                        method_name=method_name,
                        client_id=credit_id,
                        error_message="Timeout en confirmación de payable",
                        http_code=504,
                        tipo="error",
                    )

                    return JSONResponse(
                        status_code=504,
                        content={
                            "estado": "error",
                            "codigo_error": "Timeout",
                            "mensaje": "La solicitud tardó demasiado en responder",
                            "detalles": "Por favor intenta nuevamente",
                        },
                    )

                except httpx.RequestError as e:
                    logger.warning(f"Intento {attempt}: Error de conexión: {e}")

                    if attempt < max_retries:
                        wait_time = 2**attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    await error_notify(
                        method_name, credit_id, f"Error de conexión: {str(e)}"
                    )
                    await insertar_log(
                        method_name=method_name,
                        client_id=credit_id,
                        error_message=f"Error de conexión: {str(e)}",
                        http_code=502,
                        tipo="error",
                    )

                    return JSONResponse(
                        status_code=502,
                        content={
                            "estado": "error",
                            "codigo_error": "ConnectionError",
                            "mensaje": "Error de conexión con el servicio",
                            "detalles": "Por favor intenta nuevamente",
                        },
                    )

            # ===== SI SE AGOTAN TODOS LOS REINTENTOS =====
            logger.error(f"Se agotaron todos los reintentos ({max_retries})")
            await error_notify(
                method_name,
                credit_id,
                f"No se pudo confirmar el payable tras {max_retries} intentos",
            )

            return JSONResponse(
                status_code=503,
                content={
                    "estado": "error",
                    "codigo_error": "MaxRetriesExceeded",
                    "mensaje": "No se pudo completar la confirmación del crédito",
                    "detalles": f"Se agotaron los {max_retries} intentos. Por favor contacta con soporte.",
                    "last_error": last_error_response,
                },
            )

    except ValueError as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de validación: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=credit_id,
            error_message=f"Error de validación: {str(e)}",
            http_code=400,
            tipo="error",
            traceback_str=error_traceback,
        )

        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "codigo_error": "ValidationError",
                "mensaje": "Error de validación",
                "detalles": str(e),
            },
        )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error general en confirm_payable: {str(e)}", exc_info=True)
        await insertar_log(
            method_name=method_name,
            client_id=credit_id,
            error_message=f"Error general: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(method_name, credit_id, f"Error general: {str(e)}")

        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "codigo_error": "InternalError",
                "mensaje": "Error interno del servidor",
                "detalles": "Nuestro equipo técnico ha sido notificado",
            },
        )


# manejar diferentes casos de entrada para el valor "principal" y extraer solo los números
async def limpiar_valor_principal(raw_principal: str) -> float:
    """
    Limpia y extrae el valor numérico de una cadena que contiene un monto.

    Args:
        raw_principal (str): Cadena con el valor principal en diferentes formatos

    Returns:
        float: Valor numerico extraido

    Raises:
        ValueError: Si no se puede extraer un valor numérico válido
    """
    if not raw_principal:
        raise ValueError("El valor principal no puede estar vacio")

    # Convertir a string y eliminar espacios
    valor = str(raw_principal).strip().lower()

    # Casos de entrada posibles:
    # "$2500000"
    # "$2.000.000"
    # "quiero financiar 2500000"
    # "el valor seria 2500000"
    # "seria de 2.500.000"
    # "necesito 2,500,000 pesos"
    # "$ 2,500,000.00"
    # "2500000 COP"
    # "COP 2.500.000"
    # "2'500.000"
    # "2millones500mil"
    # "dos millones quinientos mil"

    # Eliminar caracteres especiales y texto común
    palabras_a_eliminar = [
        "cop",
        "pesos",
        "valor",
        "seria",
        "de",
        "quiero",
        "financiar",
        "necesito",
        "el",
        "aproximadamente",
        "como",
        "cerca",
        "millones",
        "mil",
    ]

    for palabra in palabras_a_eliminar:
        valor = valor.replace(palabra, "")

    # Eliminar símbolos monetarios y caracteres especiales
    valor = re.sub(r'[$ \'"]', "", valor)

    # Convertir puntos y comas usados como separadores de miles
    valor = valor.replace(".", "")
    valor = valor.replace(",", "")

    # Extraer solo dígitos
    numeros = re.findall(r"\d+", valor)

    if not numeros:
        raise ValueError(f"No se pudo extraer un valor numérico de: {raw_principal}")
    # Unir todos los números encontrados
    valor_limpio = "".join(numeros)

    try:
        return float(valor_limpio)
    except ValueError as e:
        await error_notify(
            "limpiar_valor_principal",
            "N/A",
            f"Error al convertir a numero: {valor_limpio}",
        )
        raise ValueError(f"No se pudo convertir a numero: {valor_limpio}") from e


# Ejemplo de uso:


# calcular financiamineto version 2
@app.post("/calcular_financiamiento")
async def calcular_financiamiento(payload: dict):
    """
    Calcula el financiamiento basado en:
    1. Cuota inicial = principal * porcentaje_cuota
    2. Plazo en dias = plazo_escogido * paymentFrequency
    3. Consulta a la API Kuenta para obtener el porcentaje de Aval
    4. Calcula desembolso, deducciones y valor a solicitar
    """
    method_name = "calcular_financiamiento"
    linea_producto_notify_error = f"linea_producto={payload.get('linea_producto')}"

    try:
        logger.info(f"###--- Payload recibido: ###---  \n {payload} \n")

        # --- VALIDACIONES DE ENTRADA ---
        linea_producto = payload.get("linea_producto")
        logger.info(f"linea_producto recibido: {linea_producto} \n")
        if not linea_producto:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                "Falta 'linea_producto' en el payload",
            )
            raise HTTPException(
                status_code=400, detail="Debe incluir 'linea_producto' en el payload"
            )

        # --- PROCESAR SEMESTRE, el dato entra como una palabra ejemplo "segundo semestre" y debe devolver el numero del semestre ---
        semestre_keys = [
            "semestre_renovacion_menu",
            "semestre_renovación_menu",
            "semestre",
        ]
        semestre_texto_raw = next(
            (payload.get(k) for k in semestre_keys if payload.get(k) is not None), ""
        )
        semestre_texto = (
            unicodedata.normalize("NFKD", str(semestre_texto_raw))
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        semestres_map = {
            "primer semestre": 1,
            "segundo semestre": 2,
            "tercer semestre": 3,
            "cuarto semestre": 4,
            "quinto semestre": 5,
            "sexto semestre": 6,
            "septimo semestre": 7,
            "octavo semestre": 8,
            "noveno semestre": 9,
            "decimo semestre": 10,
        }

        if not semestre_texto:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                "Falta 'semestre' en el payload",
            )
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message="Falta 'semestre' en el payload",
                http_code=400,
                tipo="error",
            )

            raise HTTPException(
                status_code=400, detail="Debe incluir 'semestre' en el payload"
            )

        numero_semestre = semestres_map.get(semestre_texto)
        if numero_semestre is None:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                f"Valor de semestre '{semestre_texto}' no reconocido",
            )
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message=f"Valor de semestre '{semestre_texto}' no reconocido",
                http_code=400,
                tipo="error",
            )
            raise HTTPException(
                status_code=400,
                detail=f"El semestre '{semestre_texto}' no es válido. Use: primer semestre, segundo semestre, etc.",
            )

        # --- PROCESAR PLAZO_VALOR_PAGAR, el dato entra en string y debe devolverse como un numero ---
        plazo_texto_raw = payload.get("plazo_valor_pagar", "")
        logging.info(f"plazo_valor_pagar recibido: {plazo_texto_raw} \n")
        plazo_texto = (
            unicodedata.normalize("NFKD", str(plazo_texto_raw))
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        plazo_map = {
            "1 mes": 1,
            "2 meses": 2,
            "3 meses": 3,
            "4 meses": 4,
            "5 meses": 5,
            "6 meses": 6,
        }

        if not plazo_texto:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                "Falta 'plazo_valor_pagar' en el payload",
            )
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message="Falta 'plazo_valor_pagar' en el payload",
                http_code=400,
                tipo="error",
            )
            raise HTTPException(
                status_code=400, detail="Debe incluir 'plazo_valor_pagar' en el payload"
            )

        plazo_valor = plazo_map.get(plazo_texto)
        if plazo_valor is None:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                f"Valor de plazo '{plazo_texto}' no reconocido",
            )
            raise HTTPException(
                status_code=400,
                detail=f"El plazo '{plazo_texto}' no es valido. Use: a un mes, a dos meses, etc.",
            )

        logger.info(f"plazo_valor procesado: {plazo_valor} \n")
        logger.info(f"numero de semestre procesado: {numero_semestre} \n")

        try:
            raw_principal = str(payload.get("principal", "0"))
            principal = await limpiar_valor_principal(raw_principal)
        except ValueError as e:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                f"Error en el valor principal: {str(e)}",
            )
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message=f"Error en el valor principal: {str(e)}",
                http_code=400,
                tipo="error",
            )
            raise HTTPException(
                status_code=400, detail=f"Error en el valor principal: {str(e)}"
            )

        # Porcentaje de cuota (sin si­mbolo %)
        porcentaje_str = (
            str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        )
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        # plazo_escogido = int(payload.get("plazo_escogido", 0))
        # el plazo procesado de tipo string y transformado a numero es plazo_valor y se le asigna a plazo_escogido para los calculos
        plazo_escogido = plazo_valor
        payment_frequency = int(payload.get("paymentFrequency", 30))

        # logger.info (f"plazo escogido para realizar los calculos: {plazo_escogido} \n")

        # --- CALCULOS INICIALES ---
        valor_cuota_inicial = principal * porcentaje_cuota
        dias_totales = plazo_escogido * payment_frequency

        # --- CONSULTA A API KUENTA ---
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await obtener_token(client)
            if not token:
                await error_notify(
                    method_name,
                    linea_producto_notify_error,
                    "No se pudo obtener token de autenticacion",
                )
                raise HTTPException(
                    status_code=401, detail="No se pudo obtener token de autenticacion"
                )

            ext_client_product = None  # cliente externo opcional para productos
            try:  # intenta cargar configuracion desde BD
                ext_client_product = await ExternalClient.from_code(
                    "KUENTA_PRODUCT_GET", client_id=linea_producto
                )  # codigo de servicio en BD

                logger.info(
                    f"Cliente externo KUENTA_PRODUCT_GET cargado desde BD: {ext_client_product.__dict__}\n"
                )
            except ValueError:  # si no existe en BD
                ext_client_product = None  # fallback a settings
            product_base_url = (
                ext_client_product.url
                if ext_client_product and ext_client_product.url
                else API_URL
            )  # base desde BD o settings
            product_base_url = (
                product_base_url or "https://api.kuenta.co/v1"
            )  # fallback por seguridad
            product_base_url = product_base_url.rstrip("/")  # limpia slash final
            if "{linea_producto}" in product_base_url:  # si ya viene plantilla desde BD
                product_url_template = product_base_url  # usa plantilla directa

            else:  # arma plantilla desde base
                if product_base_url.endswith(
                    "product-lines"
                ):  # si viene API_URL de product-lines
                    product_base_url = product_base_url.rsplit("/", 1)[
                        0
                    ]  # elimina el segmento final
                product_url_template = f"{product_base_url}/products/{{linea_producto}}"  # construye plantilla

            KUENTA_URL = product_url_template.format(  # construye URL final con format
                linea_producto=linea_producto,  # parametro linea_producto
            )  # URL final de consulta

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": token,
            }

            if ext_client_product:  # usa cliente externo si existe
                # Inyectar variables dinámicas
                ext_client_product.set_dynamic_values(
                    {
                        "ORG_ID": ORG_ID,
                        "access_token": token,
                        "linea_producto": linea_producto,
                    }
                )

                # headers dinamicos
                ext_client_product.set_headers(headers)

                # Asignar URL final
                ext_client_product.set_url(KUENTA_URL)

                resp = await ext_client_product.run()  # ejecuta request externa

                # data de producto
                data = resp.get("data")
                logging.info(f"Respuesta de Kuenta data: {data} \n")

                product_data = data.get("data").get("product") if data else None
                logging.info(f"Respuesta de Kuenta product_data: {product_data} \n")

                # 1. Validar estructura básica del JSON
                if data is None:
                    logger.error(
                        f"[VALIDACIÓN] Campo 'data' faltante en respuesta de Kuenta para línea: {linea_producto}"
                    )
                    await error_notify(
                        method_name,
                        linea_producto_notify_error,
                        f"Campo 'data' faltante en respuesta de producto: {linea_producto}",
                    )
                    raise HTTPException(
                        status_code=502,
                        detail="Respuesta de Kuenta incompleta: falta campo 'data'",
                    )

                if product_data is None:
                    logger.error(
                        f"[VALIDACIÓN] Campo 'product' faltante en 'data' para línea: {linea_producto}"
                    )
                    await error_notify(
                        method_name,
                        linea_producto_notify_error,
                        f"Campo 'product' faltante en respuesta de producto: {linea_producto}",
                    )
                    logger.info(
                        f"Respuesta de Kuenta status code: {resp.get('status')} \n  respuesta completa: {resp} \n detail :{resp.get('detail')} \n"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail="Respuesta de Kuenta incompleta: falta campo 'product'",
                    )

                if not isinstance(product_data, dict):
                    logger.error(
                        f"[VALIDACIÓN] 'product' no es un diccionario válido para línea: {linea_producto}"
                    )
                    await error_notify(
                        method_name,
                        linea_producto_notify_error,
                        f"'product' no es diccionario en respuesta: {linea_producto}",
                    )
                    raise HTTPException(
                        status_code=502,
                        detail="'product' debe ser un objeto diccionario",
                    )

                # 2. Validar ID del producto (ya existente, pero con log mejorado)
                product_id = product_data.get("ID")
                if product_id is None:
                    logger.error(
                        f"[VALIDACIÓN] Campo 'ID' faltante en producto para línea: {linea_producto}"
                    )
                    await error_notify(
                        method_name,
                        linea_producto_notify_error,
                        f"Campo 'ID' faltante en producto: {linea_producto}",
                    )
                    raise HTTPException(
                        status_code=502, detail="Producto sin ID válido"
                    )

                if product_id != linea_producto:
                    logger.error(
                        f"[VALIDACIÓN] ID mismatch - Esperado: {linea_producto}, Obtenido: {product_id}"
                    )
                    await error_notify(
                        method_name,
                        linea_producto_notify_error,
                        f"ID de producto no coincide: esperado {linea_producto}, obtenido {product_id}",
                    )
                    raise HTTPException(
                        status_code=404,
                        detail=f"El ID del producto no coincide. Esperado: {linea_producto}, Obtenido: {product_id}",
                    )

                logger.info(
                    f"[VALIDACIÓN] ID del producto validado correctamente: {product_id}"
                )

        aval_porcentaje = next(
            (
                float(str(c.get("percentage", 0)))
                for c in product_data.get("costs", [])
                if c.get("label") == "Aval"
            ),
            None,
        )
        logging.info(f"Porcentaje de Aval obtenido: {aval_porcentaje} \n")
        if aval_porcentaje is None:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                "No se encontro porcentaje de Aval en el producto",
            )
            raise HTTPException(
                status_code=404,
                detail="No se encontro porcentaje de Aval en el producto",
            )
        logger.info(f"Porcentaje de Aval obtenido de la linea: {aval_porcentaje}% \n")
        # --- CALCULOS FINALES ---
        valor_desembolsar = principal - valor_cuota_inicial
        if (1 - aval_porcentaje) == 0:
            await error_notify(
                method_name,
                linea_producto_notify_error,
                "El porcentaje de aval no puede ser 100%",
            )
            raise ValueError("El porcentaje de aval no puede ser 100%.")

        valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        deducciones_anticipadas = valor_solicitar * aval_porcentaje

        # --- FORMATEO PARA DEMOSTRACION ---
        demostracion_valor_producto = f"${principal:,.0f}"
        demostracion_cuota_inicial = f"${valor_cuota_inicial:,.0f}"
        demostracion_valor_desembolsar = f"${valor_desembolsar:,.0f}"
        demostracion_deducciones = f"${deducciones_anticipadas:,.0f}"
        demostracion_valor_solicitar = f"${valor_solicitar:,.0f}"

        logger.info(f"numero de semestre procesado: {numero_semestre} semestre \n")
        logger.info(f"plazo_valor_pagar procesado: {plazo_valor} meses \n")

        logger.info(f"----- Resumen de calculos realizados ----- \n")
        logger.info(f"Valor del producto (principal): {demostracion_valor_producto} \n")
        logger.info(
            f"Cuota inicial (valor_cuota_inicial): {demostracion_cuota_inicial} \n"
        )
        logger.info(
            f"Valor a desembolsar (valor_desembolsar): {demostracion_valor_desembolsar} \n"
        )
        logger.info(
            f"Deducciones anticipadas (deducciones_anticipadas): {demostracion_deducciones} \n"
        )
        logger.info(
            f"Valor a solicitar (valor_solicitar): {demostracion_valor_solicitar} \n"
        )
        logger.info(f"Aval aplicado porcentaje (aval_porcentaje): {aval_porcentaje} \n")
        logger.info(f"Plazo en dias (plazo_dias): {dias_totales} \n")
        logger.info(f"Porcentaje escogido (porcentaje_str): {porcentaje_str}% \n")

        logger.info("Calculo completado correctamente. \n")
        logger.info("-------------fin de la ejecucion------------------ \n")

        # notificacion informativa
        info_message = f"Calculo de financiamiento realizado correctamente en etapa de simulacion \n ID linea de producto: {linea_producto}"
        await info_notify(method_name, linea_producto_notify_error, info_message)
        return {
            "valor_producto": principal,
            "cuota_inicial": valor_cuota_inicial,
            "valor_desembolsar": valor_desembolsar,
            "deducciones_anticipadas": deducciones_anticipadas,
            "valor_solicitado": valor_solicitar,
            "aval_aplicado_porcentaje": aval_porcentaje,
            "plazo_dias": dias_totales,
            "porcentaje_escogido": porcentaje_str,
            "numero_semestre": numero_semestre,
            "plazo_valor_pagar_meses": plazo_valor,
            "plazo_escogido_meses": plazo_escogido,
            # Agregar valores formateados para demostracion
            "valor_producto_demostracion": demostracion_valor_producto,
            "cuota_inicial_demostracion": demostracion_cuota_inicial,
            "valor_desembolsar_demostracion": demostracion_valor_desembolsar,
            "deducciones_anticipadas_demostracion": demostracion_deducciones,
            "valor_solicitado_demostracion": demostracion_valor_solicitar,
        }

    except ValueError as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de datos: {e}")
        await insertar_log(
            method_name=method_name,
            client_id=linea_producto_notify_error,
            error_message=f"Error de datos: {e}",
            http_code=400,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name, linea_producto_notify_error, f"Error de datos: {e}"
        )
        return {
            "estado": "error",
            "mensaje": MENSAJES_USUARIO["valor_invalido"],
            "detalles_usuario": "Por favor verifica el valor del monto a financiar.",
        }

    except HTTPException as e:
        error_traceback = traceback.format_exc()
        mensaje_usuario = MENSAJES_USUARIO["datos_faltantes"]
        if "semestre" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["semestre_invalido"]
        elif "plazo" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["plazo_invalido"]
        elif "li­nea_producto" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["linea_no_existe"]

        await insertar_log(
            method_name=method_name,
            client_id=linea_producto_notify_error,
            error_message=f"Error de datos: {e}",
            http_code=400,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        return {
            "estado": "error",
            "mensaje": mensaje_usuario,
            "detalles_usuario": "Si el problema persiste, por favor comuni­cate con nuestro servicio al cliente.",
        }

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error interno inesperado: {e}")
        await insertar_log(
            method_name=method_name,
            client_id=linea_producto_notify_error,
            error_message=f"Error interno: {e}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name, linea_producto_notify_error, f"Error interno: {e}"
        )
        return {
            "estado": "error",
            "mensaje": MENSAJES_USUARIO["error_conexion"],
            "detalles_usuario": "Nuestro equipo tecnico ha sido notificado del inconveniente.",
        }


# Nuevo endpoint para consultar el estado de un pago usando creditid, installmentid y orderid
@app.post("/obtener-estado/{debtor_id}")
async def obtener_estado(debtor_id: str, request: Request):
    """
    Recibe un JSON con creditid, installmentid y orderid.
    Realiza hasta 3 peticiones a la API externa con intervalos de 15 segundos.
    Termina si el status es diferente de 'pending' o si se agotan los intentos.
    """
    method_name = "obtener_estado"
    try:
        body = await request.json()
        creditid = body.get("creditid")
        installmentid = body.get("installmentid")
        orderid = body.get("orderid")
        debtor_id_notify_error = (
            f"debtor_id_cliente =  {debtor_id} y creditid = {creditid}"
        )
        logger.info(
            f"+++++ Parametros recibidos en el body: creditid= ++++++++, \n {creditid} \n"
        )
        logger.info(
            f"+++++ Parametros recibidos en el body: installmentid= ++++++++, \n {installmentid} \n"
        )
        logger.info(
            f"+++++ Parametros recibidos en el body: orderid= ++++++++, \n {orderid} \n"
        )

        if not creditid or not installmentid or not orderid:
            raise HTTPException(
                status_code=400,
                detail="Faltan parametros obligatorios: creditid, installmentid, orderid",
            )

        ext_client_order = None  # cliente externo opcional para estado de orden
        try:  # intenta cargar configuracion desde BD
            ext_client_order = await ExternalClient.from_code(
                "KUENTA_ORDER_STATUS", client_id=debtor_id
            )  # codigo de servicio en BD

            logger.info(
                f"Cliente externo KUENTA_ORDER_STATUS cargado desde BD: {ext_client_order.__dict__}\n"
            )
        except ValueError:  # si no existe en BD
            ext_client_order = None  # fallback a settings
            logger.info(
                "Cliente externo KUENTA_ORDER_STATUS no encontrado en BD, usando configuración por defecto."
            )
        order_base_url = (
            ext_client_order.url
            if ext_client_order and ext_client_order.url
            else GET_PAYABLE_URL
        )  # base desde BD o settings
        order_base_url = (
            order_base_url or "https://api.kuenta.co/v1/payables"
        )  # fallback por seguridad
        order_base_url = order_base_url.rstrip("/") + "/"  # asegura slash final
        order_url_template = f"{order_base_url}{{creditid}}/installments/0/orders/list/{{orderid}}"  # plantilla con placeholders
        url = order_url_template.format(  # construye URL final
            creditid=creditid,  # parametro creditid
            orderid=orderid,  # parametro orderid
        )  # URL final de consulta

        intentos = 3
        intervalo_segundos = 10
        intento = 0

        logger.info(
            f"Parametros recibidos: creditid={creditid}, installmentid={installmentid}, orderid={orderid}"
        )

        async with httpx.AsyncClient() as client:
            # Obtener token una sola vez

            access_token = await obtener_token(client)
            logger.info(f"Token obtenido: {access_token}")

            if not access_token:
                raise HTTPException(
                    status_code=401, detail="No se pudo obtener el token de acceso"
                )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": debtor_id,
                "Authorization": access_token,
            }

            while intento < intentos:
                intento += 1
                try:
                    if ext_client_order:  # usa servicio externo si existe
                        # Inyectar variables dinámicas
                        ext_client_order.set_dynamic_values(
                            {"ORG_ID": ORG_ID, "access_token": access_token}
                        )

                        # Asignar URL final
                        ext_client_order.set_url(url)

                        response = (
                            await ext_client_order.run()
                        )  # ejecuta request externa
                        if not isinstance(response, dict):  # valida tipo de respuesta
                            raise Exception(
                                "Respuesta invalida del servicio externo"
                            )  # error si no es dict
                        status_code = response.get(
                            "status", 500
                        )  # status HTTP simulado
                        data = response.get("data") or {}  # data de respuesta
                        if not isinstance(data, dict):  # valida data
                            data = {}  # fallback a dict vacio
                        if status_code >= 400:  # maneja error HTTP
                            raise Exception(
                                f"Error HTTP {status_code}"
                            )  # fuerza captura en except
                    else:  # fallback a httpx directo
                        response = await client.get(
                            url, headers=headers
                        )  # request httpx original
                        response.raise_for_status()  # valida status HTTP
                        data = response.json()  # parse JSON
                    status = data.get("status")
                    logger.info(f"Intento {intento}: status del pago = {status}")

                    if status != "pending":
                        logger.info(
                            f"Estado final obtenido: {status} en el intento {intento} \n"
                        )
                        logger.info(f"Respuesta completa: {data} \n")
                        return data

                except Exception as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"Error en intento {intento}: {str(e)}")
                    await insertar_log(
                        method_name=method_name,
                        client_id=debtor_id_notify_error,
                        error_message=f"Error en intento {intento}: {str(e)}",
                        http_code=500,
                        tipo="error",
                        traceback_str=error_traceback,
                    )
                    await error_notify(
                        method_name,
                        debtor_id_notify_error,
                        f"Error en intento: {intento} {str(e)}",
                    )

                if intento < intentos:
                    await asyncio.sleep(intervalo_segundos)
        return {
            "mensaje": "No se obtuvo un estado diferente a 'pending' tras 3 intentos"
        }

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error en el proceso: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=debtor_id_notify_error,
            error_message=f"Error en el proceso: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name, debtor_id_notify_error, f"Error en el proceso: {str(e)}"
        )
        raise HTTPException(status_code=500, detail=f"Error en el proceso: {str(e)}")


@app.post("/registrar-renovacion", tags=["Renovaciones"])
async def registrar_renovacion_v2(payload: RenovacionRefactorRequest):
    method_name = "registrar_renovacion"
    client_id = payload.nombre_cliente

    try:
        logger.info(f"Procesando inserccion en la db de renovación para: {client_id}")

        # Preparar datos
        data_cliente = {
            "numero_telefono": payload.numero_telefono,
            "correo_cliente": payload.correo_cliente,
            "nombre_cliente": payload.nombre_cliente,
            "cedula_cliente": payload.cedula_cliente,
        }

        data_credito = payload.datos_credito.model_dump()

        # Inserción en BD
        resultado = await insertar_registro_completo(data_cliente, data_credito)

        # Verificar si la inserción falló por duplicado o fue actualización
        if isinstance(resultado, dict):
            if resultado["status"] == "success":
                logger.info(f"Renovación registrada exitosamente: {resultado}")
                await info_notify(
                    method_name,
                    client_id,
                    f"Renovación registrada. en base de datos ID: {resultado['id_renovacion']}",
                )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "Renovación registrada correctamente",
                    "data": resultado,
                },
            )

        elif resultado["status"] == "updated":
            logger.info(f"Registro actualizado exitosamente: {resultado}")
            await info_notify(
                method_name,
                client_id,
                f"Registro actualizado ID: {resultado['id_renovacion']}",
            )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": resultado[
                        "message"
                    ],  # "Registro actualizado exitosamente..."
                    "data": resultado,
                },
            )

        elif resultado["status"] == "error":
            logger.warning(f"Intento de registro duplicado rechazado: {resultado}")
            await error_notify(
                method_name,
                client_id,
                f"No se puede registrar la renovación: {resultado['message']}",
            )
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=f"No se puede registrar la renovación: {resultado['message']}",
                http_code=409,
                tipo="error",
            )
            return JSONResponse(
                status_code=409,  # Conflict
                content={
                    "status": "error",
                    "message": "No se puede registrar la renovación",
                    "error": resultado["message"],
                    "details": resultado.get("details", {}),
                },
            )

        else:
            # Caso inesperado
            logger.error(f"Estado desconocido en resultadoado: {resultado}")
            await error_notify(
                method_name,
                client_id,
                "Estado de respuesta desconocido al registrar renovación",
            )
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message="Estado de respuesta desconocido al registrar renovación",
                http_code=500,
                tipo="error",
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Error interno del servidor",
                    "details": "Estado de respuesta desconocido",
                },
            )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Fallo en {method_name}: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error interno: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(method_name, client_id, f"Error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
                "detail": str(e),
            },
        )


# consultar los creditos realizados en el flujo de renovaciones
@app.post(
    "/consultar-creditos", tags=["Consultas"], summary="Búsqueda avanzada de créditos"
)
async def buscar_creditos(filtros: ConsultaCreditoRequest):
    """
    Busca créditos combinando filtros opcionales:
    - Por Cédula
    - Por Teléfono
    - Por ID de Estado (0-20)
    - Por Rango de Fechas
    """
    try:
        resultados = await consultar_creditos_filtro(
            cedula=filtros.cedula,
            telefono=filtros.telefono,
            estado_id=filtros.estado_id,
            fecha_inicio=filtros.fecha_inicio,
            fecha_fin=filtros.fecha_fin,
        )

        if not resultados:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "info",
                    "message": "No se encontraron registros con esos filtros",
                },
            )

        # --- LÓGICA DE EXPORTACIÓN ---
        if filtros.exportar_excel:
            # Generar el archivo en memoria
            excel_buffer = generar_excel_memoria(resultados)

            # Definir nombre del archivo (puedes ponerle fecha dinámica si quieres)
            filename = "reporte_creditos.xlsx"

            # Retornar como descarga de archivo
            return StreamingResponse(
                excel_buffer,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "total_resultados": len(resultados),
                "data": jsonable_encoder(resultados),
            },
        )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error en endpoint búsqueda: {str(e)}")
        await insertar_log(
            method_name="buscar_creditos",
            client_id=None,
            error_message=f"Error interno: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        return JSONResponse(
            status_code=500, content={"status": "error", "detail": str(e)}
        )


#### endpoints para pruebas de notificaciones ###


# Endpoint que llama a error_notify (envía email + telegram)
@app.post("/test-notify")
async def test_notify(payload: TestNotifyRequest = Body(...)):
    try:
        result = await error_notify(
            payload.method_name, payload.client_id, payload.message
        )
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-notify")
        return JSONResponse(
            status_code=500, content={"status": "error", "detail": str(e)}
        )


# Endpoint para probar solo envi­o por email
@app.post("/test-email")
async def test_email(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_email(
            payload.method_name, payload.client_id, payload.message
        )
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-email")
        return JSONResponse(
            status_code=500, content={"status": "error", "detail": str(e)}
        )


# Endpoint para probar solo envío a Telegram
@app.post("/test-telegram")
async def test_telegram(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_telegram(
            payload.method_name, payload.client_id, payload.message
        )
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-telegram")
        return JSONResponse(
            status_code=500, content={"status": "error", "detail": str(e)}
        )


# Endpoint para enviar correo de renovacion de credito con validaciones
@app.post(
    "/Correo_post_llamada",
    summary="Receptor de variables despues de la llamada",
    description="Recibe el payload con las variables de entrada y extraídas.",
    tags=["Correo_post_llamada"],
)
async def handle_webhook(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Endpoint principal que recibe el payload del webhook.

    1.  Valida automaticamente el payload contra el modelo `WebhookPayload`.
    2.  Llama al servicio `procesar_webhook` para manejar toda la logica.
    3.  Retorna una respuesta JSON.
    """
    logger.info(f"Inicio del procesamiento del webhook, payload {payload} \n")
    try:
        logger.info(
            f"Payload completo recibido: {payload.model_dump_json(indent=2)} \n"
        )

        # logging.info(f"Objetivo extraido: {payload.extracted_variables.objetivo} \n")

        # Logica de enrutamiento de el envio de los correos basada en el objetivo de la llamada de cada agente IA
        objetivo = payload.extracted_variables.objetivo
        logging.info(f"Objetivo a procesar: {objetivo}")

        if objetivo == "webinar":
            logging.info(
                "El objetivo es 'webinar'. Llamando a procesar_webhook_webinar."
            )

            # empieza aqui
            resultado = await procesar_webhook_webinar(payload)

            logging.info(
                f"Procesamiento completado para webinar: {payload.input_variables.NOMBRE_TITULAR}"
            )

            # Validar explicitamente el resultado
            if resultado.get("status") == "error":
                logging.info(
                    f"error en la procesamiento del webhook webinar: {resultado.get('message')}"
                )
                await error_notify(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}",
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": resultado.get("message", "Error desconocido"),
                        "correo_enviado": resultado.get("correo_enviado", False),
                        "intentos": resultado.get("intentos_correo", 0),
                        "data": resultado,
                    },
                )
            elif resultado.get("status") == "success":
                logging.info(f"Webhook webinar EXITOSO: Correo enviado")
                await info_notify(
                    method_name="webhook_webinar",
                    client_id=objetivo,
                    info_message=f"Webhook de webinar completado exitosamente. Correo enviado en  para {payload.input_variables.NOMBRE_TITULAR}",
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success",
                        "message": "Webhook de renovacion procesado",
                        "correo_enviado": True,
                        "intentos": resultado.get("intentos_correo"),
                        "data": resultado,
                    },
                )
            else:
                logging.warning(f"Webhook webinar con problemas: {resultado}")
                await error_notify(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}",
                )
                await insertar_log(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}",
                )
                return {
                    "status": "error"
                    if resultado.get("status") == "error"
                    else "partial",
                    "message": resultado.get("message", "Error desconocido"),
                    "correo_enviado": resultado.get("correo_enviado", False),
                    "intentos": resultado.get("intentos_correo", 0),
                    "data": resultado,
                }

        elif objetivo == "renovacion":
            logging.info(
                "El objetivo es 'renovacion'. Llamando a procesar_webhook_renovacion."
            )

            resultado = await procesar_webhook_renovacion(payload)

            logging.info(
                f"Procesamiento completado para renovacion: {payload.input_variables.NOMBRE_TITULAR}"
            )

            # Validar explicitamente el resultado
            if resultado.get("status") == "error":
                logging.info(
                    f"error en la procesamiento del webhook renovacion: {resultado.get('message')}"
                )
                await error_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}",
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": resultado.get("message", "Error desconocido"),
                        "correo_enviado": resultado.get("correo_enviado", False),
                        "intentos": resultado.get("intentos_correo", 0),
                        "data": resultado,
                    },
                )
            elif resultado.get("status") == "success":
                # logging.info(f"Webhook renovacion EXITOSO: Correo enviado")
                await info_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    info_message=f"Webhook de renovacion completado exitosamente. Correo enviado  para {payload.input_variables.NOMBRE_TITULAR}",
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success",
                        "message": "Webhook de renovacion procesado exitosamente",
                        "correo_enviado": True,
                        "intentos": resultado.get("intentos_correo"),
                        "data": resultado,
                    },
                )
            else:
                logging.warning(
                    f"no se encontro el objetivo de la llamada: {resultado}"
                )
                await error_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}",
                )
                await insertar_log(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}",
                )
                return {
                    "status": "error"
                    if resultado.get("status") == "error"
                    else "partial",
                    "message": resultado.get("message", "Error desconocido"),
                    "correo_enviado": resultado.get("correo_enviado", False),
                    "intentos": resultado.get("intentos_correo", 0),
                    "data": resultado,
                }

        elif objetivo == "renovacion y refinanciacion":
            logging.info(
                "El objetivo es 'renovacion y refinanciacion'. Llamando a procesar_llamada_renovacion_Y_refinanciamiento."
            )
            logging.info(
                f"payload completo recibido: {payload.model_dump_json(indent=2)} \n"
            )

            try:
                resultado = await procesar_llamada_renovacion_Y_refinanciamiento(
                    payload
                )

                logging.info(
                    f"Procesamiento completado para renovacion y refinanciacion: {payload.input_variables.NOMBRE_TITULAR}"
                )

                # Validar explicitamente el resultado
                if resultado.get("status") == "error":
                    logging.error(
                        f"Error en el procesamiento: {resultado.get('message')}"
                    )
                    await error_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        error_message=f"Error en procesamiento de renovacion y refinanciacion: {resultado.get('message')}",
                    )
                    return JSONResponse(
                        status_code=430,
                        content={
                            "status": "error",
                            "message": resultado.get("message", "Error desconocido"),
                            "acciones_ejecutadas": resultado.get("acciones", []),
                            "errores": resultado.get("errores", []),
                            "data": resultado,
                        },
                    )
                elif resultado.get("status") == "success":
                    logging.info(
                        f"Procesamiento EXITOSO para renovacion y refinanciacion"
                    )
                    await info_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        info_message=f"Procesamiento de renovacion y refinanciacion completado exitosamente para {payload.input_variables.NOMBRE_TITULAR}. Acciones: {', '.join(resultado.get('acciones_ejecutadas', []))}",
                    )
                    return JSONResponse(
                        status_code=200,
                        content={
                            "status": "success",
                            "message": "Procesamiento de renovacion y refinanciacion completado exitosamente",
                            "cliente": resultado.get("cliente"),
                            "correo": resultado.get("correo"),
                            "acciones_ejecutadas": resultado.get(
                                "acciones_ejecutadas", []
                            ),
                            "data": resultado,
                        },
                    )
                else:
                    # status == "partial" o "warning"
                    logging.warning(f"⚠️ Procesamiento parcial: {resultado}")
                    await error_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        error_message=f"Procesamiento parcial de renovacion y refinanciacion: {resultado.get('message')}",
                    )
                    return JSONResponse(
                        status_code=430,
                        content={
                            "status": resultado.get("status", "partial"),
                            "message": resultado.get(
                                "message", "Procesamiento parcial"
                            ),
                            "acciones_ejecutadas": resultado.get(
                                "acciones_ejecutadas", []
                            ),
                            "errores": resultado.get("errores", []),
                            "data": resultado,
                        },
                    )
            except Exception as e:
                logging.error(
                    f"Excepción en procesar_llamada_renovacion_Y_refinanciamiento: {e}",
                    exc_info=True,
                )
                await error_notify(
                    method_name="handle_webhook_renovacion_refinanciacion",
                    client_id=objetivo,
                    error_message=f"Excepción en renovacion y refinanciacion: {str(e)}",
                )
                return JSONResponse(
                    status_code=430,
                    content={
                        "status": "error",
                        "message": f"Error en el procesamiento de renovacion y refinanciacion",
                        "detail": str(e),
                        "cliente": payload.input_variables.NOMBRE_TITULAR
                        if payload and payload.input_variables
                        else "unknown",
                    },
                )

        elif objetivo == "cobranzas":
            # [LOG] Trazamos que entramos correctamente al nuevo flujo
            logging.info(
                "El objetivo detectado es 'cobranzas'. Invocando procesar_webhook_cobranzas."
            )

            # [LÓGICA] Disparamos el servicio asíncrono pasándole el DTO entero
            resultado = await procesar_webhook_cobranzas(payload)

            # [LOG] Avisamos que el servicio ya nos devolvió una respuesta
            logging.info(
                f"Retorno del servicio cobranzas para: {payload.input_variables.NOMBRE_TITULAR}"
            )

            # [VALIDACIÓN] Si el servicio reportó un fallo duro (status: error)
            if resultado.get("status") == "error":
                logging.error(
                    f"Fallo detectado en webhook cobranzas: {resultado.get('message')}"
                )

                # [NOTIFICACIÓN] Alertamos al equipo de soporte/dev
                await error_notify(
                    method_name="handle_webhook_cobranzas",
                    client_id=payload.input_variables.NOMBRE_TITULAR or "Desconocido",
                    error_message=f"Fallo en flujo cobranzas: {resultado.get('message')}",
                )
                # [HTTP] Devolvemos 500 para mantener la convención que usas en webinar/renovación
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "error",
                        "message": resultado.get(
                            "message", "Error desconocido en cobranzas"
                        ),
                        "correo_enviado": resultado.get("correo_enviado", False),
                        "intentos": resultado.get("intentos_correo", 0),
                        "data": resultado,
                    },
                )

            # [VALIDACIÓN] Si el servicio fue un éxito rotundo (correo enviado y todo OK)
            elif resultado.get("status") == "success":
                logging.info(f"Webhook cobranzas EXITOSO. Correo marcado como enviado.")

                # [NOTIFICACIÓN] Guardamos registro de éxito para métricas
                await info_notify(
                    method_name="webhook_cobranzas",
                    client_id=payload.input_variables.NOMBRE_TITULAR or "Desconocido",
                    info_message=f"Proceso cobranzas exitoso para {payload.input_variables.NOMBRE_TITULAR}",
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success",
                        "message": "Webhook de cobranzas procesado sin errores",
                        "correo_enviado": resultado.get("correo_enviado", False),
                        "data": resultado,
                    },
                )

            # [VALIDACIÓN] Casos de 'warning' o 'partial' (Ej. cliente no interesado, no contestó)
            else:
                logging.warning(
                    f"Webhook cobranzas retornó estado advertencia/parcial: {resultado}"
                )
                return {
                    "status": resultado.get("status", "partial"),
                    "message": resultado.get(
                        "message", "Proceso de cobranzas con advertencias"
                    ),
                    "correo_enviado": resultado.get("correo_enviado", False),
                    "data": resultado,
                }
        else:
            # Objetivo no reconocido
            logging.warning(f"Objetivo no reconocido: {objetivo}")
            await error_notify(
                method_name="handle_webhook",
                client_id=objetivo,
                error_message=f"Objetivo no reconocido en webhook: {objetivo}",
            )
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Objetivo no reconocido: {objetivo}",
                    "objetivos_validos": [
                        "webinar",
                        "renovacion",
                        "renovacion y refinanciacion",
                    ],
                    "cliente": payload.input_variables.NOMBRE_TITULAR
                    if payload and payload.input_variables
                    else "unknown",
                },
            )

    except Exception as e:
        # No devolver 500 para evitar que el proveedor del webhook reintente
        # y provoque envíos duplicados. Registramos y notificamos, y
        # respondemos 200 con detalle del error interno.
        error_traceback = traceback.format_exc()
        logging.error(f"Error en el endpoint /webhook: {str(e)}", exc_info=True)
        try:
            await insertar_log(
                method_name="handle_webhook",
                client_id=(
                    payload.input_variables.NOMBRE_TITULAR
                    if payload and payload.input_variables
                    else "unknown"
                ),
                error_message=f"Error en el endpoint /webhook: {str(e)}",
                http_code=500,
                tipo="error",
                traceback_str=error_traceback,
            )
        except Exception as log_error:
            logging.error(f"Error al registrar el log: {str(log_error)}")

        await error_notify(
            method_name="handle_webhook",
            client_id=(
                payload.input_variables.NOMBRE_TITULAR
                if payload and payload.input_variables
                else "unknown"
            ),
            error_message=f"Error en el endpoint /webhook: {str(e)}",
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "message": "Error al procesar el webhook",
                "detail": str(e),
                "cliente": (
                    payload.input_variables.NOMBRE_TITULAR
                    if payload and payload.input_variables
                    else "unknown"
                ),
            },
        )


# Endpoint para registrar renovaciones en la base de datos
@app.post(
    "/renovaciones", tags=["Renovaciones"], summary="Registrar renovación de cliente"
)
async def registrar_renovacion(payload: RenovacionPayload):
    """
    Endpoint para registrar una renovacion de credito en la base de datos.

    Recibe:
    - estado_final_renovacion: Estado final de la renovacion
    - estado_pago_payvalida: Estado del pago en PayValida
    - nombre_cliente: Nombre del cliente

    Retorna:
    - Confirmacion de insercion y mensaje de exito
    """
    method_name = "registrar_renovacion"

    try:
        logger.info(f"Intentando registrar renovación para: {payload.nombre_cliente}")

        # Crear conexión asincrónica a la base de datos
        connection = await aiomysql.connect(
            host=db_host, user=db_user, password=db_pass, db=db_name
        )

        try:
            async with connection.cursor() as cursor:
                # Preparar la consulta INSERT
                query = """
                    INSERT INTO renovaciones_clientes 
                    (estado_final_renovacion, estado_pago_payvalida, nombre_cliente) 
                    VALUES (%s, %s, %s)
                """

                # Ejecutar la inserción
                await cursor.execute(
                    query,
                    (
                        payload.estado_final_renovacion,
                        payload.estado_pago_payvalida,
                        payload.nombre_cliente,
                    ),
                )

                # Confirmar la transacción
                await connection.commit()

                # Obtener el ID de la renovación insertada
                insertado_id = cursor.lastrowid

                logger.info(
                    f"Renovación registrada exitosamente con ID: {insertado_id}"
                )

                # Enviar notificación informativa
                info_message = (
                    f"Renovación registrada exitosamente en la base de datos\n"
                    f"Cliente: {payload.nombre_cliente}\n"
                    f"Estado Final: {payload.estado_final_renovacion}\n"
                    f"Estado Pago: {payload.estado_pago_payvalida}\n"
                    f"ID Registro: {insertado_id}"
                )

                await info_notify(
                    method_name=method_name,
                    client_id=payload.nombre_cliente,
                    info_message=info_message,
                    entity_id=str(insertado_id),
                )

                return JSONResponse(
                    status_code=201,
                    content={
                        "status": "success",
                        "message": "Renovación registrada exitosamente",
                        "id_registro": insertado_id,
                        "cliente": payload.nombre_cliente,
                        "estado_final_renovacion": payload.estado_final_renovacion,
                        "estado_pago_payvalida": payload.estado_pago_payvalida,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
        finally:
            connection.close()

    except aiomysql.Error as db_error:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de base de datos: {str(db_error)}")
        await insertar_log(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error BD: {str(db_error)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error al insertar en BD: {str(db_error)}",
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error al conectar con la base de datos",
                "detail": "No se pudo registrar la renovación",
            },
        )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error en registrar_renovacion: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error en registrar_renovacion: {str(e)}",
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
                "detail": str(e),
            },
        )


# enpoints cobranzas
# Endpoint para obtener información de pagos en mora de un crédito
@app.post(
    "/pagos-mora", tags=["Cobranzas"], summary="Obtener información de pagos en mora"
)
async def obtener_pagos_mora(payload: MoraData):
    """
    Endpoint para obtener información de pagos en mora de un crédito.

    Realiza una petición GET a la API externa:
    https://api.kuenta.co/v1/receivable/{id_credito}

    Ejemplo de uso:
    GET /obtener-pagos-mora/8c082794-796c-4987-ac28-e4918bea590d
    """
    method_name = "obtener_pagos_mora"
    id_credito = payload.id_credito
    client_id = id_credito

    try:
        logger.info(f"Iniciando consulta de pagos en mora para crédito: {id_credito}")

        # Validación del ID del crédito
        # if not id_credito or not id_credito.strip():
        #     raise ValueError("El ID del crédito no puede estar vacío")

        # Obtener token de autenticación
        try:
            token = await obtener_token()
        except Exception as token_error:
            logger.error(f"Error al obtener token: {str(token_error)}")
            raise

        # Configuración de headers
        headers = {
            "Config-Organization-ID": ORG_ID,
            "Organization-ID": ORG_ID,
            "Authorization": token,
        }

        # Cargar servicio externo desde BD o usar configuración fallback
        ext_client = None
        try:
            ext_client = await ExternalClient.from_code(
                "KUENTA_RECEIVABLE_GET", client_id=client_id
            )
            logger.info(
                "Configuración de servicio externo para obtener pagos en mora cargada desde BD"
            )

            # Configurar el path con el ID del crédito
            ext_client.set_path(f"/{id_credito}")
            ext_client.set_headers(headers)

            if not ext_client.url:
                logger.error(
                    "La configuración del servicio externo no tiene URL definida"
                )
                raise ValueError("URL no definida en configuración")

            # url completa del servicio externo
            # logger.info(f"URL del servicio externo para pagos en mora: {ext_client.url}")
            # headers del servicio externo
            # logger.info(f"Headers del servicio externo para pagos en mora: {ext_client.header}")

            await info_notify(
                method_name=method_name,
                client_id=f"id del crédito: {id_credito}",
                info_message=f"Configuración del servicio externo para obtener pagos en mora cargada. URL: {ext_client.url}",
            )
            # Ejecutar request con servicio externo
            response = await ext_client.run()
            # informar que se realizo la peticion para la consulta de pagos en mora

            # logger.info(f"Petición realizada a servicio externo para obtener pagos en mora del crédito: {id_credito} \n")

            # logger.info(f"Respuesta del servicio externo: {response} \n")

            await info_notify(
                method_name=method_name,
                client_id=client_id,
                info_message=f"Petición realizada a servicio externo para obtener pagos en mora del crédito: {id_credito}",
            )

        except (ValueError, Exception) as e:
            # Si falla el servicio externo, usar cliente HTTP directo
            logger.warning(
                f"Usando cliente HTTP directo para obtener pagos en mora: {str(e)}"
            )
            await error_notify(
                method_name=method_name,
                client_id=client_id,
                error_message=f"Fallo en servicio externo, usando cliente HTTP directo: {str(e)}",
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                http_response = await client.get(
                    f"https://api.kuenta.co/v1/receivable/{id_credito}", headers=headers
                )
                response = {
                    "status": http_response.status_code,
                    "data": http_response.json()
                    if http_response.status_code < 400
                    else {"error": http_response.text},
                }
                await info_notify(
                    method_name=method_name,
                    client_id=client_id,
                    info_message=f"Petición realizada con cliente HTTP directo para obtener pagos en mora del crédito: {id_credito}. Status: {http_response.status_code}",
                )

        status_code = response.get("status", 500)
        response_data = response.get("data", {})

        # Manejo de diferentes status
        if status_code == 200:
            # logger.info(f"Consulta de pagos en mora exitosa para crédito: {id_credito}")

            # Procesar installments si existen en la respuesta
            processed_data = response_data.get("data", {}).get("credit", {})
            # acceder a las cuotas
            installments = processed_data.get("installments", [])
            # cuotas del credito
            await info_notify(
                method_name=method_name,
                client_id=client_id,
                info_message=f"Cuotas obtenidas para crédito: {id_credito}, cuotas: {len(installments)}, detalle de cuotas : {installments}",
            )

            # logger.info(f"Installments obtenidos: {len(installments)} para crédito: {id_credito}\n \n")
            # logger.info (f"installments data: {installments} \n")

            # acceder al resumen
            resumen = processed_data.get("summary", {})
            # logger.info (f"resumen data: {resumen}")

            # Procesar la lista de cuotas (installments) retornada por la API de Kuenta.
            # Se recorre en orden para clasificar cada cuota según su estado y construir
            # el resumen que se enviará al frontend.
            first_pending_installment = None  # cuota pagable que se mostrará al cliente
            count_pending = (
                0  # estado 3 = pendiente (pagable, acumula el valor de las vencidas)
            )
            count_expired = (
                0  # estado 4 = vencida (no se puede pagar directamente en Kuenta)
            )
            count_paid = 0  # estado 1 = ya pagada

            if installments and isinstance(installments, list):
                for installment in installments:
                    logger.debug(
                        f"Procesando installment: {installment.get('number')} con estado {installment.get('status')} para crédito: {id_credito}"
                    )
                    status_installment = installment.get("status")

                    # Cuota ya pagada — solo se contabiliza, no se muestra al cliente
                    if status_installment == 1:
                        count_paid += 1

                    # Cuota vencida — Kuenta no permite pagarla directamente; su valor
                    # queda acumulado en la última cuota pendiente (estado 3)
                    elif status_installment == 4:
                        count_expired += 1

                    # Cuota pendiente — es la que el cliente puede pagar.
                    # Se captura únicamente la primera que aparezca en la lista (orden
                    # cronológico) para mostrarla como "próxima cuota a pagar".
                    elif status_installment == 3:
                        count_pending += 1
                        if first_pending_installment is None:
                            fecha_pago = installment.get("date")
                            valor_pagar = installment.get("payment")
                            first_pending_installment = {
                                "id": installment.get("id"),
                                "numero_de_cuota": installment.get("number"),
                                "fecha_pago": fecha_pago,
                                # Fecha formateada en español para mostrar en pantalla
                                "fecha_pago_legible": formatear_fecha_legible(
                                    fecha_pago
                                )
                                if fecha_pago
                                else "N/A",
                                "valor_total_pagar": valor_pagar,
                                # Valor formateado como moneda colombiana ($1.234.567)
                                "valor_total_legible": formatear_valor_moneda(
                                    valor_pagar
                                )
                                if valor_pagar
                                else "N/A",
                                "dias_de_mora_cuota": installment.get(
                                    "debtInterestDays"
                                ),
                                "estado_cuota": "pendiente",
                            }
                            logger.info(
                                f"Primera cuota pendiente capturada para crédito {id_credito}: cuota {installment.get('number')}"
                            )

                # Validar si la fecha de la cuota a pagar corresponde a un año anterior al
                # actual. Cuando esto ocurre el frontend debe mostrar "FECHA VENCIDA" en
                # lugar de "PRÓXIMA FECHA DE PAGO" para que el cliente entienda que está
                # atrasado. El campo `retrasado` actúa como bandera para ese cambio de etiqueta.
                if first_pending_installment:
                    fecha_str = first_pending_installment.get("fecha_pago", "")
                    retrasado = False
                    label_fecha = "PRÓXIMA FECHA DE PAGO"
                    if fecha_str:
                        try:
                            # Comparar la fecha completa (no solo el año) para detectar
                            # cuotas vencidas dentro del mismo año calendario
                            fecha_cuota = datetime.fromisoformat(
                                fecha_str.replace("Z", "+00:00")
                            ).date()
                            if fecha_cuota < datetime.now().date():
                                retrasado = True
                                label_fecha = "FECHA VENCIDA"
                        except Exception:
                            pass
                    # Se agregan los campos de señalización al objeto de cuota pendiente
                    first_pending_installment["retrasado"] = retrasado
                    first_pending_installment["label_fecha"] = label_fecha
                    await info_notify(
                        method_name=method_name,
                        client_id=client_id,
                        info_message=f"Cuota a pagar para crédito {id_credito}: {first_pending_installment}",
                    )

                # Construir el resumen final que se retorna al frontend.
                # - cuotas_pagadas: cuotas ya saldadas (estado 1)
                # - cuotas_pendientes_total: suma de pendientes (3) + vencidas (4), es decir
                #   todas las que aún no han sido pagadas
                # - pago_pendiente: detalle de la primera cuota que el cliente debe pagar
                return_data = {
                    "total_cuotas": len(installments),
                    "cuotas_pagadas": count_paid,
                    "cuotas_pendientes_total": count_pending + count_expired,
                    "dias_de_atraso": resumen.get("debtDays", 0),
                    "pendientes_estado_3": count_pending,
                    "vencidos_estado_4": count_expired,
                    "pago_pendiente": first_pending_installment,
                }
                logger.info(f"resumen de las cuotas pendientes : {return_data}")

                logger.info(
                    f"Resumen de installments para crédito {id_credito}: {count_paid} pagadas, {count_pending} pendientes, {count_expired} vencidas"
                )

            await info_notify(
                method_name=method_name,
                client_id=client_id,
                info_message=f"Información de pagos en mora obtenida exitosamente para crédito: {id_credito}",
            )
            return JSONResponse(
                status_code=200,
                content={
                    "estado": "success",
                    "mensaje": "Información de pagos en mora obtenida correctamente",
                    "data": return_data,
                },
            )

        elif status_code == 401:
            error_msg = "Error de autenticación: token inválido o expirado"
            logger.error(error_msg)
            await error_notify(method_name, client_id, error_msg)
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=error_msg,
                http_code=401,
                tipo="error",
            )
            return JSONResponse(
                status_code=401,
                content={
                    "estado": "error",
                    "codigo_error": "AuthenticationError",
                    "mensaje": "Error de autenticación",
                    "detalles": error_msg,
                },
            )

        elif status_code == 404:
            error_msg = f"Crédito no encontrado: {id_credito}"
            logger.error(error_msg)
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=error_msg,
                http_code=404,
                tipo="error",
            )
            return JSONResponse(
                status_code=404,
                content={
                    "estado": "error",
                    "codigo_error": "NotFoundError",
                    "mensaje": "Crédito no encontrado",
                    "detalles": error_msg,
                },
            )

        elif status_code == 503:
            # service unavailable, puede ser temporal, no notificar como error crítico
            error_msg = f"Servicio no disponible temporalmente: {response_data} \n"
            logger.warning(error_msg)
            error_msg = f"Error en la consulta: {response_data}"
            await error_notify(method_name, client_id, error_msg)
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=error_msg,
                http_code=status_code,
                tipo="error",
            )
            return JSONResponse(
                status_code=status_code,
                content={
                    "estado": "service_unavailable",
                    "codigo_error": f"HttpError{status_code}",
                    "mensaje": f"Servicio no disponible temporalmente",
                    "detalles": error_msg,
                },
            )

    except ValueError as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de validación: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error de validación: {str(e)}",
            http_code=400,
            tipo="error",
            traceback_str=error_traceback,
        )
        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "codigo_error": "ValidationError",
                "mensaje": "Error de validación",
                "detalles": str(e),
            },
        )

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error general en obtener_pagos_mora: {str(e)}", exc_info=True)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error general: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback,
        )
        await error_notify(method_name, client_id, f"Error general: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "codigo_error": "InternalError",
                "mensaje": "Error interno del servidor",
                "detalles": "Nuestro equipo técnico ha sido notificado",
            },
        )


# endpoints para gestion de servicios externos ###
@app.post(
    "/servicios-externos", tags=["Servicios Externos"], summary="Crear servicio externo"
)
async def crear_servicio_externo_endpoint(payload: ServicioExternoCreateRequest):
    try:
        service_id = await crear_servicio_externo(payload.model_dump())
        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "message": "Servicio externo creado",
                "id": service_id,
            },
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Error creando servicio externo: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
            },
        )


@app.put(
    "/servicios-externos/{codigo}",
    tags=["Servicios Externos"],
    summary="Actualizar servicio externo",
)
async def actualizar_servicio_externo_endpoint(
    codigo: str, payload: ServicioExternoUpdateRequest
):
    data = payload.model_dump(exclude_unset=True)
    if not data:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "No se enviaron campos para actualizar",
            },
        )

    try:
        updated = await actualizar_servicio_externo(codigo, data)
        if not updated:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": "Servicio no encontrado",
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Servicio actualizado",
            },
        )
    except Exception as exc:
        logger.error("Error actualizando servicio externo: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
            },
        )


@app.patch(
    "/servicios-externos/{codigo}/desactivar",
    tags=["Servicios Externos"],
    summary="Desactivar servicio externo",
)
async def desactivar_servicio_externo_endpoint(codigo: str):
    try:
        updated = await actualizar_servicio_externo(codigo, {"activo": 0})
        if not updated:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": "Servicio no encontrado",
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Servicio desactivado",
            },
        )
    except Exception as exc:
        logger.error("Error desactivando servicio externo: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
            },
        )


@app.get(
    "/servicios-externos/{codigo}",
    tags=["Servicios Externos"],
    summary="Obtener servicio externo por codigo",
)
async def obtener_servicio_externo_endpoint(codigo: str):
    try:
        servicio = await obtener_servicio_externo_por_codigo(codigo)
        if not servicio:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": "Servicio no encontrado",
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "data": servicio,
            },
        )
    except Exception as exc:
        logger.error("Error obteniendo servicio externo: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
            },
        )


@app.get("/logs")
async def get_logs(limit: int = 20):
    """
    Devuelve los últimos logs enviados (correo + Telegram).
    Se puede consultar por Postman o navegador.
    Manejo seguro cuando la caché esté vacía o ocurra un error.
    """
    try:
        logs = await get_cached_logs(limit)
        if not logs:
            return {"count": 0, "logs": []}
        return {"count": len(logs), "logs": logs}
    except Exception as e:
        logger.exception("Error al obtener logs desde la caché")
        # No lanzar excepción para no interrumpir el servidor; devolver estructura vacía
        return {"count": 0, "logs": []}

    # consultar logs de errores desde la base de datos con filtros


@app.post(
    "/consultar-logs", tags=["Logs"], summary="Consultar todos los logs del sistema"
)
async def consultar_logs(filtros: ConsultaLogsRequest):
    """
    Endpoint único para consultar TODOS los logs almacenados con múltiples filtros opcionales.

    **Filtros disponibles (todos opcionales):**

    - **fecha**: Fecha exacta en formato D-M-Y (ej: "12-02-2025")
    - **fecha_inicio**: Inicio de rango en formato D-M-Y (ej: "01-01-2025")
    - **fecha_fin**: Fin de rango en formato D-M-Y (ej: "31-12-2025")
    - **log_id**: ID específico del log
    - **metodo**: Nombre del método/función (búsqueda parcial)
    - **client_id**: ID del cliente (búsqueda parcial)
    - **codigo_http**: Código HTTP del error (ej: 500, 401, 409)
    - **tipo**: "error" o "info"
    - **limite**: Máximo de resultados (default 100, máximo 1000)
    - **offset**: Desplazamiento para paginación (default 0)

    **Ejemplos de uso:**

    1. Obtener todos los logs (sin filtros):
    ```json
    {}
    ```

    2. Obtener errores de una fecha específica:
    ```json
    {
        "fecha": "12-02-2025",
        "tipo": "error",
        "limite": 50
    }
    ```

    3. Obtener logs en un rango de fechas:
    ```json
    {
        "fecha_inicio": "01-01-2025",
        "fecha_fin": "31-01-2025",
        "tipo": "error",
        "limite": 100
    }
    ```

    4. Obtener logs de un cliente específico:
    ```json
    {
        "client_id": "cliente_123",
        "limite": 20,
        "offset": 0
    }
    ```

    5. Obtener logs de un método específico:
    ```json
    {
        "metodo": "create_payable",
        "tipo": "error",
        "limite": 30
    }
    ```

    6. Obtener logs con código HTTP 500:
    ```json
    {
        "codigo_http": 500,
        "limite": 25
    }
    ```

    7. Con paginación:
    ```json
    {
        "metodo": "registrar_renovacion",
        "limite": 50,
        "offset": 50
    }
    ```
    """
    try:
        # Validar que limite no exceda 1000
        limite = min(filtros.limite, 1000)

        # Validar formato de fechas si se proporcionan
        def validar_fecha_dmy(fecha_str: str, nombre_parametro: str) -> bool:
            """Valida que el formato de fecha sea D-M-Y"""
            try:
                partes = fecha_str.split("-")
                if len(partes) != 3:
                    return False
                dia, mes, año = partes
                # Validar que sean números
                dia_int = int(dia)
                mes_int = int(mes)
                año_int = int(año)
                # Validaciones básicas
                if (
                    not (1 <= dia_int <= 31)
                    or not (1 <= mes_int <= 12)
                    or año_int < 2000
                ):
                    return False
                return True
            except Exception:
                return False

        if filtros.fecha and not validar_fecha_dmy(filtros.fecha, "fecha"):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Formato de fecha inválido",
                    "parametro": "fecha",
                    "formato_esperado": "D-M-Y",
                    "ejemplo": "12-02-2025",
                },
            )

        if filtros.fecha_inicio and not validar_fecha_dmy(
            filtros.fecha_inicio, "fecha_inicio"
        ):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Formato de fecha_inicio inválido",
                    "parametro": "fecha_inicio",
                    "formato_esperado": "D-M-Y",
                    "ejemplo": "01-01-2025",
                },
            )

        if filtros.fecha_fin and not validar_fecha_dmy(filtros.fecha_fin, "fecha_fin"):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Formato de fecha_fin inválido",
                    "parametro": "fecha_fin",
                    "formato_esperado": "D-M-Y",
                    "ejemplo": "31-12-2025",
                },
            )

        # Validar que tipo sea "error" o "info"
        if filtros.tipo and filtros.tipo.lower() not in ["error", "info"]:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "El parámetro 'tipo' debe ser 'error' o 'info'",
                    "parametro": "tipo",
                    "valores_validos": ["error", "info"],
                },
            )

        # Llamar a la función de consulta
        resultado = await consultar_logs_filtrados(
            fecha=filtros.fecha,
            fecha_inicio=filtros.fecha_inicio,
            fecha_fin=filtros.fecha_fin,
            log_id=filtros.log_id,
            metodo=filtros.metodo,
            client_id=filtros.client_id,
            codigo_http=filtros.codigo_http,
            tipo=filtros.tipo,
            limite=limite,
            offset=filtros.offset,
        )

        # Verificar si hubo error en la consulta
        if "error" in resultado:
            logger.error(f"Error en consulta de logs: {resultado['error']}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Error al consultar los logs",
                    "detail": resultado["error"],
                },
            )

        # Respuesta exitosa
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Logs consultados exitosamente",
                "total": resultado["total"],
                "registros_retornados": len(resultado["registros"]),
                "limite": resultado["limite"],
                "offset": resultado["offset"],
                "hay_mas": (resultado["offset"] + resultado["limite"])
                < resultado["total"],
                "datos": resultado["registros"],
            },
        )

    except Exception as e:
        logger.error(f"Error en endpoint consultar_logs: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
                "detail": str(e),
            },
        )


# ============================================================
# Bitrix24 - Procesamiento de llamadas automatizadas de cartera
# ============================================================


@dataclass
class BitrixLookupCriteria:
    """
    Criterios usados para buscar el contacto del cliente en Bitrix24.

    Solo se usan datos que pertenecen al cliente:
    - CEDULA
    - TELEFONO
    - CORREO

    No se usa WHATSAPP_DISPONIBLE porque corresponde a otra entidad/canal.
    """

    cedula: Optional[str]
    telefono_variants: List[str]
    correo: Optional[str]
    nombre: Optional[str]
    id_libranza: Optional[str]


# ============================================================
# Schemas / Modelos de entrada para webhook de llamada Bitrix
# ============================================================


class CallInputVariables(BaseModel):
    """
    Variables de entrada de la llamada.

    Estas variables vienen desde la base/campaña.
    Se usan para consultar Bitrix24.

    Datos usados para buscar cliente:
    - CEDULA -> UF_CRM_1697774324
    - TELEFONO -> phone
    - CORREO -> email

    Datos NO usados para buscar cliente:
    - WHATSAPP_DISPONIBLE, porque pertenece a otra entidad/canal.
    """

    MORA: Optional[Union[int, float, str]] = None
    CUOTA: Optional[Union[int, float, str]] = None
    CEDULA: Union[int, str]
    CORREO: Optional[str] = None
    MOTIVO: Optional[str] = None
    NOMBRE: Optional[str] = None
    CAMPANIA: Optional[str] = None
    TELEFONO: Optional[Union[int, str]] = None
    PAGADURIA: Optional[str] = None
    MORA_TOTAL: Optional[Union[int, float, str]] = None
    ID_LIBRANZA: Optional[str] = None
    OBSERVACIONES: Optional[str] = None
    TOTAL_CREDITO: Optional[Union[int, float, str]] = None
    CORREO_CARTERA: Optional[str] = None
    WHATSAPP_DISPONIBLE: Optional[Union[int, str]] = None

    @field_validator("CEDULA", mode="before")
    @classmethod
    def validate_cedula_required(cls, value):
        """
        Valida que CEDULA venga presente y no vacía.
        Es el dato principal para buscar el contacto en Bitrix.
        """

        if value is None or str(value).strip() == "":
            raise ValueError(
                "CEDULA es obligatoria para consultar el cliente en Bitrix."
            )
        return value

    @field_validator("CORREO", "CORREO_CARTERA", mode="before")
    @classmethod
    def normalize_email(cls, value):
        """
        Normaliza correos a minúscula si vienen presentes.
        No usa EmailStr para evitar dependencia extra email-validator.
        """

        if value is None:
            return value

        value = str(value).strip()

        if value == "":
            return None

        return value.lower()

    @field_validator("TELEFONO", mode="before")
    @classmethod
    def validate_telefono_optional(cls, value):
        """
        TELEFONO no es obligatorio si existe CEDULA,
        pero si viene no debe ser vacío.
        """

        if value is None:
            return value

        if str(value).strip() == "":
            return None

        return value


class ExtractedVariableSchema(BaseModel):
    """
    Variable saliente extraída de la llamada.

    Estas variables se usan para validar intención de pago.
    La descripción puede venir desde el proveedor, pero no se usa.
    """

    name: str = Field(..., description="Nombre de la variable extraída.")
    type: Optional[str] = Field(None, description="Tipo declarado por el proveedor.")
    value: Any = Field(None, description="Valor extraído.")
    description: Optional[str] = Field(
        None, description="Descripción informativa; no se procesa."
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        """
        Valida que el nombre de la variable extraída no venga vacío.
        """

        if not value or not str(value).strip():
            raise ValueError(
                "El campo name de extracted_variables no puede estar vacío."
            )
        return value


class CallCompletedBitrixRequest(BaseModel):
    """
    Request completo recibido desde la llamada automatizada.

    input_variables:
        Datos de origen/campaña. Se usan para consultar Bitrix.

    extracted_variables:
        Datos generados por la IA o análisis de llamada. Se usan para validar
        intención positiva de pago.
    """

    input_variables: CallInputVariables
    extracted_variables: List[ExtractedVariableSchema]

    @field_validator("extracted_variables")
    @classmethod
    def validate_extracted_variables_not_empty(cls, value):
        """
        Valida que venga al menos una variable saliente.
        """

        if not value:
            raise ValueError("extracted_variables no puede venir vacío.")
        return value


class BitrixDebugSearchClientRequest(BaseModel):
    """
    Request de prueba para consultar un cliente en Bitrix
    sin pasar por validación de intención de pago.
    """

    CEDULA: Union[int, str]
    TELEFONO: Optional[Union[int, str]] = None
    CORREO: Optional[str] = None
    WHATSAPP_DISPONIBLE: Optional[Union[int, str]] = None

    @field_validator("CEDULA", mode="before")
    @classmethod
    def validate_debug_cedula_required(cls, value):
        """
        Valida que CEDULA venga presente en prueba de búsqueda.
        """

        if value is None or str(value).strip() == "":
            raise ValueError("CEDULA es obligatoria.")
        return value

    @field_validator("CORREO", mode="before")
    @classmethod
    def normalize_debug_email(cls, value):
        """
        Normaliza correo de prueba.
        """

        if value is None:
            return value

        value = str(value).strip()

        if value == "":
            return None

        return value.lower()


class BitrixAPIError(Exception):
    """
    Excepción controlada para errores de comunicación con Bitrix24.
    """

    def __init__(
        self,
        message: str,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        response_data: Any = None,
    ):
        self.message = message
        self.method = method
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(message)


def normalize_key(value: Any) -> str:
    """
    Normaliza nombres de variables para soportar tildes, espacios y mayúsculas.

    Ejemplos:
    - "contestó" -> "contesto"
    - "Interés Pagar" -> "interes_pagar"
    """
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def normalize_text(value: Any) -> str:
    """
    Normaliza valores de texto para reglas de validación.

    Ejemplos:
    - "Sí" -> "si"
    - "acuerdo con fecha" -> "acuerdo_con_fecha"
    - True -> "true"
    """
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def clean_str(value: Any) -> Optional[str]:
    """
    Convierte cualquier valor a string limpio.

    Retorna None si viene vacío o nulo.
    """
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def get_input(input_variables: Dict[str, Any], name: str, default: Any = None) -> Any:
    """
    Obtiene una variable desde input_variables ignorando tildes,
    mayúsculas, minúsculas y separadores.
    """
    target = normalize_key(name)

    for key, value in input_variables.items():
        if normalize_key(key) == target:
            return value

    return default


def is_yes(value: Any) -> bool:
    """
    Retorna True si el valor representa afirmación.
    """
    return normalize_text(value) in {
        "si",
        "s",
        "yes",
        "y",
        "true",
        "1",
        "afirmativo",
    }


def is_true(value: Any) -> bool:
    """
    Retorna True si el valor representa booleano verdadero.
    """
    if isinstance(value, bool):
        return value

    return normalize_text(value) in {
        "true",
        "si",
        "s",
        "yes",
        "1",
        "contesto",
    }


def build_output_map(extracted_variables: Any) -> Dict[str, Any]:
    """
    Convierte extracted_variables en diccionario normalizado.

    Soporta dos formatos:
    1. Lista:
       [{"name": "interes_pagar", "value": "Si"}]

    2. Diccionario:
       {"interes_pagar": "Si"}
    """
    output: Dict[str, Any] = {}

    if isinstance(extracted_variables, list):
        for item in extracted_variables:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            value = item.get("value")

            if name:
                output[normalize_key(name)] = value

        return output

    if isinstance(extracted_variables, dict):
        for key, value in extracted_variables.items():
            output[normalize_key(key)] = value

        return output

    return output


def normalize_phone_variants(value: Any) -> List[str]:
    """
    Genera variantes del teléfono del cliente para búsqueda en Bitrix.

    Prioridad:
    1. Formato con indicativo Colombia sin '+': 573002613153
    2. Número nacional: 3002613153
    3. Formato internacional con '+': +573002613153

    No usar WHATSAPP_DISPONIBLE aquí.
    """

    if value is None:
        return []

    raw = str(value).strip()
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return []

    variants: List[str] = []

    if len(digits) == 10 and digits.startswith("3"):
        variants.append(f"57{digits}")
        variants.append(digits)
        variants.append(f"+57{digits}")

    elif len(digits) == 12 and digits.startswith("57"):
        variants.append(digits)
        variants.append(digits[-10:])
        variants.append(f"+{digits}")

    else:
        variants.append(digits)
        if raw.startswith("+"):
            variants.append(raw)

    # Quitar duplicados conservando orden
    unique_variants: List[str] = []
    for item in variants:
        if item not in unique_variants:
            unique_variants.append(item)

    return unique_variants


def build_bitrix_lookup_criteria(
    input_variables: Dict[str, Any],
) -> BitrixLookupCriteria:
    """
    Construye criterios de búsqueda desde input_variables.

    Usa:
    - CEDULA
    - TELEFONO
    - CORREO

    Ignora:
    - WHATSAPP_DISPONIBLE, porque no pertenece al cliente.
    """
    cedula = clean_str(get_input(input_variables, "CEDULA"))
    telefono = get_input(input_variables, "TELEFONO")
    correo = clean_str(get_input(input_variables, "CORREO"))
    nombre = clean_str(get_input(input_variables, "NOMBRE"))
    id_libranza = clean_str(get_input(input_variables, "ID_LIBRANZA"))

    return BitrixLookupCriteria(
        cedula=cedula,
        telefono_variants=normalize_phone_variants(telefono),
        correo=correo.lower() if correo else None,
        nombre=nombre,
        id_libranza=id_libranza,
    )


def build_static_contact_select() -> List[str]:
    """
    Construye el select estático de contacto.

    Campos validados:
    - id
    - name
    - lastName
    - email
    - phone

    Campo de cédula:
    - UF_CRM_1697774324
    """
    select = CONTACT_LOOKUP_SELECT.copy()

    if BITRIX_CONTACT_CEDULA_FIELD:
        select.append(BITRIX_CONTACT_CEDULA_FIELD)

    return select


POSITIVE_GESTION_VALUES = {
    "pago_hoy",
    "promesa_pago",
    "pago_fraccionado",
    "plan_mensual",
    "acuerdo_con_fecha",
    "acuerdo_pago",
    "acuerdo",
}

NEGATIVE_GESTION_VALUES = {
    "no_acuerdo",
    "sin_respuesta",
    "cierre_sin_acuerdo",
    "numero_equivocado",
}

VALIDATION_OK_VALUES = {
    "validado",
    "valido",
    "validada",
}


def evaluate_payment_intent(output_vars: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evalúa si la llamada confirma intención real de pago.

    Reglas:
    - Debió contestar un humano.
    - La identidad debe estar validada.
    - Debe existir pago_hoy=Si o interes_pagar=Si con acuerdo/promesa/fecha.
    """
    resultvalidacion = normalize_text(output_vars.get("resultvalidacion"))
    gestion_final = normalize_text(output_vars.get("gestion_final"))
    objetivo = normalize_text(output_vars.get("objetivo"))

    interes_pagar = output_vars.get("interes_pagar")
    pago_hoy = output_vars.get("pago_hoy")
    fecha_acuerdo_pago = output_vars.get("fechacuerdopago")
    contesto = output_vars.get("contesto")

    detalle_acuerdo = normalize_text(output_vars.get("detalle_acuerdo"))
    resumen = normalize_text(output_vars.get("resumen"))
    resumen_llamada = normalize_text(output_vars.get("resumenllamada"))
    objdetallada = normalize_text(output_vars.get("objdetallada"))
    obsjdetallada = normalize_text(output_vars.get("obsjdetallada"))

    blockers: List[str] = []
    warnings: List[str] = []
    positive_signals: List[str] = []

    if not is_true(contesto):
        blockers.append(
            "La llamada no fue contestada por un humano o no hubo conversación efectiva."
        )
    else:
        positive_signals.append("contesto=true")

    if resultvalidacion not in VALIDATION_OK_VALUES:
        blockers.append(
            f"Identidad no validada: resultvalidacion={resultvalidacion or 'vacio'}."
        )
    else:
        positive_signals.append("resultvalidacion=validado")

    if objetivo and objetivo != "cobro_libranza":
        warnings.append(f"Objetivo diferente al esperado: objetivo={objetivo}.")

    if gestion_final in NEGATIVE_GESTION_VALUES:
        blockers.append(f"Gestión final negativa: gestion_final={gestion_final}.")

    has_positive_gestion = gestion_final in POSITIVE_GESTION_VALUES
    has_payment_date = bool(str(fecha_acuerdo_pago or "").strip())

    agreement_text = " ".join(
        [
            detalle_acuerdo,
            resumen,
            resumen_llamada,
            objdetallada,
            obsjdetallada,
        ]
    )

    has_agreement_text = (
        "acuerdo" in agreement_text
        and "sin_acuerdo" not in agreement_text
        and "no_acuerdo" not in agreement_text
    )

    if is_yes(interes_pagar):
        positive_signals.append("interes_pagar=Si")

    if is_yes(pago_hoy):
        positive_signals.append("pago_hoy=Si")

    if has_positive_gestion:
        positive_signals.append(f"gestion_final={gestion_final}")

    if has_payment_date:
        positive_signals.append(f"fechacuerdopago={fecha_acuerdo_pago}")

    if has_agreement_text:
        positive_signals.append("texto_contiene_acuerdo")

    payment_intent = is_yes(pago_hoy) or (
        is_yes(interes_pagar)
        and (has_positive_gestion or has_payment_date or has_agreement_text)
    )

    if not payment_intent:
        blockers.append(
            "No hay intención de pago suficiente. Se requiere pago_hoy=Si "
            "o interes_pagar=Si con acuerdo, promesa o fecha de pago."
        )

    approved = len(blockers) == 0

    return {
        "approved": approved,
        "payment_intent": payment_intent,
        "positive_signals": positive_signals,
        "blockers": blockers,
        "warnings": warnings,
        "normalized": {
            "resultvalidacion": resultvalidacion,
            "gestion_final": gestion_final,
            "objetivo": objetivo,
            "interes_pagar": normalize_text(interes_pagar),
            "pago_hoy": normalize_text(pago_hoy),
            "fechacuerdopago": fecha_acuerdo_pago,
            "contesto": is_true(contesto),
        },
    }


async def bitrix_call(
    method: str,
    payload: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
    retry_delay_seconds: float = 1.0,
) -> Dict[str, Any]:
    """
    Ejecuta POST contra un método REST de Bitrix24 con manejo de errores y reintentos.

    Reintenta cuando:
    - timeout
    - error de conexión
    - HTTP 429
    - HTTP 5xx

    No reintenta cuando:
    - Bitrix responde HTTP 200, aunque no haya resultados.
    - Bitrix responde error funcional 4xx distinto de 429.
    """

    payload = payload or {}

    if not BITRIX_BASE_URL:
        raise BitrixAPIError(
            message="BITRIX_BASE_URL no está configurado.",
            method=method,
        )

    url = f"{BITRIX_BASE_URL}/{method}.json"

    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        logger.info(
            "Bitrix request iniciado | method=%s | attempt=%s/%s | payload=%s",
            method,
            attempt,
            max_retries,
            json.dumps(payload, ensure_ascii=False, default=str),
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload)

            try:
                data = response.json()
            except ValueError as exc:
                logger.error(
                    "Bitrix respondió contenido no JSON | method=%s | status=%s | text=%s",
                    method,
                    response.status_code,
                    response.text[:1000],
                )
                raise BitrixAPIError(
                    message="Bitrix24 respondió contenido no JSON.",
                    method=method,
                    status_code=response.status_code,
                    response_data=response.text[:1000],
                ) from exc

            if response.status_code == 200:
                if "error" in data:
                    logger.error(
                        "Bitrix respondió error de API | method=%s | error=%s | description=%s",
                        method,
                        data.get("error"),
                        data.get("error_description"),
                    )
                    raise BitrixAPIError(
                        message=data.get("error_description")
                        or data.get("error")
                        or "Error Bitrix24.",
                        method=method,
                        status_code=response.status_code,
                        response_data=data,
                    )

                logger.info(
                    "Bitrix request exitoso | method=%s | attempt=%s | status=200",
                    method,
                    attempt,
                )
                return data

            if response.status_code == 429 or response.status_code >= 500:
                logger.warning(
                    "Bitrix respondió error reintentable | method=%s | status=%s | attempt=%s/%s | response=%s",
                    method,
                    response.status_code,
                    attempt,
                    max_retries,
                    json.dumps(data, ensure_ascii=False, default=str),
                )

                last_error = BitrixAPIError(
                    message="Bitrix24 respondió error reintentable.",
                    method=method,
                    status_code=response.status_code,
                    response_data=data,
                )

                if attempt < max_retries:
                    await asyncio.sleep(retry_delay_seconds * attempt)
                    continue

                raise last_error

            logger.error(
                "Bitrix respondió error HTTP no reintentable | method=%s | status=%s | response=%s",
                method,
                response.status_code,
                json.dumps(data, ensure_ascii=False, default=str),
            )

            raise BitrixAPIError(
                message="Bitrix24 respondió error HTTP no reintentable.",
                method=method,
                status_code=response.status_code,
                response_data=data,
            )

        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning(
                "Timeout llamando Bitrix | method=%s | attempt=%s/%s | error=%s",
                method,
                attempt,
                max_retries,
                str(exc),
            )

            if attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds * attempt)
                continue

            raise BitrixAPIError(
                message="Timeout al llamar Bitrix24.",
                method=method,
            ) from exc

        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "Error de conexión llamando Bitrix | method=%s | attempt=%s/%s | error=%s",
                method,
                attempt,
                max_retries,
                str(exc),
            )

            if attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds * attempt)
                continue

            raise BitrixAPIError(
                message="Error de conexión al llamar Bitrix24.",
                method=method,
            ) from exc

    raise BitrixAPIError(
        message=f"No fue posible completar la llamada a Bitrix24. Último error: {last_error}",
        method=method,
    )


def extract_bitrix_items(bitrix_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extrae items desde respuestas de crm.item.list.
    """
    result = bitrix_response.get("result") or {}

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]

    if isinstance(result, list):
        return result

    return []


async def find_contact_by_cedula(cedula: str) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por cédula/documento de identidad.

    Usa UF_CRM_1697774324 solo como filtro.
    No lo incluye en select para evitar respuestas sin id.
    """

    payload = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "select": build_contact_lookup_select(),
        "filter": {
            BITRIX_CONTACT_CEDULA_FIELD: cedula,
        },
    }

    logger.info(
        "Buscando contacto por cédula | cedula=%s | field=%s | payload=%s",
        cedula,
        BITRIX_CONTACT_CEDULA_FIELD,
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    data = await bitrix_call("crm.item.list", payload)
    items = extract_bitrix_items(data)

    logger.info(
        "Respuesta búsqueda cédula | items=%s",
        json.dumps(items, ensure_ascii=False, default=str),
    )

    if not items:
        logger.info("Bitrix respondió 200 pero no encontró contacto por cédula.")
        return None

    first_item = items[0]

    if not is_valid_bitrix_contact(first_item):
        logger.warning(
            "Bitrix encontró item por cédula pero sin id válido | item=%s",
            json.dumps(first_item, ensure_ascii=False, default=str),
        )
        return None

    logger.info("Contacto encontrado por cédula | id=%s", first_item.get("id"))
    return first_item


async def find_contact_by_phone(telefono_variants: List[str]) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por teléfono del cliente.
    Estrategia:
    1. Intenta cada variante individualmente con el formato que funcionó en Bitrix:
        filter: {"@phone": 573002613153}
    2. Si no encuentra, intenta con array como fallback:
        filter: {"@phone": ["573002613153", "3002613153", "+573002613153"]}

    Detiene la búsqueda apenas obtiene un contacto con id válido.
    """

    if not telefono_variants:
        logger.info("No hay teléfono del cliente para consultar.")
        return None

    select = build_contact_lookup_select()

    logger.info("Buscando contacto por teléfono | variants=%s", telefono_variants)

    # Intento 1: variante por variante, como tu prueba manual.
    for phone in telefono_variants:
        phone_value: Any = phone

        # Si es puramente numérico, enviarlo como int para replicar tu prueba:
        # "@phone": 573002613153
        if str(phone).isdigit():
            phone_value = int(phone)

        payload = {
            "entityTypeId": CONTACT_ENTITY_TYPE_ID,
            "select": select,
            "filter": {
                "@phone": phone_value,
            },
        }

        logger.info(
            "Buscando contacto por teléfono individual | phone=%s | payload=%s",
            phone,
            json.dumps(payload, ensure_ascii=False, default=str),
        )

        data = await bitrix_call("crm.item.list", payload)
        items = extract_bitrix_items(data)

        logger.info(
            "Respuesta búsqueda teléfono individual | phone=%s | items=%s",
            phone,
            json.dumps(items, ensure_ascii=False, default=str),
        )

        if not items:
            continue

        first_item = items[0]

        if is_valid_bitrix_contact(first_item):
            logger.info(
                "Contacto válido encontrado por teléfono individual | phone=%s | id=%s",
                phone,
                first_item.get("id") or first_item.get("ID"),
            )
            return first_item

        logger.warning(
            "Item encontrado por teléfono, pero sin id válido | phone=%s | item=%s",
            phone,
            json.dumps(first_item, ensure_ascii=False, default=str),
        )

    # Intento 2: fallback con array.
    payload = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "select": select,
        "filter": {
            "@phone": telefono_variants,
        },
    }

    logger.info(
        "Buscando contacto por teléfono en array fallback | payload=%s",
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    data = await bitrix_call("crm.item.list", payload)
    items = extract_bitrix_items(data)

    logger.info(
        "Respuesta búsqueda teléfono fallback | items=%s",
        json.dumps(items, ensure_ascii=False, default=str),
    )

    if not items:
        logger.info("Bitrix respondió 200 pero no encontró contacto por teléfono.")
        return None

    first_item = items[0]

    if not is_valid_bitrix_contact(first_item):
        logger.warning(
            "Bitrix encontró item por teléfono pero sin id válido | item=%s",
            json.dumps(first_item, ensure_ascii=False, default=str),
        )
        return None

    logger.info("Contacto encontrado por teléfono fallback | id=%s", first_item.get("id"))
    return first_item


async def find_contact_by_email(correo: str) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por correo electrónico.

    Usa select limpio para garantizar que Bitrix devuelva id.
    """

    payload = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "select": build_contact_lookup_select(),
        "filter": {
            "email": correo,
        },
    }

    logger.info(
        "Buscando contacto por correo | correo=%s | payload=%s",
        correo,
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    data = await bitrix_call("crm.item.list", payload)
    items = extract_bitrix_items(data)

    logger.info(
        "Respuesta búsqueda correo | items=%s",
        json.dumps(items, ensure_ascii=False, default=str),
    )

    if not items:
        logger.info("Bitrix respondió 200 pero no encontró contacto por correo.")
        return None

    first_item = items[0]

    if not is_valid_bitrix_contact(first_item):
        logger.warning(
            "Bitrix encontró item por correo pero sin id válido | item=%s",
            json.dumps(first_item, ensure_ascii=False, default=str),
        )
        return None

    logger.info("Contacto encontrado por correo | id=%s", first_item.get("id"))
    return first_item


async def find_contact_from_input_variables(
    input_variables: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Busca contacto en Bitrix usando datos del cliente.

    Nuevo orden requerido:
    1. TELEFONO -> phone
    2. CEDULA -> UF_CRM_1697774324
    3. CORREO -> email, como fallback opcional

    Reglas:
    - Si Bitrix responde 200 y encuentra contacto con id válido, se detiene.
    - Si Bitrix responde 200 pero no hay contacto o no trae id, se pasa al siguiente criterio.
    - Si Bitrix falla por timeout/conexión/5xx, se reintenta.
    """

    criteria = build_bitrix_lookup_criteria(input_variables)

    logger.info(
        "Criterios de búsqueda construidos | %s",
        json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
    )

    if criteria.telefono_variants:
        contact = await find_contact_by_phone(criteria.telefono_variants)

        if is_valid_bitrix_contact(contact):
            logger.info(
                "Contacto válido encontrado por teléfono | id=%s", contact.get("id")
            )
            return contact

        logger.info(
            "No se obtuvo contacto válido por teléfono. Se intentará por cédula."
        )

    if criteria.cedula:
        contact = await find_contact_by_cedula(criteria.cedula)

        if is_valid_bitrix_contact(contact):
            logger.info(
                "Contacto válido encontrado por cédula | id=%s", contact.get("id")
            )
            return contact

        logger.info("No se obtuvo contacto válido por cédula. Se intentará por correo.")

    if criteria.correo:
        contact = await find_contact_by_email(criteria.correo)

        if is_valid_bitrix_contact(contact):
            logger.info(
                "Contacto válido encontrado por correo | id=%s", contact.get("id")
            )
            return contact

    logger.warning(
        "No se encontró contacto válido con TELEFONO, CEDULA ni CORREO | criteria=%s",
        json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
    )

    return None


async def create_deal_in_resultado_llamada_ia(
    input_variables: Dict[str, Any],
    output_vars: Dict[str, Any],
    contact: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Crea una tarjeta en HOR CARTERA / RESULTADO LLAMADA IA.

    Requiere contacto válido, porque debe enviar:
    contactIds: [contact_id]
    """

    if not is_valid_bitrix_contact(contact):
        raise BitrixAPIError(
            message="No se puede crear deal porque el contacto no tiene id válido.",
            method="crm.item.add",
            response_data=contact,
        )

    contact_id = int(contact.get("id") or contact.get("ID"))

    fields = build_resultado_llamada_ia_fields(
        input_variables=input_variables,
        output_vars=output_vars,
        contact_id=contact_id,
    )

    payload = {
        "entityTypeId": DEAL_ENTITY_TYPE_ID,
        "fields": fields,
        "useOriginalUfNames": "Y",
    }

    logger.info(
        "Creando deal en RESULTADO LLAMADA IA | payload=%s",
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    data = await bitrix_call("crm.item.add", payload)

    logger.info(
        "Deal creado correctamente en RESULTADO LLAMADA IA | response=%s",
        json.dumps(data, ensure_ascii=False, default=str),
    )

    return data.get("result")


def bitrix_api_error_to_http(exc: BitrixAPIError) -> HTTPException:
    """
    Convierte un BitrixAPIError en HTTPException.
    """
    return HTTPException(
        status_code=502,
        detail={
            "ok": False,
            "error_type": "bitrix_api_error",
            "message": exc.message,
            "bitrix_method": exc.method,
            "bitrix_status_code": exc.status_code,
            "bitrix_response": exc.response_data,
        },
    )


def is_valid_bitrix_contact(contact: Optional[Dict[str, Any]]) -> bool:
    """
    Valida que el contacto retornado por Bitrix sea utilizable.

    Un contacto sin id no sirve para crear contactIds en el deal.
    """

    if not contact:
        return False

    contact_id = contact.get("id") or contact.get("ID")

    if contact_id is None:
        logger.warning(
            "Bitrix retornó un contacto sin id. Se tratará como inválido | contact=%s",
            json.dumps(contact, ensure_ascii=False, default=str),
        )
        return False

    try:
        int(contact_id)
        return True
    except (TypeError, ValueError):
        logger.warning(
            "Bitrix retornó un id de contacto inválido | id=%s | contact=%s",
            contact_id,
            json.dumps(contact, ensure_ascii=False, default=str),
        )
        return False


def to_int_or_none(value: Any) -> Optional[int]:
    """
    Convierte valores numéricos a int.
    Soporta strings con comas, puntos y espacios.
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    cleaned = re.sub(r"[^\d-]", "", text)

    if not cleaned:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def build_resultado_llamada_ia_fields(
    input_variables: Dict[str, Any],
    output_vars: Dict[str, Any],
    contact_id: int,
) -> Dict[str, Any]:
    """
    Construye fields para crm.item.add en la etapa RESULTADO LLAMADA IA.

    Embudo:
    - HOR CARTERA -> categoryId = 0

    Etapa:
    - RESULTADO LLAMADA IA -> stageId = UC_L3QC2Y

    Contacto:
    - contactIds = [contact_id]
    """

    pagaduria = clean_str(get_input(input_variables, "PAGADURIA"))
    cuotas_en_mora = clean_str(get_input(input_variables, "CUOTAS_EN_MORA"))
    valor_total_mora = to_int_or_none(
        get_input(input_variables, "MORA_TOTAL") or get_input(input_variables, "MORA")
    )
    valor_confirmado = to_int_or_none(
        output_vars.get("valor_confirmado")
        or output_vars.get("valorconfirmado")
        or get_input(input_variables, "VALOR_CONFIRMADO")
    )

    resumen_llamada = clean_str(output_vars.get("resumenllamada"))
    detalle_acuerdo = clean_str(output_vars.get("detalle_acuerdo"))
    gestion_final = clean_str(output_vars.get("gestion_final"))
    motivo_principal = clean_str(output_vars.get("motivo_principal"))
    interes_pagar = clean_str(output_vars.get("interes_pagar"))
    pago_hoy = clean_str(output_vars.get("pago_hoy"))
    fecha_acuerdo_pago = clean_str(output_vars.get("fechacuerdopago"))

    id_libranza = clean_str(get_input(input_variables, "ID_LIBRANZA"))
    telefono_cliente = clean_str(get_input(input_variables, "TELEFONO"))
    cedula_cliente = clean_str(get_input(input_variables, "CEDULA"))

    nombre_cliente = clean_str(get_input(input_variables, "NOMBRE")) or "Cliente"
    title = f"Resultado llamada IA - {nombre_cliente}"

    if cedula_cliente:
        title += f" - CC {cedula_cliente}"

    fields: Dict[str, Any] = {
        "title": title,
        "categoryId": BITRIX_DEAL_CATEGORY_ID,
        "stageId": BITRIX_DEAL_STAGE_ID,
        # Relación con contacto encontrado.
        "contactId": contact_id,
        "contactIds": [contact_id],
        # Campos personalizados según mapeo entregado.
        "UF_CRM_1773780818920": pagaduria,
        "UF_CRM_1773781100010": cuotas_en_mora,
        "UF_CRM_1773841728850": valor_total_mora,
        "UF_CRM_1773841764862": valor_confirmado,
        "UF_CRM_1773841785299": resumen_llamada,
        "UF_CRM_1773841804045": detalle_acuerdo,
        "UF_CRM_1773871145738": None,
        "UF_CRM_1774627816455": None,
        "UF_CRM_1774627875235": None,
        "UF_CRM_1778864749613": gestion_final,
        "UF_CRM_1778864857436": motivo_principal,
        "UF_CRM_1778864879029": interes_pagar,
        "UF_CRM_1778864893956": pago_hoy,
        "UF_CRM_1778864914788": fecha_acuerdo_pago,
        "UF_CRM_1778865126453": id_libranza,
        "UF_CRM_1778865231524": telefono_cliente,
        "UF_CRM_1778865296676": cedula_cliente,
    }

    if BITRIX_ASSIGNED_BY_ID:
        fields["assignedById"] = BITRIX_ASSIGNED_BY_ID

    # Campo monto nativo del deal, opcional pero útil.
    if valor_total_mora is not None:
        fields["opportunity"] = valor_total_mora
        fields["currencyId"] = "COP"

    # Quitar claves con None solo si Bitrix te rechaza nulos.
    # Si quieres enviar explícitamente null, comenta esta línea.
    # fields = {k: v for k, v in fields.items() if v is not None}

    return fields


@app.post("/bitrix/debug/search-client")
async def bitrix_debug_search_client(payload: BitrixDebugSearchClientRequest):
    """
    Endpoint de prueba para buscar cliente en Bitrix usando input_variables.
    """

    method_name = "bitrix_debug_search_client"

    input_variables = payload.model_dump(exclude_none=True)
    criteria = build_bitrix_lookup_criteria(input_variables)

    logger.info(
        "Endpoint iniciado | POST /bitrix/debug/search-client | criteria=%s",
        json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
    )

    try:
        contact = await find_contact_from_input_variables(input_variables)

        if contact:
            logger.info(
                "Búsqueda Bitrix exitosa | contact=%s",
                json.dumps(contact, ensure_ascii=False, default=str),
            )
        else:
            logger.warning(
                "Búsqueda Bitrix sin resultados | criteria=%s",
                json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
            )

        return {
            "ok": True,
            "lookup_strategy": [
                "CEDULA -> UF_CRM_1697774324",
                "TELEFONO -> phone",
                "CORREO -> email",
            ],
            "ignored_fields": {
                "WHATSAPP_DISPONIBLE": "No se usa para buscar cliente porque pertenece a otra entidad/canal."
            },
            "lookup_criteria": criteria.__dict__,
            "client_found": contact is not None,
            "client": contact,
        }

    except BitrixAPIError as exc:
        logger.error("Error Bitrix en /bitrix/debug/search-client | %s", exc.message)
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.telefono_variants),
            exc.message,
        )
        raise bitrix_api_error_to_http(exc)

    except Exception as exc:
        logger.error("Error interno en /bitrix/debug/search-client | %s", str(exc))
        logger.error(traceback.format_exc())
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.telefono_variants),
            str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Error interno: {str(exc)}")


@app.post(
    "/webhooks/call-completed-bitrix",
    response_model=BitrixCallCompletedResponse,
)
async def call_completed_bitrix(request: Request):
    """
    Endpoint principal para recibir el JSON final de la llamada automatizada.
    """

    method_name = "call_completed_bitrix"

    logger.info("Endpoint iniciado | POST /webhooks/call-completed-bitrix")

    raw_body = await request.body()
    body_text = raw_body.decode("utf-8", errors="replace")

    logger.info(
        "JSON entrante (raw) | bytes=%s | body=%s",
        len(raw_body),
        body_text,
    )

    try:
        incoming_json = json.loads(body_text) if body_text.strip() else {}
        logger.info(
            "JSON entrante (parseado) | %s",
            json.dumps(incoming_json, ensure_ascii=False, indent=2, default=str),
        )
    except json.JSONDecodeError as exc:
        logger.error("JSON entrante inválido | %s", exc)
        raise HTTPException(status_code=400, detail=f"JSON inválido: {exc}")

    try:
        payload = BitrixCallCompletedRequest.model_validate(incoming_json)
    except ValidationError as exc:
        logger.error(
            "Error validación BitrixCallCompletedRequest | %s",
            json.dumps(exc.errors(), ensure_ascii=False, indent=2, default=str),
        )
        raise HTTPException(status_code=422, detail=exc.errors())

    input_variables = payload.input_variables.model_dump(exclude_none=True)
    extracted_variables = [
        item.model_dump(exclude_none=True) for item in payload.extracted_variables
    ]

    output_vars = build_output_map(extracted_variables)
    criteria = build_bitrix_lookup_criteria(input_variables)

    try:
        logger.info(
            "Input variables validadas | %s",
            json.dumps(input_variables, ensure_ascii=False, default=str),
        )

        logger.info(
            "Extracted variables validadas y normalizadas | %s",
            json.dumps(output_vars, ensure_ascii=False, default=str),
        )

        validation = evaluate_payment_intent(output_vars)

        logger.info(
            "Resultado validación intención de pago | %s",
            json.dumps(validation, ensure_ascii=False, default=str),
        )

        if not validation["approved"]:
            logger.info(
                "Validación no aprobada. No se consulta Bitrix | blockers=%s",
                json.dumps(validation["blockers"], ensure_ascii=False, default=str),
            )

            return {
                "ok": True,
                "action": "skipped_bitrix_lookup",
                "reason": "La validación de intención de pago no aprobó.",
                "validation": validation,
                "lookup_criteria": criteria.__dict__,
                "client_found": False,
                "client": None,
                "deal_created": False,
                "deal": None,
            }

        logger.info(
            "Validación aprobada. Iniciando consulta Bitrix | criteria=%s",
            json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
        )

        contact = await find_contact_from_input_variables(input_variables)

        if contact:
            logger.info(
                "CLIENTE ENCONTRADO EN BITRIX24 | %s",
                json.dumps(contact, ensure_ascii=False, default=str),
            )
        else:
            logger.warning(
                "CLIENTE NO ENCONTRADO EN BITRIX24 | criteria=%s",
                json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
            )

        deal_result = None

        if CREATE_DEAL_ON_VALID_PAYMENT:
            logger.info("CREATE_DEAL_ON_VALID_PAYMENT=true. Se intentará crear deal.")

            if not is_valid_bitrix_contact(contact):
                logger.error("No se crea deal porque no hay contacto válido con id.")
                raise HTTPException(
                    status_code=404,
                    detail={
                        "ok": False,
                        "message": "No se encontró contacto válido en Bitrix para asociar el deal.",
                        "lookup_criteria": criteria.__dict__,
                    },
                )

            deal_result = await create_deal_in_resultado_llamada_ia(
                input_variables=input_variables,
                output_vars=output_vars,
                contact=contact,
            )

            logger.info(
                "Deal creado desde webhook | %s",
                json.dumps(deal_result, ensure_ascii=False, default=str),
            )
        else:
            logger.info("CREATE_DEAL_ON_VALID_PAYMENT=false. No se crea deal.")

        return {
            "ok": True,
            "action": "processed_call_completed",
            "validation": validation,
            "lookup_criteria": criteria.__dict__,
            "ignored_fields": {
                "WHATSAPP_DISPONIBLE": "No se usó para buscar cliente porque pertenece a otra entidad/canal."
            },
            "client_found": contact is not None,
            "client": contact,
            "deal_created": deal_result is not None,
            "deal": deal_result,
        }

    except BitrixAPIError as exc:
        logger.error(
            "Error Bitrix en /webhooks/call-completed-bitrix | %s", exc.message
        )
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.telefono_variants),
            exc.message,
        )
        raise bitrix_api_error_to_http(exc)

    except HTTPException:
        raise

    except Exception as exc:
        logger.error("Error interno en /webhooks/call-completed-bitrix | %s", str(exc))
        logger.error(traceback.format_exc())
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.telefono_variants),
            str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Error interno: {str(exc)}")
    
    
    # --- Registro del módulo de pagos ---
from pagos.inicializador import registrar_modulo_pagos

registrar_modulo_pagos(app)
