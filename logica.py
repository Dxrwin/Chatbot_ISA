from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.responses import JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
    # --- Registro del módulo de pagos ---
from pagos.inicializador import registrar_modulo_pagos
from renovaciones.inicializador import registrar_modulo_renovaciones

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
#from utils.whatsapp_service import enviar_whatsapp_renovacion
#from utils.linea_credito_links import obtener_link_por_linea_credito
from models.bitrix_call_models import (
    BitrixCallCompletedRequest,
    BitrixCallCompletedResponse,
    BitrixProcessDestinationRequest,
    BitrixProcessFieldMappingRequest,
    #BitrixDebugSearchClientRequest,
)
from pagos.aplicacion.servicios.servicio_ordenes_pago import ServicioOrdenesPago
from pagos.excepciones import ErrorProveedorPago
from pagos.utilidades.auditoria_http import construir_contexto_auditoria_http
from pagos.utilidades.modelos import modelo_a_diccionario
from pagos.utilidades.validaciones_payvalida import ErrorValidacionPayvalida
from utils.bitrix_payvalida_mapper import (
    construir_solicitud_pago_desde_bitrix,
    construir_solicitud_pago_desde_bitrix_sin_deal,
)
from utils.bitrix_process_service import (
    CAMPO_BITRIX_UNIVERSIDAD_RENOVACIONES_DEPRECADO,
    MAPEOS_CAMPOS_PREDETERMINADOS,
    PROCESOS_SOPORTADOS,
    asignar_destino_bitrix_proceso,
    asignar_mapeo_campo_proceso,
    campos_logicos_soportados,
    construir_campos_deal_proceso,
    desactivar_destino_bitrix_proceso,
    desactivar_mapeo_campo_proceso,
    evaluar_interes_proceso,
    filtrar_variables_salida_proceso,
    listar_destinos_bitrix_procesos,
    listar_mapeos_campos_configurados,
    obtener_destino_bitrix_proceso,
    obtener_mapeo_campo_configurado,
    normalizar_codigo_proceso,
    resolver_codigo_proceso,
    resolver_mapeos_campos_proceso,
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
from datetime import datetime, timezone, timedelta
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
    creditLineId: str = Field(
        validation_alias=AliasChoices("creditLineID", "creditLineId"),
        serialization_alias="creditLineID",
    )
    principal: float
    time: int
    paymentFrequency: int
    initialFee: float
    disbursementMethod: Optional[str] = None
    source: Optional[str] = None
    redirectUrl: Optional[str] = None
    callbackUrl: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def validate_credit_line_aliases(cls, data):
        """Acepta ambos nombres del contrato, pero evita IDs ambiguos."""
        if isinstance(data, dict):
            upper_id = data.get("creditLineID")
            lower_id = data.get("creditLineId")
            if (
                upper_id is not None
                and lower_id is not None
                and str(upper_id).strip() != str(lower_id).strip()
            ):
                raise ValueError(
                    "creditLineID y creditLineId no pueden contener valores diferentes"
                )
        return data

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
        """Conserva el string vacío porque forma parte del contrato de Kuenta."""
        if v is None:
            return None
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("meta", mode="before")
    @classmethod
    def validate_meta(cls, v):
        """Normaliza metadata ausente o nula a un objeto vacío."""
        return {} if v is None else v

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
    key: Optional[str] = None


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

#inicalizacion del modlo de pagos
registrar_modulo_pagos(app)
registrar_modulo_renovaciones(app)

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
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s\n"
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


def _valor_texto_linea_producto(valor: Any) -> str:
    """Convierte valores opcionales de una linea a texto entendible para una IA."""
    if valor is None or valor == "":
        return "No informado"
    if isinstance(valor, (list, tuple, set)):
        return ", ".join(str(item) for item in valor) if valor else "No informado"
    return str(valor)


def _valor_cop_linea_producto(valor: Any) -> str:
    """Formatea un limite monetario sin perder su valor numerico original."""
    if valor is None or valor == "":
        return "No informado"
    try:
        return formatear_valor_moneda(float(valor))
    except (TypeError, ValueError):
        return str(valor)


def build_product_line_text_fields(
    respuesta_base: Dict[str, Any],
) -> Dict[str, str]:
    """
    Genera variables de texto para que un agente de IA pueda entender y validar
    los limites de una linea sin alterar el contrato estructurado existente.
    """
    principal_min = respuesta_base.get("principalMin")
    principal_max = respuesta_base.get("principalMax")
    time_min = respuesta_base.get("timeMin")
    time_max = respuesta_base.get("timeMax")
    time_default = respuesta_base.get("timeDefault")
    payment_frequency = respuesta_base.get("paymentFrequency")

    contexto_ia = (
        "INFORMACIÓN DE LA LÍNEA DE CRÉDITO\n\n"
        f"ID de la línea: {_valor_texto_linea_producto(respuesta_base.get('id'))}\n"
        f"Nombre: {_valor_texto_linea_producto(respuesta_base.get('name'))}\n"
        f"Título: {_valor_texto_linea_producto(respuesta_base.get('title'))}\n"
        f"Parent ID: {_valor_texto_linea_producto(respuesta_base.get('parentId'))}\n"
        f"Version: {_valor_texto_linea_producto(respuesta_base.get('version'))}\n"
        f"Linea encontrada por: {_valor_texto_linea_producto(respuesta_base.get('matched_by'))}\n\n"
        "LÍMITES PARA ARMAR EL PAYLOAD\n"
        f"Principal mínimo: {_valor_texto_linea_producto(principal_min)} "
        f"({_valor_cop_linea_producto(principal_min)} COP)\n"
        f"Principal máximo: {_valor_texto_linea_producto(principal_max)} "
        f"({_valor_cop_linea_producto(principal_max)} COP)\n"
        f"Plazo mínimo: {_valor_texto_linea_producto(time_min)} días\n"
        f"Plazo máximo: {_valor_texto_linea_producto(time_max)} días\n"
        f"Plazo recomendado: {_valor_texto_linea_producto(time_default)} días\n"
        f"Frecuencias de pago permitidas: {_valor_texto_linea_producto(payment_frequency)} días\n\n"
        "REGLAS OBLIGATORIAS PARA EL AGENTE DE IA\n"
        "1. El principal debe estar entre principalMin y principalMax, incluyendo ambos límites.\n"
        "2. El plazo time debe estar entre timeMin y timeMax, incluyendo ambos límites.\n"
        "3. paymentFrequency debe pertenecer a las frecuencias de pago permitidas.\n"
        "4. Si algún valor está fuera de los límites, no armes ni envíes el payable; "
        "solicita al usuario un valor válido.\n"
        "5. Copia el campo id de esta respuesta en creditLineId del payload."
    )

    return {
        "linea_producto_contexto_ia": contexto_ia,
        "linea_producto_json_texto": json.dumps(
            respuesta_base,
            ensure_ascii=False,
            default=str,
        ),
    }


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
            respuesta_base = {
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
            return {
                **respuesta_base,
                **build_product_line_text_fields(respuesta_base),
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


CLAVES_SENSIBLES_DEPURACION = (
    "authorization",
    "token",
    "secret",
    "password",
    "pass",
    "client_secret",
)


def sanitizar_depuracion_kuenta(value: Any) -> Any:
    """Redacta secretos antes de registrar trazas relacionadas con Kuenta."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in CLAVES_SENSIBLES_DEPURACION):
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = sanitizar_depuracion_kuenta(item)
        return sanitized

    if isinstance(value, list):
        return [sanitizar_depuracion_kuenta(item) for item in value]

    return value


def truncar_depuracion_kuenta(value: Any, max_chars: int = 4000) -> str:
    """Serializa trazas sanitizadas y limita su tamaño en los logs."""
    text = json.dumps(sanitizar_depuracion_kuenta(value), ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncado]"


def extraer_error_kuenta_create_payable(response_data: Any) -> Dict[str, Any]:
    """Normaliza los formatos de error que puede devolver Kuenta."""
    if not isinstance(response_data, dict):
        return {
            "codigo": "Unknown",
            "mensaje": "Kuenta devolvio una respuesta no estructurada.",
            "detalle": sanitizar_depuracion_kuenta(response_data),
        }

    data = response_data.get("data")
    data_dict = data if isinstance(data, dict) else {}
    codigo = (
        data_dict.get("code")
        or response_data.get("code")
        or response_data.get("CODE")
        or response_data.get("error")
        or "Unknown"
    )
    mensaje = (
        data_dict.get("message")
        or response_data.get("message")
        or response_data.get("DESC")
        or response_data.get("error_description")
        or response_data.get("error")
        or "Kuenta no informo el detalle del error."
    )
    return {
        "codigo": codigo,
        "mensaje": mensaje,
        "detalle": sanitizar_depuracion_kuenta(response_data),
    }


def log_respuesta_create_payable_depuracion(
    *,
    codigo_estado_respuesta: int,
    endpoint: str,
    metodo_http_externo: str,
    api_externa: str,
    cuerpo_enviado: Dict[str, Any],
    respuesta: Any,
) -> None:
    """Registra una traza sanitizada antes de responder desde create_payable."""
    logger.info(
        "[RESPUESTA_CREATE_PAYABLE] codigo_estado_respuesta=%s | endpoint=%s | "
        "metodo_http_externo=%s | api_externa=%s | cuerpo_enviado=%s | respuesta=%s",
        codigo_estado_respuesta,
        endpoint,
        metodo_http_externo,
        api_externa,
        truncar_depuracion_kuenta(cuerpo_enviado),
        truncar_depuracion_kuenta(respuesta),
    )


def _json_respuesta_http_seguro(response: httpx.Response) -> Any:
    """Evita que respuestas vacías o no JSON oculten el error real de Kuenta."""
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return {
            "code": "InvalidJSONResponse",
            "message": "Kuenta devolvio una respuesta no JSON.",
            "contentType": response.headers.get("content-type"),
            "bodyLength": len(response.content),
        }
def _numero_opcional(valor: Any) -> Optional[float]:
    """Convierte configuraciones numericas de Kuenta sin asumir que siempre existen."""
    if valor is None or valor == "":
        return None
    try:
        if isinstance(valor, str):
            valor = valor.replace(",", ".").strip()
        return float(valor)
    except (TypeError, ValueError):
        return None


def _frecuencias_permitidas(valor: Any) -> List[int]:
    valores = valor if isinstance(valor, (list, tuple, set)) else [valor]
    frecuencias = []
    for item in valores:
        numero = _numero_opcional(item)
        if numero is not None and numero.is_integer():
            frecuencias.append(int(numero))
    return frecuencias


def construir_restricciones_producto(producto: Dict[str, Any]) -> Dict[str, Any]:
    """Extrae las reglas relevantes para respuestas de validacion y trazabilidad."""
    return {
        "creditLineId": producto.get("ID") or producto.get("id"),
        "principalMin": producto.get("principalMin"),
        "principalMax": producto.get("principalMax"),
        "timeMin": producto.get("timeMin"),
        "timeMax": producto.get("timeMax"),
        "timeDefault": producto.get("timeDefault"),
        "paymentFrequency": producto.get("paymentFrequency"),
        "initialFee": producto.get("initialFee"),
        "initialFeeMin": producto.get("initialFeeMin"),
        "initialFeeMax": producto.get("initialFeeMax"),
        "initialFeeMinRate": producto.get("initialFeeMinRate"),
        "initialFeeMaxRate": producto.get("initialFeeMaxRate"),
    }


def _error_validacion_producto(
    codigo: str,
    detalles_usuario: str,
    producto: Dict[str, Any],
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "estado": "error",
            "codigo": codigo,
            "detalles_usuario": detalles_usuario,
            "restricciones": construir_restricciones_producto(producto),
        },
    )


def validar_payable_con_producto(
    payload: PayableRequest,
    producto: Dict[str, Any],
) -> None:
    """Valida el payable contra la configuracion vigente de la linea en Kuenta."""
    product_id = producto.get("ID") or producto.get("id")
    if not product_id or product_id != payload.creditLineId or producto.get("archived") is True:
        raise _error_validacion_producto(
            "InvalidCreditLine",
            "La linea de credito no existe, esta archivada o no coincide con la solicitada.",
            producto,
        )

    time_min = _numero_opcional(producto.get("timeMin"))
    time_max = _numero_opcional(producto.get("timeMax"))
    if (time_min is not None and payload.time < time_min) or (
        time_max is not None and payload.time > time_max
    ):
        raise _error_validacion_producto(
            "InvalidDays",
            f"El plazo {payload.time} dias no es valido para esta linea. "
            f"Debe estar entre {_valor_texto_linea_producto(producto.get('timeMin'))} y "
            f"{_valor_texto_linea_producto(producto.get('timeMax'))} dias.",
            producto,
        )

    principal_min = _numero_opcional(producto.get("principalMin"))
    principal_max = _numero_opcional(producto.get("principalMax"))
    if (principal_min is not None and payload.principal < principal_min) or (
        principal_max is not None and payload.principal > principal_max
    ):
        raise _error_validacion_producto(
            "InvalidPrincipal",
            f"El principal {payload.principal:g} no es valido para esta linea. "
            f"Debe estar entre {_valor_texto_linea_producto(producto.get('principalMin'))} y "
            f"{_valor_texto_linea_producto(producto.get('principalMax'))}.",
            producto,
        )

    frecuencias = _frecuencias_permitidas(producto.get("paymentFrequency"))
    if frecuencias and payload.paymentFrequency not in frecuencias:
        raise _error_validacion_producto(
            "InvalidPaymentFrequency",
            f"La frecuencia {payload.paymentFrequency} no esta permitida. "
            f"Frecuencias validas: {', '.join(str(item) for item in frecuencias)}.",
            producto,
        )

    if payload.initialFee < 0:
        raise _error_validacion_producto(
            "InvalidInitialFee",
            "La cuota inicial no puede ser negativa.",
            producto,
        )

    initial_fee_enabled = producto.get("initialFee")
    if initial_fee_enabled is True:
        fee_min = _numero_opcional(producto.get("initialFeeMin"))
        fee_max = _numero_opcional(producto.get("initialFeeMax"))
        fee_min_rate = _numero_opcional(producto.get("initialFeeMinRate"))
        fee_max_rate = _numero_opcional(producto.get("initialFeeMaxRate"))
        fee_rate = payload.initialFee / payload.principal if payload.principal > 0 else 0

        fee_out_of_range = (
            (fee_min is not None and fee_min > 0 and payload.initialFee < fee_min)
            or (fee_max is not None and fee_max > 0 and payload.initialFee > fee_max)
            or (fee_min_rate is not None and fee_min_rate > 0 and fee_rate < fee_min_rate)
            or (fee_max_rate is not None and fee_max_rate > 0 and fee_rate > fee_max_rate)
        )
        if fee_out_of_range:
            raise _error_validacion_producto(
                "InvalidInitialFee",
                "La cuota inicial esta fuera de los limites permitidos para esta linea.",
                producto,
            )


def _construir_url_producto_kuenta(base_url: Optional[str], credit_line_id: str) -> str:
    base = (base_url or API_URL or "https://api.kuenta.co/v1").rstrip("/")
    for placeholder in (
        "{linea_producto}",
        "{creditLineID}",
        "{creditLineId}",
        "{credit_line_id}",
    ):
        if placeholder in base:
            return base.replace(placeholder, credit_line_id)
    if base.endswith("/product-lines"):
        base = base.rsplit("/", 1)[0]
    if base.endswith("/products"):
        return f"{base}/{credit_line_id}"
    return f"{base}/products/{credit_line_id}"


async def obtener_producto_kuenta_para_validacion(
    client: httpx.AsyncClient,
    token: str,
    credit_line_id: str,
    client_id: str,
) -> Dict[str, Any]:
    """Obtiene la linea vigente antes de crear un payable y normaliza su respuesta."""
    headers = {
        "Config-Organization-ID": ORG_ID,
        "Organization-ID": ORG_ID,
        "Authorization": token,
    }
    ext_client_product = None
    try:
        ext_client_product = await ExternalClient.from_code(
            "KUENTA_PRODUCT_GET",
            client_id=client_id,
        )
    except ValueError:
        pass

    product_url = _construir_url_producto_kuenta(
        ext_client_product.url if ext_client_product else API_URL,
        credit_line_id,
    )

    if ext_client_product:
        ext_client_product.set_dynamic_values(
            {
                "ORG_ID": ORG_ID,
                "access_token": token,
                "linea_producto": credit_line_id,
                "creditLineID": credit_line_id,
                "creditLineId": credit_line_id,
            }
        )
        ext_client_product.set_headers(headers)
        ext_client_product.set_url(product_url)
        ext_client_product.set_body({})
        external_response = await ext_client_product.run()
        if not isinstance(external_response, dict):
            raise HTTPException(status_code=502, detail={
                "estado": "error",
                "codigo": "InvalidProductResponse",
                "detalles_usuario": "Kuenta devolvio una respuesta invalida al consultar la linea.",
            })
        status_code = external_response.get("status", 500)
        response_data = external_response.get("data") or {}
    else:
        response = await client.get(product_url, headers=headers)
        status_code = response.status_code
        try:
            response_data = response.json()
        except Exception:
            response_data = {}

    if status_code == 404:
        raise HTTPException(status_code=400, detail={
            "estado": "error",
            "codigo": "InvalidCreditLine",
            "detalles_usuario": "La linea de credito indicada no existe en Kuenta.",
        })
    if status_code not in (200, 201):
        raise HTTPException(status_code=502, detail={
            "estado": "error",
            "codigo": "ProductServiceUnavailable",
            "detalles_usuario": "No fue posible validar los limites de la linea de credito.",
        })

    nested_data = response_data.get("data") if isinstance(response_data, dict) else None
    producto = nested_data.get("product") if isinstance(nested_data, dict) else None
    if not isinstance(producto, dict) and isinstance(response_data, dict):
        producto = response_data.get("product")
    if not isinstance(producto, dict):
        raise HTTPException(status_code=502, detail={
            "estado": "error",
            "codigo": "InvalidProductResponse",
            "detalles_usuario": "Kuenta no devolvio la configuracion esperada de la linea.",
        })
    return producto


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
            logger.info(
                "Cuerpo entrante create_payable (sanitizado) | %s",
                truncar_depuracion_kuenta(payload.model_dump(exclude_none=False)),
            )

            principal = payload.principal
            initial_fee = payload.initialFee

            token = await obtener_token(client)
            if not token:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "estado": "error",
                        "codigo": "AuthenticationError",
                        "detalles_usuario": "No fue posible obtener autorizacion para validar y crear el payable.",
                    },
                )

            producto = await obtener_producto_kuenta_para_validacion(
                client=client,
                token=token,
                credit_line_id=payload.creditLineId,
                client_id=client_id,
            )
            validar_payable_con_producto(payload, producto)

            # Construir payload para Kuenta
            new_payload = {
                "creditLineID": payload.creditLineId,
                "principal": principal,
                "time": payload.time,
                "paymentFrequency": payload.paymentFrequency,
                "source": payload.source,
                "redirectUrl": payload.redirectUrl,
                "callbackUrl": payload.callbackUrl,
            }

            if producto.get("initialFee") is True:
                new_payload["initialFee"] = initial_fee
            else:
                logger.info(
                    "La linea %s tiene initialFee deshabilitado; el campo se omite "
                    "sin modificar principal.",
                    payload.creditLineId,
                )

            # Kuenta distingue entre null y string vacío para este campo.
            if payload.disbursementMethod is not None:
                new_payload["disbursementMethod"] = payload.disbursementMethod

            if payload.meta is not None:
                new_payload["meta"] = payload.meta

            # Eliminar campos None restantes antes de enviar a Kuenta.
            new_payload = {k: v for k, v in new_payload.items() if v is not None}

            logger.info(
                "Cuerpo final para Kuenta (sanitizado) | %s",
                truncar_depuracion_kuenta(new_payload),
            )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": client_id,
                "Authorization": token,
            }
            logger.info(
                "Encabezados para Kuenta (sanitizados) | %s",
                truncar_depuracion_kuenta(headers),
            )

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
            status_code = 500
            error_code = "Unknown"
            error_message = "No fue posible crear el payable"
            ultimo_error_kuenta = None

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
                            **new_payload,
                            "creditLineId": payload.creditLineId,
                            "access_token": token,
                            "ORG_ID": ORG_ID,
                        }

                        logger.info(
                            "Variables dinámicas a inyectar (sanitizadas): %s",
                            truncar_depuracion_kuenta(dynamic_vars),
                        )
                        ext_client_post.set_dynamic_values(dynamic_vars)

                        # header dinámicos
                        ext_client_post.set_headers(headers)

                        # DEBUG/FUTURO USO:
                        # KUENTA_PAYABLE_CREATE en BD puede tener placeholders para
                        # meta y disbursementMethod. Para evitar enviar null, se fuerza
                        # el body final limpio construido arriba. Si un flujo futuro
                        # necesita esos parametros, agregarlos al new_payload solo con
                        # valores reales, no None.
                        ext_client_post.set_body(new_payload)

                        # Asignar URL si no está definida en la configuración
                        if not ext_client_post.url:
                            ext_client_post.set_url(PAYABLE_URL)

                        # Ejecutar el servicio externo
                        response = await ext_client_post.run()

                        if not isinstance(response, dict):
                            raise HTTPException(
                                status_code=502,
                                detail={
                                    "estado": "error",
                                    "codigo": "InvalidPayableResponse",
                                    "detalles_usuario": "Kuenta devolvio una respuesta invalida al crear el credito.",
                                },
                            )
                        status_code = response.get("status", 500)
                        response_data = response.get("data")
                        if response_data is None:
                            response_data = {}
                    else:
                        response = await client.post(
                            PAYABLE_URL, json=new_payload, headers=headers
                        )
                        response_data = _json_respuesta_http_seguro(response)
                        status_code = response.status_code

                    logger.info(
                        "Respuesta KUENTA_PAYABLE_CREATE | intento=%s/%s | codigo_estado=%s | datos=%s",
                        attempt + 1,
                        max_retries,
                        status_code,
                        truncar_depuracion_kuenta(response_data),
                    )

                    # ===== CASO 1: ÉXITO (200 o 201) =====
                    if status_code in (200, 201):
                        response_dict = response_data if isinstance(response_data, dict) else {}
                        response_nested = response_dict.get("data")
                        response_nested = (
                            response_nested if isinstance(response_nested, dict) else {}
                        )
                        credit = response_nested.get("credit") or response_dict.get("credit")
                        credit = credit if isinstance(credit, dict) else {}
                        response_credit_id = credit.get("ID")
                        logger.info(
                            f"Respuesta: HTTP {status_code}, \n ID del credito: {response_credit_id}"
                        )
                        if not response_credit_id:
                            ultimo_error_kuenta = {
                                "operacion": "KUENTA_PAYABLE_CREATE",
                                "codigo_estado_kuenta": status_code,
                                "codigo_error_kuenta": "InvalidPayableResponse",
                                "mensaje_error_kuenta": "Kuenta no devolvio el ID del credito creado.",
                                "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                                "respuesta_kuenta": sanitizar_depuracion_kuenta(response_data),
                                "intento": attempt + 1,
                            }
                            logger.error(
                                "Respuesta exitosa de Kuenta sin ID de credito | %s",
                                truncar_depuracion_kuenta(ultimo_error_kuenta),
                            )
                            raise HTTPException(
                                status_code=502,
                                detail={
                                    "estado": "error",
                                    "codigo": "InvalidPayableResponse",
                                    "detalles_usuario": "Kuenta no confirmo el credito creado.",
                                },
                            )
                        break

                    # ===== CASO 2: ERROR DEL CLIENTE O REGLA DE NEGOCIO (400, 409, 422) =====
                    elif status_code in (400, 409, 422):
                        error_info = extraer_error_kuenta_create_payable(response_data)
                        error_code = error_info["codigo"]
                        error_message = error_info["mensaje"]
                        ultimo_error_kuenta = {
                            "operacion": "KUENTA_PAYABLE_CREATE",
                            "codigo_estado_kuenta": status_code,
                            "codigo_error_kuenta": error_code,
                            "mensaje_error_kuenta": error_message,
                            "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                            "respuesta_kuenta": error_info["detalle"],
                            "intento": attempt + 1,
                        }

                        logger.error(
                            "KUENTA_PAYABLE_CREATE fallo sin reintento | %s",
                            truncar_depuracion_kuenta(ultimo_error_kuenta),
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
                                respuesta_api=truncar_depuracion_kuenta(error_info["detalle"]),
                                payload_enviado=truncar_depuracion_kuenta(new_payload),
                            )

                            respuesta_final = {
                                "estado": "error",
                                "http_real": status_code,
                                "codigo_tecnico": error_code,
                                "mensaje": mensaje_amigable,
                                "detalles_usuario": detalles_usuario,
                                "depuracion": ultimo_error_kuenta,
                            }
                            log_respuesta_create_payable_depuracion(
                                codigo_estado_respuesta=200,
                                endpoint=f"/payables/{client_id}",
                                metodo_http_externo="POST",
                                api_externa=PAYABLE_URL,
                                cuerpo_enviado=new_payload,
                                respuesta=respuesta_final,
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

                        raise HTTPException(
                            status_code=status_code,
                            detail={
                                "estado": "error",
                                "codigo": error_code,
                                "mensaje": mensaje_amigable,
                                "detalles_usuario": detalles_usuario,
                                "restricciones": construir_restricciones_producto(producto),
                            },
                        )

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
                            if not token:
                                raise HTTPException(
                                    status_code=401,
                                    detail={
                                        "estado": "error",
                                        "codigo": "AuthenticationError",
                                        "detalles_usuario": "No fue posible renovar la autorizacion con Kuenta.",
                                    },
                                )
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

                    # ===== CASO 4: ERRORES DE SERVIDOR (5XX) =====
                    elif 500 <= status_code <= 599:
                        ultimo_error_kuenta = {
                            "operacion": "KUENTA_PAYABLE_CREATE",
                            "codigo_estado_kuenta": status_code,
                            "codigo_error_kuenta": "error_servidor_o_estado_no_esperado",
                            "mensaje_error_kuenta": "Error del servidor Kuenta o estado no esperado",
                            "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                            "respuesta_kuenta": sanitizar_depuracion_kuenta(response_data),
                            "intento": attempt + 1,
                        }
                        logger.error(
                            "Error del servidor Kuenta | %s",
                            truncar_depuracion_kuenta(ultimo_error_kuenta),
                        )
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error del servidor Kuenta HTTP {status_code}: {truncar_depuracion_kuenta(response_data)}",
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2**attempt)
                            continue

                    # Otros estados no se reintentan: no son errores transitorios conocidos.
                    else:
                        error_info = extraer_error_kuenta_create_payable(response_data)
                        ultimo_error_kuenta = {
                            "operacion": "KUENTA_PAYABLE_CREATE",
                            "codigo_estado_kuenta": status_code,
                            "codigo_error_kuenta": error_info["codigo"],
                            "mensaje_error_kuenta": error_info["mensaje"],
                            "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                            "respuesta_kuenta": error_info["detalle"],
                            "intento": attempt + 1,
                        }
                        logger.error(
                            "Estado no reintentable de Kuenta | %s",
                            truncar_depuracion_kuenta(ultimo_error_kuenta),
                        )
                        raise HTTPException(
                            status_code=502,
                            detail={
                                "estado": "error",
                                "codigo": "KuentaRequestRejected",
                                "detalles_usuario": "Kuenta rechazo la solicitud con un estado no esperado.",
                            },
                        )

                except httpx.TimeoutException as e:
                    status_code = 504
                    ultimo_error_kuenta = {
                        "operacion": "KUENTA_PAYABLE_CREATE",
                        "codigo_estado_kuenta": 504,
                        "codigo_error_kuenta": "tiempo_espera_agotado",
                        "mensaje_error_kuenta": str(e),
                        "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                        "respuesta_kuenta": None,
                        "intento": attempt + 1,
                    }
                    if attempt < max_retries - 1:
                        wait_time = 2**attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                except httpx.RequestError as e:
                    status_code = 502
                    error_traceback = traceback.format_exc()
                    ultimo_error_kuenta = {
                        "operacion": "KUENTA_PAYABLE_CREATE",
                        "codigo_estado_kuenta": 502,
                        "codigo_error_kuenta": "error_peticion",
                        "mensaje_error_kuenta": str(e),
                        "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                        "respuesta_kuenta": None,
                        "intento": attempt + 1,
                    }
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
                detalle_fallo_payable = ultimo_error_kuenta or {
                    "operacion": "KUENTA_PAYABLE_CREATE",
                    "codigo_estado_kuenta": status_code or 500,
                    "codigo_error_kuenta": error_code,
                    "mensaje_error_kuenta": error_message,
                    "cuerpo_enviado": sanitizar_depuracion_kuenta(new_payload),
                    "respuesta_kuenta": None,
                    "intento": max_retries,
                }
                logger.error(
                    "KUENTA_PAYABLE_CREATE agotó sus reintentos | %s",
                    truncar_depuracion_kuenta(detalle_fallo_payable),
                )
                codigo_publico = (
                    504
                    if detalle_fallo_payable.get("codigo_estado_kuenta") == 504
                    else 502
                )
                raise HTTPException(
                    status_code=codigo_publico,
                    detail={
                        "estado": "error",
                        "operacion": "KUENTA_PAYABLE_CREATE",
                        "codigo": detalle_fallo_payable.get("codigo_error_kuenta", error_code),
                        "detalles_usuario": (
                            "Kuenta no respondio dentro del tiempo esperado."
                            if codigo_publico == 504
                            else "No fue posible crear el credito en Kuenta."
                        ),
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
                            raise ValueError("Respuesta invalida en simulacion externa")

                        status_code_simulacion = res_simulacion.get("status", 500)
                        simulacion_data = res_simulacion.get("data", {})
                    else:
                        res_simulacion = await client.get(
                            f"{PAYABLE_URL}/{response_credit_id}", headers=headers
                        )
                        status_code_simulacion = res_simulacion.status_code
                        simulacion_data = _json_respuesta_http_seguro(res_simulacion)

                    # Éxito en la simulación
                    if status_code_simulacion in (200, 201):
                        logger.info(
                            f"GET simulación exitoso en intento {attempt_get + 1}"
                        )
                        break

                    # Solo los 5xx se consideran transitorios en la simulacion.
                    elif 500 <= status_code_simulacion <= 599:
                        logger.warning(
                            f"GET simulación status {status_code_simulacion}. Reintentando..."
                        )
                        if attempt_get < max_retries_simulacion - 1:
                            await asyncio.sleep(5)
                            continue
                        break

                    else:
                        logger.error(
                            "GET simulacion devolvio un estado no reintentable: %s",
                            status_code_simulacion,
                        )
                        break

                except (httpx.TimeoutException, httpx.RequestError) as e:
                    logger.error(f"Fallo de conexión en GET simulación: {str(e)}")
                    if attempt_get < max_retries_simulacion - 1:
                        await asyncio.sleep(5)
                        continue
                except (TypeError, ValueError) as e:
                    logger.error(f"Respuesta invalida en GET simulacion: {str(e)}")
                    break

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
                cuota_inicial = _numero_opcional(credit_data.get("initialFee")) or 0
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

                log_respuesta_create_payable_depuracion(
                    codigo_estado_respuesta=200,
                    endpoint=f"/payables/{client_id}",
                    metodo_http_externo="POST",
                    api_externa=PAYABLE_URL,
                    cuerpo_enviado=new_payload,
                    respuesta=response_data,
                )
                return response_data

            except HTTPException:
                raise
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
        log_respuesta_create_payable_depuracion(
            codigo_estado_respuesta=http_exc.status_code,
            endpoint=f"/payables/{client_id}",
            metodo_http_externo="POST",
            api_externa=PAYABLE_URL,
            cuerpo_enviado=locals().get("new_payload", {}),
            respuesta=http_exc.detail,
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
        respuesta_final = {
            "estado": "error",
            "mensaje": MENSAJES_CLIENTE["error_datos"],
            "detalles_usuario": "Se detectó un valor numérico incorrecto en el proceso.",
        }
        log_respuesta_create_payable_depuracion(
            codigo_estado_respuesta=400,
            endpoint=f"/payables/{client_id}",
            metodo_http_externo="POST",
            api_externa=PAYABLE_URL,
            cuerpo_enviado=locals().get("new_payload", {}),
            respuesta=respuesta_final,
        )
        return JSONResponse(status_code=400, content=respuesta_final)
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
        respuesta_final = {
            "estado": "error",
            "mensaje": MENSAJES_CLIENTE["error_general"],
            "detalles_usuario": "El equipo técnico ya fue notificado. Disculpa las molestias.",
        }
        log_respuesta_create_payable_depuracion(
            codigo_estado_respuesta=500,
            endpoint=f"/payables/{client_id}",
            metodo_http_externo="POST",
            api_externa=PAYABLE_URL,
            cuerpo_enviado=locals().get("new_payload", {}),
            respuesta=respuesta_final,
        )
        return JSONResponse(status_code=500, content=respuesta_final)


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
    if payload.key:
        outbound_payload["key"] = payload.key

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
            logger.info("Token obtenido correctamente para consulta de orden Kuenta")

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


ESTADOS_CUOTA_MORA = {
    0: ("InactiveInstallment", "inactiva"),
    1: ("PaidInstallment", "pagada"),
    3: ("DueInstallment", "pendiente"),
    4: ("ExpiredInstallment", "vencida"),
}


def convertir_decimal_cuota(valor: Any) -> Optional[Decimal]:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return Decimal(int(valor))
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace("$", "").replace("COP", "").replace(" ", "")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".") else texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes[-1]) <= 2 else texto.replace(",", "")
    elif "." in texto:
        partes = texto.split(".")
        if len(partes) > 2:
            texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) <= 2 else "".join(partes)
        elif len(partes[-1]) > 2:
            texto = texto.replace(".", "")

    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        logger.warning("No se pudo convertir valor de cuota a decimal | valor=%s", valor)
        return None


def normalizar_valor_original_cuota(valor: Any) -> Any:
    numero = convertir_decimal_cuota(valor)
    if numero is None:
        return None
    if numero == numero.to_integral_value():
        return int(numero)
    return float(numero)


def redondear_valor_cuota(valor: Any) -> Optional[int]:
    numero = convertir_decimal_cuota(valor)
    if numero is None:
        return None
    return int(numero.to_integral_value(rounding=ROUND_HALF_UP))


def formatear_valor_cuota(valor: Any) -> str:
    redondeado = redondear_valor_cuota(valor)
    if redondeado is None:
        return "N/A"
    return f"$ {redondeado:,}".replace(",", ".")


def to_int(value: Any) -> int:
    value_int = redondear_valor_cuota(value)
    return value_int if value_int is not None else 0


def money(value: Any) -> str:
    return f"${to_int(value):,}".replace(",", ".")


def money_signed(value: Any) -> str:
    value_int = to_int(value)
    sign = "-" if value_int < 0 else ""
    return f"{sign}${abs(value_int):,}".replace(",", ".")


def percent(value: Any) -> str:
    return formatear_tasa_ea_cuota(value)


def percent_simple(value: Any) -> str:
    numero = convertir_decimal_cuota(value)
    if numero is None:
        return "N/A"
    if abs(numero) <= Decimal("1"):
        numero = numero * Decimal("100")
    return f"{numero.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def get_nested(obj: Any, *keys: Any, default: Any = None) -> Any:
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def text_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def formatear_tasa_ea_cuota(valor: Any) -> str:
    numero = convertir_decimal_cuota(valor)
    if numero is None:
        return "N/A"
    return f"{(numero * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}% EA"


def mapear_estado_cuota(status: Any) -> Dict[str, Any]:
    try:
        estado_codigo = int(status)
    except (TypeError, ValueError):
        estado_codigo = status
    estado_nombre, estado_descripcion = ESTADOS_CUOTA_MORA.get(
        estado_codigo,
        ("UnknownInstallment", "desconocida"),
    )
    return {
        "estado_codigo": estado_codigo,
        "estado_nombre": estado_nombre,
        "estado_descripcion": estado_descripcion,
    }


def construir_valor_financiero_cuota(
    installment: Dict[str, Any],
    campo: str,
    fuente: str,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    valor = installment.get(campo)
    data = {
        "fuente": fuente,
        "valor_original": normalizar_valor_original_cuota(valor),
        "valor_redondeado": redondear_valor_cuota(valor),
        "valor_legible": formatear_valor_cuota(valor),
    }
    if label:
        data["label"] = label
    return data


def normalizar_valor_desglose(value: Any, abs_value: bool = False) -> Any:
    numero = convertir_decimal_cuota(value)
    if numero is None:
        return None
    if abs_value:
        numero = abs(numero)
    if numero == numero.to_integral_value():
        return int(numero)
    return float(numero)


def redondear_valor_desglose(value: Any, abs_value: bool = False) -> int:
    numero = convertir_decimal_cuota(value)
    if numero is None:
        return 0
    if abs_value:
        numero = abs(numero)
    return int(numero.to_integral_value(rounding=ROUND_HALF_UP))


def build_valor_desglose(
    source: Dict[str, Any],
    field: str,
    label: str,
    abs_value: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    valor = source.get(field) if isinstance(source, dict) else None
    valor_redondeado = redondear_valor_desglose(valor, abs_value=abs_value)
    data = {
        "fuente": field,
        "valor_original": normalizar_valor_desglose(valor, abs_value=abs_value),
        "valor_redondeado": valor_redondeado,
        "valor_legible": money(valor_redondeado),
        "label": label,
    }
    if extra:
        data.update(extra)
    return data


EXPLICACION_CONSOLIDACION_CUOTA_PENDIENTE = (
    "La cuota pendiente puede acumular valores de cuotas vencidas anteriores. "
    "Cuando una cuota anterior se vence y no se paga, el sistema puede trasladar, "
    "consolidar o asignar ciertos conceptos a la ultima cuota pendiente, como mora, "
    "cargos de cobranza, costos pendientes, impuestos pendientes e intereses de mora. "
    "Por eso el valor total de la cuota pendiente puede ser mayor que el valor corriente "
    "de una cuota normal."
)


def build_desglose_cuota_pendiente(installment: Dict[str, Any]) -> Dict[str, Any]:
    componentes_corrientes = {
        "capital": build_valor_desglose(installment, "capital", "Capital"),
        "intereses_corrientes": build_valor_desglose(
            installment,
            "interest",
            "Intereses corrientes",
            extra={
                "dias": installment.get("interestDays"),
                "tasa": percent(installment.get("rateEA")),
            },
        ),
        "costos": build_valor_desglose(installment, "costs", "Costos"),
        "impuestos": build_valor_desglose(installment, "taxes", "Impuestos"),
    }
    componentes_heredados = {
        "mora_heredada": build_valor_desglose(
            installment,
            "debtPayment",
            "Mora heredada",
            abs_value=True,
        ),
        "cargos_cobranza": build_valor_desglose(
            installment,
            "penalty",
            "Cargos de cobranza",
            abs_value=True,
            extra={
                "dias": installment.get("penaltyDays"),
                "porcentaje": percent_simple(installment.get("penaltyPercentage")),
            },
        ),
        "costos_pendientes": build_valor_desglose(
            installment,
            "debtCosts",
            "Costos pendientes",
            abs_value=True,
        ),
        "impuestos_pendientes": build_valor_desglose(
            installment,
            "debtTaxes",
            "Impuestos pendientes",
            abs_value=True,
        ),
        "intereses_mora": build_valor_desglose(
            installment,
            "debtInterest",
            "Intereses de mora",
            abs_value=True,
            extra={
                "dias": installment.get("debtInterestDays"),
                "tasa": percent(installment.get("debtRateEA")),
            },
        ),
    }

    total_corriente = sum(
        to_int(get_nested(componentes_corrientes, key, "valor_redondeado"))
        for key in ("capital", "intereses_corrientes", "costos", "impuestos")
    )
    total_heredado = sum(
        to_int(get_nested(componentes_heredados, key, "valor_redondeado"))
        for key in (
            "mora_heredada",
            "cargos_cobranza",
            "costos_pendientes",
            "impuestos_pendientes",
            "intereses_mora",
        )
    )
    total_calculado = total_corriente + total_heredado
    total_cuota = redondear_valor_desglose(installment.get("payment"))
    diferencia = total_cuota - total_calculado

    return {
        "componentes_corrientes": componentes_corrientes,
        "componentes_heredados_por_atraso": componentes_heredados,
        "componentes_heredados_por_cuotas_vencidas": componentes_heredados,
        "totales": {
            "total_componentes_corrientes": {
                "valor_redondeado": total_corriente,
                "valor_legible": money(total_corriente),
            },
            "total_componentes_heredados": {
                "valor_redondeado": total_heredado,
                "valor_legible": money(total_heredado),
            },
            "total_calculado": {
                "valor_redondeado": total_calculado,
                "valor_legible": money(total_calculado),
            },
            "total_cuota_pendiente": {
                "valor_redondeado": total_cuota,
                "valor_legible": money(total_cuota),
            },
            "diferencia_redondeo": {
                "valor_redondeado": diferencia,
                "valor_legible": money_signed(diferencia),
                "alerta_revision": abs(diferencia) > 5,
            },
        },
    }


def find_cuota_pendiente_installment(
    installments: Any,
    pago_pendiente: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(installments, list):
        return None
    pending_id = str(pago_pendiente.get("id")) if isinstance(pago_pendiente, dict) and pago_pendiente.get("id") else ""
    if pending_id:
        for installment in installments:
            if isinstance(installment, dict) and str(installment.get("id")) == pending_id:
                return installment
    for installment in installments:
        if not isinstance(installment, dict):
            continue
        try:
            status = int(installment.get("status"))
        except (TypeError, ValueError):
            status = installment.get("status")
        if status == 3:
            return installment
    return None


def enriquecer_pago_pendiente_con_desglose(
    pago_pendiente: Optional[Dict[str, Any]],
    installments: Any,
) -> Optional[Dict[str, Any]]:
    if not isinstance(pago_pendiente, dict):
        return pago_pendiente
    installment = find_cuota_pendiente_installment(installments, pago_pendiente)
    if not isinstance(installment, dict):
        return pago_pendiente
    enriquecido = dict(pago_pendiente)
    enriquecido["desglose_completo"] = build_desglose_cuota_pendiente(installment)
    enriquecido["explicacion_consolidacion"] = (
        "Esta cuota esta en estado pendiente. Su total incluye valores propios de "
        "la cuota actual y valores heredados o asignados por cuotas vencidas "
        "anteriores, como mora, cargos de cobranza, costos pendientes, impuestos "
        "pendientes e intereses de mora."
    )
    return enriquecido


def construir_desglose_financiero_cuota(installment: Dict[str, Any]) -> Dict[str, Any]:
    interest_days = installment.get("interestDays")
    rate_label = formatear_tasa_ea_cuota(installment.get("rateEA"))
    interest_label = "Intereses"
    if interest_days is not None and rate_label != "N/A":
        interest_label = f"Intereses ({interest_days} dias) ({rate_label})"
    elif interest_days is not None:
        interest_label = f"Intereses ({interest_days} dias)"

    return {
        "abono_capital": construir_valor_financiero_cuota(installment, "capitalPaid", "capitalPaid", "Abono a capital"),
        "intereses": construir_valor_financiero_cuota(installment, "interest", "interest", interest_label),
        "costos": construir_valor_financiero_cuota(installment, "costs", "costs", "Costos"),
        "impuestos": construir_valor_financiero_cuota(installment, "taxes", "taxes", "Impuestos"),
        "total": construir_valor_financiero_cuota(installment, "payment", "payment", "Total"),
    }


def construir_pago_cuota_resumido(payment: Dict[str, Any]) -> Dict[str, Any]:
    resumen = {
        "registeredAt": payment.get("registeredAt"),
        "amount": normalizar_valor_original_cuota(payment.get("amount")),
        "orderId": payment.get("orderId"),
        "capitalPaid": normalizar_valor_original_cuota(payment.get("capitalPaid")),
        "interestPaid": normalizar_valor_original_cuota(payment.get("interestPaid")),
        "costsPaid": normalizar_valor_original_cuota(payment.get("costsPaid")),
        "taxesPaid": normalizar_valor_original_cuota(payment.get("taxesPaid")),
        "valuePaid": normalizar_valor_original_cuota(payment.get("valuePaid")),
    }
    for campo in ("amount", "capitalPaid", "interestPaid", "costsPaid", "taxesPaid", "valuePaid"):
        resumen[f"{campo}_redondeado"] = redondear_valor_cuota(payment.get(campo))
        resumen[f"{campo}_legible"] = formatear_valor_cuota(payment.get(campo))
    return resumen


def construir_detalle_cuota_mora(installment: Dict[str, Any]) -> Dict[str, Any]:
    fecha_pago = installment.get("date")
    payments = installment.get("payments") if isinstance(installment.get("payments"), list) else []
    detalle = {
        "id": installment.get("id"),
        "numero_de_cuota": installment.get("number"),
        **mapear_estado_cuota(installment.get("status")),
        "fecha_pago": fecha_pago,
        "fecha_pago_legible": formatear_fecha_legible(fecha_pago) if fecha_pago else "N/A",
        "rate_ea": normalizar_valor_original_cuota(installment.get("rateEA")),
        "rate_ea_porcentaje": formatear_tasa_ea_cuota(installment.get("rateEA")),
        "interest_days": installment.get("interestDays"),
        "desglose": construir_desglose_financiero_cuota(installment),
        "payments": [construir_pago_cuota_resumido(payment) for payment in payments if isinstance(payment, dict)],
    }
    if detalle.get("estado_codigo") in (3, 4):
        detalle["desglose_completo"] = build_desglose_cuota_pendiente(installment)
    return detalle


def construir_detalle_cuotas_mora(installments: Any) -> List[Dict[str, Any]]:
    if not isinstance(installments, list):
        return []
    return [
        construir_detalle_cuota_mora(installment)
        for installment in installments
        if isinstance(installment, dict)
    ]


def suma_payment_amount_pagado(cuota: Dict[str, Any]) -> int:
    payments = cuota.get("payments") if isinstance(cuota.get("payments"), list) else []
    total = sum(to_int(payment.get("amount_redondeado")) for payment in payments if isinstance(payment, dict))
    if total > 0:
        return total
    return to_int(get_nested(cuota, "desglose", "total", "valor_redondeado"))


def desglose_completo_value(
    desglose_completo: Dict[str, Any],
    grupo: str,
    campo: str,
    llave: str = "valor_redondeado",
) -> Any:
    return get_nested(desglose_completo, grupo, campo, llave, default=0)


def desglose_completo_money(desglose_completo: Dict[str, Any], grupo: str, campo: str) -> str:
    return money(desglose_completo_value(desglose_completo, grupo, campo))


def build_cuota_pendiente_desglose_texto(pago_pendiente: Dict[str, Any]) -> str:
    if not isinstance(pago_pendiente, dict):
        return "No se recibio una cuota pendiente para construir desglose."
    desglose_completo = pago_pendiente.get("desglose_completo")
    if not isinstance(desglose_completo, dict):
        return "No se recibio desglose completo de la cuota pendiente."

    componentes_corrientes = "componentes_corrientes"
    componentes_heredados = "componentes_heredados_por_atraso"
    totales = "totales"
    diferencia = to_int(get_nested(desglose_completo, totales, "diferencia_redondeo", "valor_redondeado"))
    diferencia_texto = money_signed(diferencia)

    return (
        "Desglose de la cuota pendiente\n\n"
        f"Cuota: {text_value(pago_pendiente.get('numero_de_cuota'), 'N/A')}\n"
        f"Estado: {text_value(pago_pendiente.get('estado_cuota'), 'N/A')}\n"
        f"Fecha relacionada: {text_value(pago_pendiente.get('fecha_pago_legible'), 'N/A')}\n"
        f"Total a pagar: {money(desglose_completo_value(desglose_completo, totales, 'total_cuota_pendiente'))}\n\n"
        "Componentes corrientes:\n"
        f"Capital: {desglose_completo_money(desglose_completo, componentes_corrientes, 'capital')}\n"
        f"Intereses corrientes: {desglose_completo_money(desglose_completo, componentes_corrientes, 'intereses_corrientes')}\n"
        f"Costos: {desglose_completo_money(desglose_completo, componentes_corrientes, 'costos')}\n"
        f"Impuestos: {desglose_completo_money(desglose_completo, componentes_corrientes, 'impuestos')}\n"
        f"Total corriente: {money(desglose_completo_value(desglose_completo, totales, 'total_componentes_corrientes'))}\n\n"
        "Componentes heredados o asignados por atraso:\n"
        f"Mora heredada: {desglose_completo_money(desglose_completo, componentes_heredados, 'mora_heredada')}\n"
        f"Cargos de cobranza: {desglose_completo_money(desglose_completo, componentes_heredados, 'cargos_cobranza')}\n"
        f"Costos pendientes: {desglose_completo_money(desglose_completo, componentes_heredados, 'costos_pendientes')}\n"
        f"Impuestos pendientes: {desglose_completo_money(desglose_completo, componentes_heredados, 'impuestos_pendientes')}\n"
        f"Intereses de mora: {desglose_completo_money(desglose_completo, componentes_heredados, 'intereses_mora')}\n"
        f"Total heredado: {money(desglose_completo_value(desglose_completo, totales, 'total_componentes_heredados'))}\n\n"
        f"Total cuota pendiente: {money(desglose_completo_value(desglose_completo, totales, 'total_cuota_pendiente'))}"
    )


def linea_detalle_cuota_texto(cuota: Dict[str, Any]) -> str:
    desglose = cuota.get("desglose") if isinstance(cuota.get("desglose"), dict) else {}
    desglose_completo = cuota.get("desglose_completo") if isinstance(cuota.get("desglose_completo"), dict) else {}
    payments = cuota.get("payments") if isinstance(cuota.get("payments"), list) else []
    estado_codigo = cuota.get("estado_codigo")
    base = (
        f"Cuota {text_value(cuota.get('numero_de_cuota'), 'N/A')} | "
        f"Estado: {text_value(cuota.get('estado_descripcion'), 'desconocida')} | "
        f"Fecha: {text_value(cuota.get('fecha_pago_legible'), 'N/A')} | "
    )

    if estado_codigo == 3 and desglose_completo:
        componentes_corrientes = "componentes_corrientes"
        componentes_heredados = "componentes_heredados_por_cuotas_vencidas"
        totales = "totales"
        return (
            base +
            f"Total: {money(desglose_completo_value(desglose_completo, totales, 'total_cuota_pendiente'))} | "
            f"Capital: {desglose_completo_money(desglose_completo, componentes_corrientes, 'capital')} | "
            f"Intereses corrientes: {desglose_completo_money(desglose_completo, componentes_corrientes, 'intereses_corrientes')} | "
            f"Costos: {desglose_completo_money(desglose_completo, componentes_corrientes, 'costos')} | "
            f"Impuestos: {desglose_completo_money(desglose_completo, componentes_corrientes, 'impuestos')} | "
            f"Mora heredada: {desglose_completo_money(desglose_completo, componentes_heredados, 'mora_heredada')} | "
            f"Cargos de cobranza: {desglose_completo_money(desglose_completo, componentes_heredados, 'cargos_cobranza')} | "
            f"Costos pendientes: {desglose_completo_money(desglose_completo, componentes_heredados, 'costos_pendientes')} | "
            f"Impuestos pendientes: {desglose_completo_money(desglose_completo, componentes_heredados, 'impuestos_pendientes')} | "
            f"Intereses de mora: {desglose_completo_money(desglose_completo, componentes_heredados, 'intereses_mora')} | "
            f"Pagos registrados: {len(payments)}"
        )

    linea = (
        base +
        f"Total: {text_value(get_nested(desglose, 'total', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Capital: {text_value(get_nested(desglose, 'abono_capital', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Intereses: {text_value(get_nested(desglose, 'intereses', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Costos: {text_value(get_nested(desglose, 'costos', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Impuestos: {text_value(get_nested(desglose, 'impuestos', 'valor_legible'), money(0)).replace('$ ', '$')}"
    )

    if estado_codigo == 4 and desglose_completo:
        componentes_heredados = "componentes_heredados_por_cuotas_vencidas"
        campos_heredados = (
            ("mora_heredada", "Mora heredada"),
            ("cargos_cobranza", "Cargos de cobranza"),
            ("costos_pendientes", "Costos pendientes"),
            ("impuestos_pendientes", "Impuestos pendientes"),
            ("intereses_mora", "Intereses de mora"),
        )
        for campo, label in campos_heredados:
            valor = to_int(desglose_completo_value(desglose_completo, componentes_heredados, campo))
            if valor:
                linea += f" | {label}: {money(valor)}"
    return f"{linea} | Pagos registrados: {len(payments)}"


def linea_detalle_cuota_texto_legacy(cuota: Dict[str, Any]) -> str:
    desglose = cuota.get("desglose") if isinstance(cuota.get("desglose"), dict) else {}
    payments = cuota.get("payments") if isinstance(cuota.get("payments"), list) else []
    return (
        f"Cuota {text_value(cuota.get('numero_de_cuota'), 'N/A')} | "
        f"Estado: {text_value(cuota.get('estado_descripcion'), 'desconocida')} | "
        f"Fecha: {text_value(cuota.get('fecha_pago_legible'), 'N/A')} | "
        f"Total: {text_value(get_nested(desglose, 'total', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Capital: {text_value(get_nested(desglose, 'abono_capital', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Intereses: {text_value(get_nested(desglose, 'intereses', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Costos: {text_value(get_nested(desglose, 'costos', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Impuestos: {text_value(get_nested(desglose, 'impuestos', 'valor_legible'), money(0)).replace('$ ', '$')} | "
        f"Pagos registrados: {len(payments)}"
    )


def build_pago_text_fields_legacy(data: Dict[str, Any]) -> Dict[str, str]:
    detalle_cuotas = data.get("detalle_cuotas") if isinstance(data.get("detalle_cuotas"), list) else []
    pago_pendiente = data.get("pago_pendiente") if isinstance(data.get("pago_pendiente"), dict) else {}

    total_pagado = 0
    total_pendiente = 0
    total_vencido = 0
    total_pendiente_no_vencido = 0
    intereses_pendientes = 0
    costos_pendientes = 0
    impuestos_pendientes = 0

    for cuota in detalle_cuotas:
        if not isinstance(cuota, dict):
            continue
        estado_codigo = cuota.get("estado_codigo")
        total_cuota = to_int(get_nested(cuota, "desglose", "total", "valor_redondeado"))

        if estado_codigo == 1:
            total_pagado += suma_payment_amount_pagado(cuota)
        elif estado_codigo in (3, 4):
            total_pendiente += total_cuota
            intereses_pendientes += to_int(get_nested(cuota, "desglose", "intereses", "valor_redondeado"))
            costos_pendientes += to_int(get_nested(cuota, "desglose", "costos", "valor_redondeado"))
            impuestos_pendientes += to_int(get_nested(cuota, "desglose", "impuestos", "valor_redondeado"))
            if estado_codigo == 4:
                total_vencido += total_cuota
            elif estado_codigo == 3:
                total_pendiente_no_vencido += total_cuota

    if detalle_cuotas:
        detalle_cuotas_texto = "\n".join(linea_detalle_cuota_texto(cuota) for cuota in detalle_cuotas if isinstance(cuota, dict))
    else:
        detalle_cuotas_texto = "No se recibió detalle de cuotas."

    cuota_principal_numero = text_value(pago_pendiente.get("numero_de_cuota"))
    cuota_principal_estado = text_value(pago_pendiente.get("estado_cuota"))
    cuota_principal_fecha = text_value(pago_pendiente.get("fecha_pago"))
    cuota_principal_fecha_legible = text_value(pago_pendiente.get("fecha_pago_legible"))
    cuota_principal_valor_numero = text_value(to_int(pago_pendiente.get("valor_total_pagar")))
    cuota_principal_valor_legible = money(pago_pendiente.get("valor_total_pagar"))
    cuota_principal_dias_mora = text_value(pago_pendiente.get("dias_de_mora_cuota"))
    cuota_principal_retrasado = str(bool(pago_pendiente.get("retrasado"))).lower() if "retrasado" in pago_pendiente else ""
    cuota_principal_label_fecha = text_value(pago_pendiente.get("label_fecha"))

    if detalle_cuotas:
        contexto_ia = (
            "Resumen de pagos One2Credit\n\n"
            "Información general:\n"
            f"Total cuotas: {data.get('total_cuotas', 0)}\n"
            f"Cuotas pagadas: {data.get('cuotas_pagadas', 0)}\n"
            f"Cuotas pendientes o vencidas: {data.get('cuotas_pendientes_total', 0)}\n"
            f"Días de atraso: {data.get('dias_de_atraso', 0)}\n"
            f"Cuotas pendientes estado 3: {data.get('pendientes_estado_3', 0)}\n"
            f"Cuotas vencidas estado 4: {data.get('vencidos_estado_4', 0)}\n\n"
            "Cuota pendiente a gestionar:\n"
            f"Número cuota: {cuota_principal_numero or 'N/A'}\n"
            f"Estado cuota: {cuota_principal_estado or 'N/A'}\n"
            f"Fecha pago: {cuota_principal_fecha_legible or cuota_principal_fecha or 'N/A'}\n"
            f"Valor total: {cuota_principal_valor_legible}\n"
            f"Días de mora de la cuota: {cuota_principal_dias_mora or 'N/A'}\n"
            f"Retrasado: {cuota_principal_retrasado or 'N/A'}\n"
            f"Etiqueta fecha: {cuota_principal_label_fecha or 'N/A'}\n\n"
            "Totales calculados desde detalle_cuotas:\n"
            f"Total pagado: {money(total_pagado)}\n"
            f"Total pendiente completo: {money(total_pendiente)}\n"
            f"Total vencido: {money(total_vencido)}\n"
            f"Total pendiente no vencido: {money(total_pendiente_no_vencido)}\n"
            f"Intereses pendientes: {money(intereses_pendientes)}\n"
            f"Costos pendientes: {money(costos_pendientes)}\n"
            f"Impuestos pendientes: {money(impuestos_pendientes)}\n\n"
            "Detalle de cuotas:\n"
            f"{detalle_cuotas_texto}"
        )
    else:
        contexto_ia = "No se recibió detalle suficiente de cuotas para interpretación completa."

    debug_texto = (
        "Debug pagos-mora\n"
        "Estado API: success\n"
        f"Total cuotas detectadas: {data.get('total_cuotas', 0)}\n"
        f"Cuotas pagadas: {data.get('cuotas_pagadas', 0)}\n"
        f"Cuotas pendientes total: {data.get('cuotas_pendientes_total', 0)}\n"
        f"Días de atraso: {data.get('dias_de_atraso', 0)}\n"
        f"Cuota pendiente: {cuota_principal_numero or 'N/A'}\n"
        f"Valor cuota pendiente: {cuota_principal_valor_legible}\n"
        f"Total pagado calculado: {money(total_pagado)}\n"
        f"Total pendiente calculado: {money(total_pendiente)}\n"
        f"Total vencido calculado: {money(total_vencido)}\n"
        f"Intereses pendientes: {money(intereses_pendientes)}\n"
        f"Costos pendientes: {money(costos_pendientes)}\n"
        f"Impuestos pendientes: {money(impuestos_pendientes)}"
    )

    return {
        "pago_data_json_texto": json.dumps(convertir_a_json_seguro(data), ensure_ascii=False, default=str),
        "pago_data_contexto_ia": contexto_ia,
        "pago_total_cuotas_texto": text_value(data.get("total_cuotas"), "0"),
        "pago_cuotas_pagadas_texto": text_value(data.get("cuotas_pagadas"), "0"),
        "pago_cuotas_pendientes_total_texto": text_value(data.get("cuotas_pendientes_total"), "0"),
        "pago_dias_de_atraso_texto": text_value(data.get("dias_de_atraso"), "0"),
        "pago_pendientes_estado_3_texto": text_value(data.get("pendientes_estado_3"), "0"),
        "pago_vencidos_estado_4_texto": text_value(data.get("vencidos_estado_4"), "0"),
        "pago_cuota_principal_id_texto": text_value(pago_pendiente.get("id")),
        "pago_cuota_principal_numero_texto": cuota_principal_numero,
        "pago_cuota_principal_fecha_texto": cuota_principal_fecha,
        "pago_cuota_principal_fecha_legible_texto": cuota_principal_fecha_legible,
        "pago_cuota_principal_valor_numero_texto": cuota_principal_valor_numero,
        "pago_cuota_principal_valor_legible_texto": cuota_principal_valor_legible,
        "pago_cuota_principal_dias_mora_texto": cuota_principal_dias_mora,
        "pago_cuota_principal_estado_texto": cuota_principal_estado,
        "pago_cuota_principal_retrasado_texto": cuota_principal_retrasado,
        "pago_cuota_principal_label_fecha_texto": cuota_principal_label_fecha,
        "pago_total_pagado_numero_texto": str(total_pagado),
        "pago_total_pagado_legible_texto": money(total_pagado),
        "pago_total_pagado_texto": money(total_pagado),
        "pago_total_pendiente_numero_texto": str(total_pendiente),
        "pago_total_pendiente_legible_texto": money(total_pendiente),
        "pago_total_pendiente_texto": money(total_pendiente),
        "pago_total_vencido_numero_texto": str(total_vencido),
        "pago_total_vencido_legible_texto": money(total_vencido),
        "pago_total_vencido_texto": money(total_vencido),
        "pago_total_pendiente_no_vencido_numero_texto": str(total_pendiente_no_vencido),
        "pago_total_pendiente_no_vencido_legible_texto": money(total_pendiente_no_vencido),
        "pago_total_pendiente_no_vencido_texto": money(total_pendiente_no_vencido),
        "pago_intereses_pendientes_numero_texto": str(intereses_pendientes),
        "pago_intereses_pendientes_legible_texto": money(intereses_pendientes),
        "pago_intereses_pendientes_texto": money(intereses_pendientes),
        "pago_costos_pendientes_numero_texto": str(costos_pendientes),
        "pago_costos_pendientes_legible_texto": money(costos_pendientes),
        "pago_costos_pendientes_texto": money(costos_pendientes),
        "pago_impuestos_pendientes_numero_texto": str(impuestos_pendientes),
        "pago_impuestos_pendientes_legible_texto": money(impuestos_pendientes),
        "pago_impuestos_pendientes_texto": money(impuestos_pendientes),
        "pago_detalle_cuotas_texto": detalle_cuotas_texto,
        "pago_debug_texto": debug_texto,
    }


def build_pago_text_fields(data: Dict[str, Any]) -> Dict[str, str]:
    detalle_cuotas = data.get("detalle_cuotas") if isinstance(data.get("detalle_cuotas"), list) else []
    pago_pendiente = data.get("pago_pendiente") if isinstance(data.get("pago_pendiente"), dict) else {}
    desglose_pendiente = pago_pendiente.get("desglose_completo") if isinstance(pago_pendiente.get("desglose_completo"), dict) else {}
    componentes_corrientes = "componentes_corrientes"
    componentes_heredados = "componentes_heredados_por_cuotas_vencidas"
    totales_pendiente = "totales"

    total_pagado = 0
    total_pendiente = 0
    total_vencido = 0
    total_pendiente_no_vencido = 0
    intereses_pendientes = 0
    costos_pendientes = 0
    impuestos_pendientes = 0

    for cuota in detalle_cuotas:
        if not isinstance(cuota, dict):
            continue
        estado_codigo = cuota.get("estado_codigo")
        total_cuota = to_int(get_nested(cuota, "desglose", "total", "valor_redondeado"))
        if estado_codigo == 1:
            total_pagado += suma_payment_amount_pagado(cuota)
        elif estado_codigo in (3, 4):
            total_pendiente += total_cuota
            intereses_pendientes += to_int(get_nested(cuota, "desglose", "intereses", "valor_redondeado"))
            costos_pendientes += to_int(get_nested(cuota, "desglose", "costos", "valor_redondeado"))
            impuestos_pendientes += to_int(get_nested(cuota, "desglose", "impuestos", "valor_redondeado"))
            if estado_codigo == 4:
                total_vencido += total_cuota
            elif estado_codigo == 3:
                total_pendiente_no_vencido += total_cuota

    detalle_cuotas_texto = (
        "\n".join(linea_detalle_cuota_texto(cuota) for cuota in detalle_cuotas if isinstance(cuota, dict))
        if detalle_cuotas
        else "No se recibio detalle de cuotas."
    )

    cuota_pendiente_id = text_value(pago_pendiente.get("id"))
    cuota_pendiente_numero = text_value(pago_pendiente.get("numero_de_cuota"))
    cuota_pendiente_estado = text_value(pago_pendiente.get("estado_cuota"))
    cuota_pendiente_fecha = text_value(pago_pendiente.get("fecha_pago"))
    cuota_pendiente_fecha_legible = text_value(pago_pendiente.get("fecha_pago_legible"))
    cuota_pendiente_dias_mora = text_value(pago_pendiente.get("dias_de_mora_cuota"))
    cuota_pendiente_retrasado = str(bool(pago_pendiente.get("retrasado"))).lower() if "retrasado" in pago_pendiente else ""
    cuota_pendiente_label_fecha = text_value(pago_pendiente.get("label_fecha"))

    total_general_int = to_int(get_nested(desglose_pendiente, totales_pendiente, "total_cuota_pendiente", "valor_redondeado", default=pago_pendiente.get("valor_total_pagar")))
    capital_int = to_int(desglose_completo_value(desglose_pendiente, componentes_corrientes, "capital"))
    intereses_corrientes_int = to_int(desglose_completo_value(desglose_pendiente, componentes_corrientes, "intereses_corrientes"))
    costos_int = to_int(desglose_completo_value(desglose_pendiente, componentes_corrientes, "costos"))
    impuestos_int = to_int(desglose_completo_value(desglose_pendiente, componentes_corrientes, "impuestos"))
    mora_heredada_int = to_int(desglose_completo_value(desglose_pendiente, componentes_heredados, "mora_heredada"))
    cargos_cobranza_int = to_int(desglose_completo_value(desglose_pendiente, componentes_heredados, "cargos_cobranza"))
    costos_pendientes_cuota_int = to_int(desglose_completo_value(desglose_pendiente, componentes_heredados, "costos_pendientes"))
    impuestos_pendientes_cuota_int = to_int(desglose_completo_value(desglose_pendiente, componentes_heredados, "impuestos_pendientes"))
    intereses_mora_int = to_int(desglose_completo_value(desglose_pendiente, componentes_heredados, "intereses_mora"))
    total_corriente_int = to_int(get_nested(desglose_pendiente, totales_pendiente, "total_componentes_corrientes", "valor_redondeado"))
    total_heredado_int = to_int(get_nested(desglose_pendiente, totales_pendiente, "total_componentes_heredados", "valor_redondeado"))
    total_calculado_int = to_int(get_nested(desglose_pendiente, totales_pendiente, "total_calculado", "valor_redondeado"))
    diferencia_redondeo_int = to_int(get_nested(desglose_pendiente, totales_pendiente, "diferencia_redondeo", "valor_redondeado"))
    alerta_redondeo = bool(get_nested(desglose_pendiente, totales_pendiente, "diferencia_redondeo", "alerta_revision", default=False))

    intereses_corrientes_dias = text_value(get_nested(desglose_pendiente, componentes_corrientes, "intereses_corrientes", "dias"), "0")
    intereses_corrientes_tasa = text_value(get_nested(desglose_pendiente, componentes_corrientes, "intereses_corrientes", "tasa"), "N/A")
    cargos_cobranza_dias = text_value(get_nested(desglose_pendiente, componentes_heredados, "cargos_cobranza", "dias"), "0")
    cargos_cobranza_porcentaje = text_value(get_nested(desglose_pendiente, componentes_heredados, "cargos_cobranza", "porcentaje"), "N/A")
    intereses_mora_dias = text_value(get_nested(desglose_pendiente, componentes_heredados, "intereses_mora", "dias"), "0")
    intereses_mora_tasa = text_value(get_nested(desglose_pendiente, componentes_heredados, "intereses_mora", "tasa"), "N/A")

    cuota_pendiente_desglose_texto = build_cuota_pendiente_desglose_texto(pago_pendiente)
    cuota_pendiente_explicacion_texto = text_value(pago_pendiente.get("explicacion_consolidacion"), EXPLICACION_CONSOLIDACION_CUOTA_PENDIENTE)

    if detalle_cuotas:
        contexto_ia = (
            "Resumen de pagos One2Credit\n\n"
            "Informacion general:\n"
            f"Total cuotas: {data.get('total_cuotas', 0)}\n"
            f"Cuotas pagadas: {data.get('cuotas_pagadas', 0)}\n"
            f"Cuotas pendientes o vencidas: {data.get('cuotas_pendientes_total', 0)}\n"
            f"Dias de atraso: {data.get('dias_de_atraso', 0)}\n"
            f"Cuotas pendientes estado 3: {data.get('pendientes_estado_3', 0)}\n"
            f"Cuotas vencidas estado 4: {data.get('vencidos_estado_4', 0)}\n\n"
            "Cuota pendiente a gestionar:\n"
            f"Numero cuota: {cuota_pendiente_numero or 'N/A'}\n"
            f"Estado cuota: {cuota_pendiente_estado or 'N/A'}\n"
            f"Fecha pago: {cuota_pendiente_fecha_legible or cuota_pendiente_fecha or 'N/A'}\n"
            f"Valor total: {money(total_general_int)}\n"
            f"Dias de mora de la cuota: {cuota_pendiente_dias_mora or 'N/A'}\n"
            f"Retrasado: {cuota_pendiente_retrasado or 'N/A'}\n"
            f"Etiqueta fecha: {cuota_pendiente_label_fecha or 'N/A'}\n\n"
            "Desglose completo de la cuota pendiente:\n"
            f"{cuota_pendiente_desglose_texto}\n\n"
            "Explicacion de consolidacion:\n"
            f"{cuota_pendiente_explicacion_texto}\n\n"
            "Totales calculados desde detalle_cuotas:\n"
            f"Total pagado: {money(total_pagado)}\n"
            f"Total pendiente completo: {money(total_pendiente)}\n"
            f"Total vencido: {money(total_vencido)}\n"
            f"Total pendiente no vencido: {money(total_pendiente_no_vencido)}\n"
            f"Intereses pendientes: {money(intereses_pendientes)}\n"
            f"Costos pendientes: {money(costos_pendientes)}\n"
            f"Impuestos pendientes: {money(impuestos_pendientes)}\n\n"
            "Detalle de cuotas:\n"
            f"{detalle_cuotas_texto}"
        )
    else:
        contexto_ia = "No se recibio detalle suficiente de cuotas para interpretacion completa."

    debug_texto = (
        "Debug pagos-mora\n"
        "Estado API: success\n"
        f"Total cuotas detectadas: {data.get('total_cuotas', 0)}\n"
        f"Cuotas pagadas: {data.get('cuotas_pagadas', 0)}\n"
        f"Cuotas pendientes total: {data.get('cuotas_pendientes_total', 0)}\n"
        f"Dias de atraso: {data.get('dias_de_atraso', 0)}\n"
        f"Cuota pendiente detectada: {cuota_pendiente_numero or 'N/A'}\n"
        f"Valor cuota pendiente: {money(total_general_int)}\n"
        f"Total pagado calculado: {money(total_pagado)}\n"
        f"Total pendiente calculado: {money(total_pendiente)}\n"
        f"Total vencido calculado: {money(total_vencido)}\n"
        f"Intereses pendientes: {money(intereses_pendientes)}\n"
        f"Costos pendientes: {money(costos_pendientes)}\n"
        f"Impuestos pendientes: {money(impuestos_pendientes)}\n"
        f"Capital cuota pendiente: {money(capital_int)}\n"
        f"Intereses corrientes cuota pendiente: {money(intereses_corrientes_int)}\n"
        f"Costos cuota pendiente: {money(costos_int)}\n"
        f"Impuestos cuota pendiente: {money(impuestos_int)}\n"
        f"Mora heredada cuota pendiente: {money(mora_heredada_int)}\n"
        f"Cargos cobranza cuota pendiente: {money(cargos_cobranza_int)}\n"
        f"Costos pendientes cuota pendiente: {money(costos_pendientes_cuota_int)}\n"
        f"Impuestos pendientes cuota pendiente: {money(impuestos_pendientes_cuota_int)}\n"
        f"Intereses mora cuota pendiente: {money(intereses_mora_int)}\n"
        f"Total corriente cuota pendiente: {money(total_corriente_int)}\n"
        f"Total heredado cuota pendiente: {money(total_heredado_int)}\n"
        f"Total general cuota pendiente: {money(total_general_int)}\n"
        f"Diferencia redondeo: {money_signed(diferencia_redondeo_int)}"
    )
    if alerta_redondeo:
        debug_texto += "\nAlerta diferencia redondeo: revisar diferencia mayor a 5 pesos."

    fields = {
        "pago_data_json_texto": json.dumps(convertir_a_json_seguro(data), ensure_ascii=False, default=str),
        "pago_data_contexto_ia": contexto_ia,
        "pago_total_cuotas_texto": text_value(data.get("total_cuotas"), "0"),
        "pago_cuotas_pagadas_texto": text_value(data.get("cuotas_pagadas"), "0"),
        "pago_cuotas_pendientes_total_texto": text_value(data.get("cuotas_pendientes_total"), "0"),
        "pago_dias_de_atraso_texto": text_value(data.get("dias_de_atraso"), "0"),
        "pago_pendientes_estado_3_texto": text_value(data.get("pendientes_estado_3"), "0"),
        "pago_vencidos_estado_4_texto": text_value(data.get("vencidos_estado_4"), "0"),
        "pago_cuota_pendiente_id_texto": cuota_pendiente_id,
        "pago_cuota_pendiente_numero_texto": cuota_pendiente_numero,
        "pago_cuota_pendiente_estado_texto": cuota_pendiente_estado,
        "pago_cuota_pendiente_fecha_texto": cuota_pendiente_fecha,
        "pago_cuota_pendiente_fecha_legible_texto": cuota_pendiente_fecha_legible,
        "pago_cuota_pendiente_retrasado_texto": cuota_pendiente_retrasado,
        "pago_cuota_pendiente_label_fecha_texto": cuota_pendiente_label_fecha,
        "pago_cuota_pendiente_dias_mora_texto": cuota_pendiente_dias_mora,
        "pago_cuota_pendiente_total_numero_texto": str(total_general_int),
        "pago_cuota_pendiente_total_texto": money(total_general_int),
        "pago_cuota_pendiente_capital_numero_texto": str(capital_int),
        "pago_cuota_pendiente_capital_texto": money(capital_int),
        "pago_cuota_pendiente_intereses_corrientes_numero_texto": str(intereses_corrientes_int),
        "pago_cuota_pendiente_intereses_corrientes_texto": money(intereses_corrientes_int),
        "pago_cuota_pendiente_intereses_corrientes_dias_texto": intereses_corrientes_dias,
        "pago_cuota_pendiente_intereses_corrientes_tasa_texto": intereses_corrientes_tasa,
        "pago_cuota_pendiente_costos_numero_texto": str(costos_int),
        "pago_cuota_pendiente_costos_texto": money(costos_int),
        "pago_cuota_pendiente_impuestos_numero_texto": str(impuestos_int),
        "pago_cuota_pendiente_impuestos_texto": money(impuestos_int),
        "pago_cuota_pendiente_mora_heredada_numero_texto": str(mora_heredada_int),
        "pago_cuota_pendiente_mora_heredada_texto": money(mora_heredada_int),
        "pago_cuota_pendiente_cargos_cobranza_numero_texto": str(cargos_cobranza_int),
        "pago_cuota_pendiente_cargos_cobranza_texto": money(cargos_cobranza_int),
        "pago_cuota_pendiente_cargos_cobranza_dias_texto": cargos_cobranza_dias,
        "pago_cuota_pendiente_cargos_cobranza_porcentaje_texto": cargos_cobranza_porcentaje,
        "pago_cuota_pendiente_costos_pendientes_numero_texto": str(costos_pendientes_cuota_int),
        "pago_cuota_pendiente_costos_pendientes_texto": money(costos_pendientes_cuota_int),
        "pago_cuota_pendiente_impuestos_pendientes_numero_texto": str(impuestos_pendientes_cuota_int),
        "pago_cuota_pendiente_impuestos_pendientes_texto": money(impuestos_pendientes_cuota_int),
        "pago_cuota_pendiente_intereses_mora_numero_texto": str(intereses_mora_int),
        "pago_cuota_pendiente_intereses_mora_texto": money(intereses_mora_int),
        "pago_cuota_pendiente_intereses_mora_dias_texto": intereses_mora_dias,
        "pago_cuota_pendiente_intereses_mora_tasa_texto": intereses_mora_tasa,
        "pago_cuota_pendiente_total_corriente_numero_texto": str(total_corriente_int),
        "pago_cuota_pendiente_total_corriente_texto": money(total_corriente_int),
        "pago_cuota_pendiente_total_heredado_numero_texto": str(total_heredado_int),
        "pago_cuota_pendiente_total_heredado_texto": money(total_heredado_int),
        "pago_cuota_pendiente_total_calculado_numero_texto": str(total_calculado_int),
        "pago_cuota_pendiente_total_calculado_texto": money(total_calculado_int),
        "pago_cuota_pendiente_total_general_numero_texto": str(total_general_int),
        "pago_cuota_pendiente_total_general_texto": money(total_general_int),
        "pago_cuota_pendiente_diferencia_redondeo_numero_texto": str(diferencia_redondeo_int),
        "pago_cuota_pendiente_diferencia_redondeo_texto": money_signed(diferencia_redondeo_int),
        "pago_cuota_pendiente_desglose_texto": cuota_pendiente_desglose_texto,
        "pago_cuota_pendiente_explicacion_consolidacion_texto": cuota_pendiente_explicacion_texto,
        "pago_total_pagado_numero_texto": str(total_pagado),
        "pago_total_pagado_legible_texto": money(total_pagado),
        "pago_total_pagado_texto": money(total_pagado),
        "pago_total_pendiente_numero_texto": str(total_pendiente),
        "pago_total_pendiente_legible_texto": money(total_pendiente),
        "pago_total_pendiente_texto": money(total_pendiente),
        "pago_total_vencido_numero_texto": str(total_vencido),
        "pago_total_vencido_legible_texto": money(total_vencido),
        "pago_total_vencido_texto": money(total_vencido),
        "pago_total_pendiente_no_vencido_numero_texto": str(total_pendiente_no_vencido),
        "pago_total_pendiente_no_vencido_legible_texto": money(total_pendiente_no_vencido),
        "pago_total_pendiente_no_vencido_texto": money(total_pendiente_no_vencido),
        "pago_intereses_pendientes_numero_texto": str(intereses_pendientes),
        "pago_intereses_pendientes_legible_texto": money(intereses_pendientes),
        "pago_intereses_pendientes_texto": money(intereses_pendientes),
        "pago_costos_pendientes_numero_texto": str(costos_pendientes),
        "pago_costos_pendientes_legible_texto": money(costos_pendientes),
        "pago_costos_pendientes_texto": money(costos_pendientes),
        "pago_impuestos_pendientes_numero_texto": str(impuestos_pendientes),
        "pago_impuestos_pendientes_legible_texto": money(impuestos_pendientes),
        "pago_impuestos_pendientes_texto": money(impuestos_pendientes),
        "pago_detalle_cuotas_texto": detalle_cuotas_texto,
        "pago_debug_texto": debug_texto,
    }
    fields.update(
        {
            "pago_cuota_principal_id_texto": fields["pago_cuota_pendiente_id_texto"],
            "pago_cuota_principal_numero_texto": fields["pago_cuota_pendiente_numero_texto"],
            "pago_cuota_principal_fecha_texto": fields["pago_cuota_pendiente_fecha_texto"],
            "pago_cuota_principal_fecha_legible_texto": fields["pago_cuota_pendiente_fecha_legible_texto"],
            "pago_cuota_principal_valor_numero_texto": fields["pago_cuota_pendiente_total_numero_texto"],
            "pago_cuota_principal_valor_legible_texto": fields["pago_cuota_pendiente_total_texto"],
            "pago_cuota_principal_dias_mora_texto": fields["pago_cuota_pendiente_dias_mora_texto"],
            "pago_cuota_principal_estado_texto": fields["pago_cuota_pendiente_estado_texto"],
            "pago_cuota_principal_retrasado_texto": fields["pago_cuota_pendiente_retrasado_texto"],
            "pago_cuota_principal_label_fecha_texto": fields["pago_cuota_pendiente_label_fecha_texto"],
        }
    )
    return fields


PARAMS_ESTANDAR_RECEIVABLES_MORA = {
    "offset": 0,
    "limit": 10,
    "statusCounts": "true",
    "include": "summary,installments",
    "status": "0,1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,22",
    "order": "updated_at:desc",
}


def es_id_credito_kuenta(valor: Any) -> bool:
    texto = str(valor or "").strip()
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            texto,
        )
    )


def construir_params_receivables_mora(cedula_o_busqueda: str) -> Dict[str, Any]:
    params = dict(PARAMS_ESTANDAR_RECEIVABLES_MORA)
    params["q"] = str(cedula_o_busqueda or "").strip()
    return params


def estados_credito_mora_elegibles() -> set:
    estados_configurados = globals().get(
        "ESTADOS_CREDITO_ONE2CREDIT_CONSULTA_MORA",
        {7, 10, 16},
    )
    estados = set()
    for estado in estados_configurados:
        try:
            estados.add(int(estado))
        except (TypeError, ValueError):
            continue
    return estados or {7, 10, 16}


def status_credito_int(credito: Dict[str, Any]) -> Optional[int]:
    try:
        return int(credito.get("status"))
    except (TypeError, ValueError, AttributeError):
        return None


def extraer_creditos_desde_respuesta_mora(response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = response_data.get("data") if isinstance(response_data, dict) else {}
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    data = data if isinstance(data, dict) else {}
    for key in ("credits", "receivables", "items"):
        creditos = data.get(key)
        if isinstance(creditos, list):
            return [item for item in creditos if isinstance(item, dict)]
    return []


def contar_estados_installments(installments: Any) -> Dict[str, int]:
    conteo: Dict[str, int] = {}
    if not isinstance(installments, list):
        return conteo
    for installment in installments:
        if not isinstance(installment, dict):
            continue
        status = installment.get("status")
        key = str(status if status is not None else "desconocido")
        conteo[key] = conteo.get(key, 0) + 1
    return conteo


def resumir_credito_mora(credito: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(credito, dict):
        return {}
    installments = credito.get("installments") if isinstance(credito.get("installments"), list) else []
    summary = credito.get("summary") if isinstance(credito.get("summary"), dict) else {}
    installment_info = credito.get("installmentInfo") if isinstance(credito.get("installmentInfo"), dict) else {}
    next_installment = installment_info.get("nextInstallment") if isinstance(installment_info.get("nextInstallment"), dict) else {}
    monto_siguiente = next_installment.get("amount")
    return {
        "id": credito.get("ID") or credito.get("id"),
        "status": status_credito_int(credito),
        "reference": credito.get("reference"),
        "consecutive": credito.get("consecutive"),
        "parentID": credito.get("parentID") or credito.get("parentId"),
        "updatedAt": credito.get("updatedAt"),
        "createdAt": credito.get("createdAt"),
        "creditLineTitle": get_nested(credito, "creditLine", "title"),
        "balance": valor_original_para_json(summary.get("balance")),
        "installments_total": len(installments),
        "installments_status_counts": contar_estados_installments(installments),
        "has_overdue_installments": installment_info.get("hasOverdueInstallments"),
        "pending_installments_count": installment_info.get("pendingInstallmentsCount"),
        "next_installment_number": next_installment.get("number"),
        "next_installment_amount": valor_original_para_json(monto_siguiente),
        "next_installment_amount_legible": money(monto_siguiente),
    }


def linea_resumen_credito_mora_texto(credito: Dict[str, Any], indice: int = 0) -> str:
    prefijo = f"Credito {indice}: " if indice else "Credito: "
    return (
        f"{prefijo}"
        f"ID {text_value(credito.get('id'), 'N/A')} | "
        f"Estado {text_value(credito.get('status'), 'N/A')} | "
        f"Referencia {text_value(credito.get('reference'), 'N/A')} | "
        f"Linea {text_value(credito.get('creditLineTitle'), 'N/A')} | "
        f"Actualizado {text_value(credito.get('updatedAt'), 'N/A')} | "
        f"Cuotas {text_value(credito.get('installments_total'), '0')} | "
        f"Pendientes {text_value(credito.get('pending_installments_count'), '0')} | "
        f"Siguiente cuota {text_value(credito.get('next_installment_number'), 'N/A')} "
        f"{text_value(credito.get('next_installment_amount_legible'), '$0')}"
    )


def resumen_creditos_mora_texto(creditos: Any, fallback: str) -> str:
    if not isinstance(creditos, list) or not creditos:
        return fallback
    lineas = [
        linea_resumen_credito_mora_texto(credito, indice)
        for indice, credito in enumerate(creditos, start=1)
        if isinstance(credito, dict)
    ]
    return "\n".join(lineas) or fallback


def seleccionar_credito_mora_desde_coleccion(response_data: Dict[str, Any]) -> Dict[str, Any]:
    data = response_data.get("data") if isinstance(response_data, dict) else {}
    data = data if isinstance(data, dict) else {}
    creditos = extraer_creditos_desde_respuesta_mora(response_data)
    estados_elegibles = sorted(estados_credito_mora_elegibles())
    estados_elegibles_set = set(estados_elegibles)
    creditos_elegibles_raw = [
        credito
        for credito in creditos
        if status_credito_int(credito) in estados_elegibles_set
    ]
    credito_seleccionado_raw = creditos_elegibles_raw[0] if creditos_elegibles_raw else {}
    status_seleccionado = status_credito_int(credito_seleccionado_raw) if credito_seleccionado_raw else None
    creditos_mismo_estado_raw = [
        credito
        for credito in creditos_elegibles_raw
        if status_credito_int(credito) == status_seleccionado
    ] if status_seleccionado is not None else []
    alertas = []
    if not creditos:
        alertas.append("La consulta /receivables no retorno creditos.")
    elif not creditos_elegibles_raw:
        alertas.append(
            "La consulta retorno creditos, pero ninguno tiene estado elegible para pagos en mora."
        )
    elif len(creditos_mismo_estado_raw) > 1:
        alertas.append(
            f"Se encontraron {len(creditos_mismo_estado_raw)} creditos elegibles con estado {status_seleccionado}; se proceso el primero por order=updated_at:desc."
        )

    return {
        "source_of_truth": "data.credits",
        "criterio": "primer credito elegible por order=updated_at:desc",
        "estados_elegibles": estados_elegibles,
        "credito_seleccionado": resumir_credito_mora(credito_seleccionado_raw),
        "credito_seleccionado_raw": credito_seleccionado_raw,
        "creditos_elegibles_count": len(creditos_elegibles_raw),
        "creditos_elegibles_resumen": [resumir_credito_mora(c) for c in creditos_elegibles_raw],
        "creditos_mismo_estado_count": len(creditos_mismo_estado_raw),
        "creditos_mismo_estado_resumen": [resumir_credito_mora(c) for c in creditos_mismo_estado_raw],
        "creditos_mismo_estado_seleccionado": [resumir_credito_mora(c) for c in creditos_mismo_estado_raw],
        "creditos_recibidos_count": len(creditos),
        "creditos_recibidos_resumen": [resumir_credito_mora(c) for c in creditos],
        "status_counts": data.get("statusCounts") or response_data.get("statusCounts") or {},
        "alertas_seleccion": alertas,
    }


def serializar_seleccion_credito_mora(seleccion: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(seleccion, dict):
        return {
            "source_of_truth": "data.credit",
            "criterio": "consulta directa por id_credito",
            "estados_elegibles": sorted(estados_credito_mora_elegibles()),
            "creditos_elegibles_count": 0,
            "creditos_elegibles_resumen": [],
            "creditos_mismo_estado_count": 0,
            "creditos_mismo_estado_resumen": [],
            "status_counts": {},
            "alertas_seleccion": [],
        }
    return {
        key: value
        for key, value in seleccion.items()
        if key != "credito_seleccionado_raw"
    }


def estado_cuota_mora_descripcion(status: Any) -> str:
    return mapear_estado_cuota(status).get("estado_descripcion", "desconocida")


def status_cuota_int(installment: Dict[str, Any]) -> Optional[int]:
    try:
        return int(installment.get("status"))
    except (TypeError, ValueError):
        return None


def numero_cuota_int(installment: Dict[str, Any]) -> int:
    return to_int(installment.get("number"))


def buscar_flow_payment_cuota_inicial(credito: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    flow_payments = credito.get("flowPayments") if isinstance(credito.get("flowPayments"), list) else []
    for flow_payment in flow_payments:
        if not isinstance(flow_payment, dict):
            continue
        key = str(flow_payment.get("key") or "").strip().lower()
        payment_type = str(flow_payment.get("type") or "").strip().lower()
        if key == "cuota_inicial" or payment_type == "initial_fee":
            return flow_payment
    return None


def evaluar_cuota_inicial_mora(
    credito: Dict[str, Any],
    installments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    alertas: List[str] = []
    estados = [status_cuota_int(installment) for installment in installments]
    cuotas_inactivas = sum(1 for estado in estados if estado == 0)
    todas_las_cuotas_inactivas = bool(installments) and cuotas_inactivas == len(installments)

    flow_payment = buscar_flow_payment_cuota_inicial(credito)
    registro_encontrado = isinstance(flow_payment, dict)
    flow_payment = flow_payment or {}
    valor_flow_presente = registro_encontrado and "amount" in flow_payment
    valor_flow_decimal = convertir_decimal_cuota(flow_payment.get("amount")) if registro_encontrado else None
    valor_flow_invalido = valor_flow_presente and valor_flow_decimal is None
    valor_initial_fee_decimal = convertir_decimal_cuota(credito.get("initialFee"))
    valor_decimal = valor_flow_decimal if valor_flow_decimal is not None else valor_initial_fee_decimal
    valor_original = normalizar_valor_original_cuota(valor_decimal)
    valor_redondeado = redondear_valor_cuota(valor_decimal)
    approved_original = flow_payment.get("approved") if registro_encontrado else None
    approved = approved_original is True

    if not registro_encontrado:
        estado = "sin_registro"
    elif approved:
        estado = "pagada"
    else:
        estado = "pendiente"

    valores_difieren = bool(
        valor_flow_decimal is not None
        and valor_initial_fee_decimal is not None
        and valor_flow_decimal != valor_initial_fee_decimal
    )
    if valores_difieren:
        alertas.append("flowPayments.cuota_inicial.amount difiere de initialFee")
    if valor_flow_invalido:
        alertas.append("flowPayments.cuota_inicial.amount no es un importe valido")

    if todas_las_cuotas_inactivas:
        if not registro_encontrado:
            alertas.append("cuotas inactivas sin registro flowPayments para cuota inicial")
        elif approved:
            alertas.append("cuota inicial pagada; cuotas pendientes de activacion")
        elif valor_decimal is None or valor_decimal <= 0:
            alertas.append("cuota inicial pendiente sin importe valido para crear orden")

    orden_habilitada = bool(
        todas_las_cuotas_inactivas
        and registro_encontrado
        and not approved
        and valor_decimal is not None
        and valor_decimal > 0
        and not valores_difieren
        and not valor_flow_invalido
    )
    evidencia = []
    if registro_encontrado:
        evidencia.append("data.credit.flowPayments[key=cuota_inicial|type=initial_fee]")
    if valor_flow_decimal is None and valor_initial_fee_decimal is not None:
        evidencia.append("data.credit.initialFee como fallback")

    return {
        "aplica": bool(registro_encontrado or valor_initial_fee_decimal is not None),
        "registro_encontrado": registro_encontrado,
        "key": flow_payment.get("key") or "cuota_inicial",
        "type": flow_payment.get("type") or "initial_fee",
        "estado": estado,
        "approved": approved,
        "approved_original": approved_original,
        "approvedAt": flow_payment.get("approvedAt"),
        "orderId": flow_payment.get("orderId"),
        "amount": valor_original,
        "amount_redondeado": valor_redondeado,
        "amount_legible": money(valor_redondeado),
        "fuente_amount": "flowPayments.amount" if valor_flow_decimal is not None else "initialFee",
        "cuotas_inactivas_estado_0": cuotas_inactivas,
        "todas_las_cuotas_inactivas": todas_las_cuotas_inactivas,
        "orden_habilitada": orden_habilitada,
        "id_cuota_orden": "zero" if orden_habilitada else None,
        "key_orden": "cuota_inicial" if orden_habilitada else None,
        "evidencia_utilizada": evidencia,
        "alertas_integridad": alertas,
    }


def valor_original_para_json(value: Any) -> Any:
    return normalizar_valor_original_cuota(value)


def valor_capital_cuota(installment: Dict[str, Any]) -> Any:
    if status_cuota_int(installment) == 1:
        return installment.get("capitalPaid", installment.get("capital"))
    return installment.get("capital")


def calcular_retraso_cuota(fecha_pago: Optional[str]) -> bool:
    if not fecha_pago:
        return False
    try:
        fecha_cuota = datetime.fromisoformat(str(fecha_pago).replace("Z", "+00:00")).date()
        return fecha_cuota < datetime.now().date()
    except Exception:
        return False


def calcular_dias_atraso_oficial(installments: List[Dict[str, Any]], resumen: Dict[str, Any]) -> int:
    cuota_pendiente = next((i for i in installments if status_cuota_int(i) == 3), None)
    if cuota_pendiente:
        penalty_days = to_int(cuota_pendiente.get("penaltyDays"))
        if penalty_days > 0:
            return penalty_days
    dias_mora = [
        to_int(i.get("debtInterestDays"))
        for i in installments
        if status_cuota_int(i) in (3, 4)
    ]
    mayor_dia_mora = max(dias_mora or [0])
    if mayor_dia_mora > 0:
        return mayor_dia_mora
    return to_int(resumen.get("debtDays"))


def construir_detalle_cuota_oficial(installment: Dict[str, Any]) -> Dict[str, Any]:
    estado_codigo = status_cuota_int(installment)
    estado_cuota = estado_cuota_mora_descripcion(estado_codigo)
    fecha_pago = installment.get("date")
    pagos = installment.get("payments") if isinstance(installment.get("payments"), list) else []
    total = to_int(installment.get("payment"))
    capital = to_int(valor_capital_cuota(installment))
    intereses = to_int(installment.get("interest"))
    costos = to_int(installment.get("costs"))
    impuestos = to_int(installment.get("taxes"))
    detalle = {
        "id": installment.get("id"),
        "numero_de_cuota": installment.get("number"),
        "estado_codigo": estado_codigo,
        "estado_cuota": estado_cuota,
        "fecha_pago": fecha_pago,
        "fecha_pago_legible": formatear_fecha_legible(fecha_pago) if fecha_pago else "N/A",
        "valor_total_redondeado": total,
        "valor_total_legible": money(total),
        "capital_redondeado": capital,
        "capital_legible": money(capital),
        "intereses_redondeado": intereses,
        "intereses_legible": money(intereses),
        "costos_redondeado": costos,
        "costos_legible": money(costos),
        "impuestos_redondeado": impuestos,
        "impuestos_legible": money(impuestos),
        "pagos_registrados": len(pagos),
    }
    if estado_codigo == 4:
        detalle.update(
            {
                "intereses_mora_redondeado": to_int(installment.get("debtInterest")),
                "intereses_mora_legible": money(installment.get("debtInterest")),
                "dias_intereses_mora": installment.get("debtInterestDays"),
                "mora_heredada_redondeado": to_int(installment.get("debtPayment")),
                "cargos_cobranza_redondeado": to_int(installment.get("penalty")),
                "costos_pendientes_redondeado": to_int(installment.get("debtCosts")),
                "impuestos_pendientes_redondeado": to_int(installment.get("debtTaxes")),
            }
        )
    if estado_codigo == 3:
        desglose = build_desglose_cuota_pendiente(installment)
        heredados = "componentes_heredados_por_atraso"
        corrientes = "componentes_corrientes"
        detalle.update(
            {
                "intereses_corrientes_redondeado": intereses,
                "intereses_corrientes_legible": money(intereses),
                "mora_heredada_redondeado": to_int(desglose_completo_value(desglose, heredados, "mora_heredada")),
                "mora_heredada_legible": desglose_completo_money(desglose, heredados, "mora_heredada"),
                "cargos_cobranza_redondeado": to_int(desglose_completo_value(desglose, heredados, "cargos_cobranza")),
                "cargos_cobranza_legible": desglose_completo_money(desglose, heredados, "cargos_cobranza"),
                "costos_pendientes_redondeado": to_int(desglose_completo_value(desglose, heredados, "costos_pendientes")),
                "costos_pendientes_legible": desglose_completo_money(desglose, heredados, "costos_pendientes"),
                "impuestos_pendientes_redondeado": to_int(desglose_completo_value(desglose, heredados, "impuestos_pendientes")),
                "impuestos_pendientes_legible": desglose_completo_money(desglose, heredados, "impuestos_pendientes"),
                "intereses_mora_redondeado": to_int(desglose_completo_value(desglose, heredados, "intereses_mora")),
                "intereses_mora_legible": desglose_completo_money(desglose, heredados, "intereses_mora"),
                "capital_legible": desglose_completo_money(desglose, corrientes, "capital"),
            }
        )
    return detalle


COMPONENTES_PAGO_CUOTA = (
    ("impuestos", "taxesPaid", "Impuestos"),
    ("costos", "costsPaid", "Costos"),
    ("cargos_cobranza", "penaltyPaid", "Cargos de cobranza"),
    ("intereses_mora", "debtInterestPaid", "Intereses de mora"),
    ("intereses_corrientes", "interestPaid", "Intereses corrientes"),
    ("intereses_adicionales", "additionalInterestPaid", "Intereses adicionales"),
    ("capital", "capitalPaid", "Capital"),
)


def construir_importe_pago(valor: Any, fuente: str) -> Dict[str, Any]:
    """Conserva el importe original y agrega representaciones de negocio."""
    return {
        "fuente": fuente,
        "valor_original": normalizar_valor_original_cuota(valor),
        "valor_redondeado": to_int(valor),
        "valor_legible": money(valor),
    }


def formatear_fecha_hora_pago_legible(fecha_iso: Any) -> str:
    """Convierte la fecha UTC de Kuenta a hora local de Colombia."""
    if not fecha_iso:
        return "N/A"
    try:
        fecha_obj = datetime.fromisoformat(str(fecha_iso).replace("Z", "+00:00"))
        if fecha_obj.tzinfo is None:
            fecha_obj = fecha_obj.replace(tzinfo=timezone.utc)
        fecha_bogota = fecha_obj.astimezone(ZoneInfo("America/Bogota"))
        hora_12 = fecha_bogota.hour % 12 or 12
        periodo = "a. m." if fecha_bogota.hour < 12 else "p. m."
        return (
            f"{fecha_bogota.day:02d}/{fecha_bogota.month:02d}/{fecha_bogota.year} "
            f"{hora_12}:{fecha_bogota.minute:02d} {periodo}"
        )
    except (TypeError, ValueError):
        return str(fecha_iso)


def construir_desglose_pago(
    source: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    return {
        clave: construir_importe_pago(source.get(campo, 0), campo)
        for clave, campo, _ in COMPONENTES_PAGO_CUOTA
    }


def construir_pago_pendiente_detallado(payment: Dict[str, Any]) -> Dict[str, Any]:
    fecha_registro = payment.get("registeredAt")
    return {
        "fecha_registro": {
            "valor_original": fecha_registro,
            "valor_legible": formatear_fecha_hora_pago_legible(fecha_registro),
            "zona_horaria": "America/Bogota",
        },
        "id_orden": payment.get("orderId"),
        "aplicacion": "En esta cuota",
        "valor_registrado": construir_importe_pago(payment.get("amount", 0), "amount"),
        "valor_pagado_en_esta_cuota": construir_importe_pago(
            payment.get("valuePaid", 0),
            "valuePaid",
        ),
        "desglose_pagado_en_esta_cuota": construir_desglose_pago(payment),
        "datos_fuente": convertir_a_json_seguro(dict(payment)),
    }


def decimal_pago_o_cero(valor: Any) -> Decimal:
    numero = convertir_decimal_cuota(valor)
    return numero if numero is not None else Decimal("0")


def agregar_alertas_conciliacion_pagos(
    cuota_pendiente: Dict[str, Any],
    pagos: List[Dict[str, Any]],
    alertas_integridad: List[str],
) -> None:
    valor_pagado_cuota = decimal_pago_o_cero(cuota_pendiente.get("valuePaid"))
    suma_transacciones = sum(
        (decimal_pago_o_cero(pago.get("valuePaid")) for pago in pagos),
        Decimal("0"),
    )
    suma_desglose = sum(
        (
            decimal_pago_o_cero(cuota_pendiente.get(campo))
            for _, campo, _ in COMPONENTES_PAGO_CUOTA
        ),
        Decimal("0"),
    )

    if abs(suma_transacciones - valor_pagado_cuota) > Decimal("5"):
        alertas_integridad.append(
            "suma de payments[].valuePaid difiere de installment.valuePaid por mas de 5 pesos"
        )
    if abs(suma_desglose - valor_pagado_cuota) > Decimal("5"):
        alertas_integridad.append(
            "desglose pagado acumulado difiere de installment.valuePaid por mas de 5 pesos"
        )


def construir_pago_pendiente_oficial(
    cuota_pendiente: Optional[Dict[str, Any]],
    dias_atraso_general: int,
    alertas_integridad: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(cuota_pendiente, dict):
        return None
    fecha_pago = cuota_pendiente.get("date")
    retrasado = calcular_retraso_cuota(fecha_pago)
    desglose = build_desglose_cuota_pendiente(cuota_pendiente)
    total = to_int(cuota_pendiente.get("payment"))
    payments_raw = cuota_pendiente.get("payments")
    if payments_raw is not None and not isinstance(payments_raw, list):
        if alertas_integridad is not None:
            alertas_integridad.append("payments de cuota pendiente no es una lista valida")
        payments_raw = []
    payments_raw = payments_raw or []
    pagos_validos = [payment for payment in payments_raw if isinstance(payment, dict)]
    if len(pagos_validos) != len(payments_raw) and alertas_integridad is not None:
        alertas_integridad.append("payments de cuota pendiente contiene registros invalidos")

    valor_pagado_original = cuota_pendiente.get("valuePaid", 0)
    valor_pendiente = decimal_pago_o_cero(cuota_pendiente.get("payment")) - decimal_pago_o_cero(
        valor_pagado_original
    )
    if alertas_integridad is not None:
        agregar_alertas_conciliacion_pagos(
            cuota_pendiente,
            pagos_validos,
            alertas_integridad,
        )
    return {
        "id": cuota_pendiente.get("id"),
        "numero_de_cuota": cuota_pendiente.get("number"),
        "estado_codigo": status_cuota_int(cuota_pendiente),
        "estado_cuota": "pendiente",
        "fecha_pago": fecha_pago,
        "fecha_pago_legible": formatear_fecha_legible(fecha_pago) if fecha_pago else "N/A",
        "valor_total_pagar": valor_original_para_json(cuota_pendiente.get("payment")),
        "valor_total_redondeado": total,
        "valor_total_legible": money(total),
        "dias_de_mora_cuota": cuota_pendiente.get("debtInterestDays"),
        "dias_de_atraso_general": dias_atraso_general,
        "retrasado": retrasado,
        "label_fecha": "FECHA VENCIDA" if retrasado else "PROXIMA FECHA DE PAGO",
        "desglose_completo": desglose,
        "pagos_registrados_cantidad": len(pagos_validos),
        "valor_pagado_registrado": construir_importe_pago(
            valor_pagado_original,
            "valuePaid",
        ),
        "valor_pendiente_a_la_fecha": construir_importe_pago(
            valor_pendiente,
            "payment-valuePaid",
        ),
        "desglose_pagado_acumulado": construir_desglose_pago(cuota_pendiente),
        "pagos": [construir_pago_pendiente_detallado(payment) for payment in pagos_validos],
        "explicacion_consolidacion": (
            "La cuota pendiente puede incluir valores propios de la cuota actual y valores heredados "
            "o asignados por cuotas vencidas anteriores, como mora heredada, cargos de cobranza, "
            "costos pendientes, impuestos pendientes e intereses de mora."
        ),
    }


def construir_data_pagos_mora_oficial(
    credito: Dict[str, Any],
    id_credito_solicitado: str,
    consulta_por_cedula: bool,
) -> Dict[str, Any]:
    alertas_integridad: List[str] = []
    installments_raw = credito.get("installments") if isinstance(credito.get("installments"), list) else []
    installments = sorted(
        [i for i in installments_raw if isinstance(i, dict)],
        key=lambda item: numero_cuota_int(item),
    )
    resumen_kuenta = credito.get("summary") if isinstance(credito.get("summary"), dict) else {}
    credito_id = credito.get("ID") or credito.get("id") or ""
    cuota_inicial = evaluar_cuota_inicial_mora(credito, installments)
    alertas_integridad.extend(cuota_inicial.get("alertas_integridad") or [])
    if not installments:
        alertas_integridad.append("installments vacio")
    if credito_id and not consulta_por_cedula and str(credito_id) != str(id_credito_solicitado):
        alertas_integridad.append("credit_id recibido no coincide con data.credit.ID")

    numeros_cuotas = [numero_cuota_int(i) for i in installments]
    total_cuotas = len(installments)
    max_numero_cuota = max(numeros_cuotas or [0])
    cuotas_pagadas_raw = [i for i in installments if status_cuota_int(i) == 1]
    cuotas_pendientes_raw = [i for i in installments if status_cuota_int(i) == 3]
    cuotas_vencidas_raw = [i for i in installments if status_cuota_int(i) == 4]
    cuota_pendiente_raw = cuotas_pendientes_raw[0] if cuotas_pendientes_raw else None
    if not cuota_pendiente_raw and not cuota_inicial.get("todas_las_cuotas_inactivas"):
        alertas_integridad.append("cuota_pendiente no encontrada")

    dias_atraso = calcular_dias_atraso_oficial(installments, resumen_kuenta)
    pago_pendiente = construir_pago_pendiente_oficial(
        cuota_pendiente_raw,
        dias_atraso,
        alertas_integridad,
    )
    pago_prioritario = None
    if (
        cuota_inicial.get("todas_las_cuotas_inactivas")
        and cuota_inicial.get("registro_encontrado")
        and not cuota_inicial.get("approved")
    ):
        pago_prioritario = {
            "tipo": "cuota_inicial",
            "estado": "pendiente",
            "key": "cuota_inicial",
            "id_credito": credito_id,
            "id_cuota_orden": cuota_inicial.get("id_cuota_orden"),
            "key_orden": cuota_inicial.get("key_orden"),
            "orden_habilitada": bool(cuota_inicial.get("orden_habilitada")),
            "valor_total_pagar": cuota_inicial.get("amount"),
            "valor_total_redondeado": cuota_inicial.get("amount_redondeado"),
            "valor_total_legible": cuota_inicial.get("amount_legible"),
            "motivo": "Las cuotas estan inactivas hasta registrar el pago de la cuota inicial.",
        }
    elif pago_pendiente:
        pago_prioritario = {
            "tipo": "cuota",
            "estado": pago_pendiente.get("estado_cuota"),
            "id_credito": credito_id,
            "id_cuota_orden": pago_pendiente.get("id"),
            "key_orden": None,
            "orden_habilitada": bool(pago_pendiente.get("id")),
            "numero_de_cuota": pago_pendiente.get("numero_de_cuota"),
            "fecha_pago": pago_pendiente.get("fecha_pago"),
            "fecha_pago_legible": pago_pendiente.get("fecha_pago_legible"),
            "valor_total_pagar": pago_pendiente.get("valor_total_pagar"),
            "valor_total_redondeado": pago_pendiente.get("valor_total_redondeado"),
            "valor_total_legible": pago_pendiente.get("valor_total_legible"),
            "motivo": "Primera cuota en estado pendiente disponible para pago.",
        }
    detalle_cuotas = [construir_detalle_cuota_oficial(i) for i in installments]
    cuotas_vencidas = [construir_detalle_cuota_oficial(i) for i in cuotas_vencidas_raw]

    linea_credito = (
        get_nested(credito, "creditLine", "title")
        or get_nested(credito, "line", "title")
        or get_nested(credito, "productLine", "title")
        or credito.get("title")
        or credito.get("reference")
        or ""
    )
    estado_credito = credito.get("status")
    validacion = (
        f"El credito consultado tiene {total_cuotas} cuotas. "
        f"La cuota maxima existente es la numero {max_numero_cuota}. "
        f"No existen cuotas mayores a la numero {max_numero_cuota} en este credito."
    )

    total_pagado = sum(to_int(i.get("payment")) for i in cuotas_pagadas_raw)
    total_vencido = sum(to_int(i.get("payment")) for i in cuotas_vencidas_raw)
    total_pendiente_no_vencido = sum(to_int(i.get("payment")) for i in cuotas_pendientes_raw)
    total_pendiente = total_vencido + total_pendiente_no_vencido
    intereses_pendientes = sum(to_int(i.get("interest")) for i in installments if status_cuota_int(i) in (3, 4))
    costos_pendientes = sum(to_int(i.get("costs")) for i in installments if status_cuota_int(i) in (3, 4))
    impuestos_pendientes = sum(to_int(i.get("taxes")) for i in installments if status_cuota_int(i) in (3, 4))
    mora_heredada_total = sum(to_int(i.get("debtPayment")) for i in installments if status_cuota_int(i) in (3, 4))
    intereses_mora_total = sum(to_int(i.get("debtInterest")) for i in installments if status_cuota_int(i) in (3, 4))
    cargos_cobranza_total = sum(to_int(i.get("penalty")) for i in installments if status_cuota_int(i) in (3, 4))
    costos_heredados_total = sum(to_int(i.get("debtCosts")) for i in installments if status_cuota_int(i) in (3, 4))
    impuestos_heredados_total = sum(to_int(i.get("debtTaxes")) for i in installments if status_cuota_int(i) in (3, 4))

    if pago_pendiente:
        diferencia = to_int(get_nested(pago_pendiente, "desglose_completo", "totales", "diferencia_redondeo", "valor_redondeado"))
        if abs(diferencia) > 5:
            alertas_integridad.append("total calculado difiere del payment por mas de 5 pesos")

    data = {
        "credito": {
            "id": credito_id,
            "linea": linea_credito,
            "estado_codigo": estado_credito,
            "total_cuotas": total_cuotas,
            "max_numero_cuota": max_numero_cuota,
            "validacion": validacion,
        },
        "resumen": {
            "total_cuotas": total_cuotas,
            "cuotas_pagadas": len(cuotas_pagadas_raw),
            "cuotas_pendientes_total": len(cuotas_pendientes_raw) + len(cuotas_vencidas_raw),
            "inactivas_estado_0": cuota_inicial.get("cuotas_inactivas_estado_0", 0),
            "pendientes_estado_3": len(cuotas_pendientes_raw),
            "vencidos_estado_4": len(cuotas_vencidas_raw),
            "dias_de_atraso": dias_atraso,
        },
        "cuota_inicial": cuota_inicial,
        "pago_prioritario": pago_prioritario,
        "pago_pendiente": pago_pendiente,
        "cuotas_vencidas": cuotas_vencidas,
        "detalle_cuotas": detalle_cuotas,
        "totales_calculados": {
            "total_pagado": total_pagado,
            "total_pendiente": total_pendiente,
            "total_vencido": total_vencido,
            "total_pendiente_no_vencido": total_pendiente_no_vencido,
            "intereses_pendientes": intereses_pendientes,
            "costos_pendientes": costos_pendientes,
            "impuestos_pendientes": impuestos_pendientes,
            "mora_heredada_total": mora_heredada_total,
            "intereses_mora_total": intereses_mora_total,
            "cargos_cobranza_total": cargos_cobranza_total,
            "costos_heredados_total": costos_heredados_total,
            "impuestos_heredados_total": impuestos_heredados_total,
        },
        # Compatibilidad interna temporal con el contrato anterior.
        "total_cuotas": total_cuotas,
        "cuotas_pagadas": len(cuotas_pagadas_raw),
        "cuotas_pendientes_total": len(cuotas_pendientes_raw) + len(cuotas_vencidas_raw),
        "inactivas_estado_0": cuota_inicial.get("cuotas_inactivas_estado_0", 0),
        "dias_de_atraso": dias_atraso,
        "pendientes_estado_3": len(cuotas_pendientes_raw),
        "vencidos_estado_4": len(cuotas_vencidas_raw),
    }
    return {
        "source_of_truth": "data.credit.flowPayments + data.credit.installments",
        "alertas_integridad": alertas_integridad,
        "data": data,
    }


def linea_cuota_resumen_texto(cuota: Dict[str, Any]) -> str:
    return (
        f"Cuota {text_value(cuota.get('numero_de_cuota'), 'N/A')}: "
        f"{text_value(cuota.get('estado_cuota'), 'desconocida')} - "
        f"{text_value(cuota.get('valor_total_legible'), '$0')}"
    )


def linea_detalle_cuota_oficial_texto(cuota: Dict[str, Any]) -> str:
    base = (
        f"Cuota {text_value(cuota.get('numero_de_cuota'), 'N/A')} | "
        f"Estado: {text_value(cuota.get('estado_cuota'), 'desconocida')} | "
        f"Fecha: {text_value(cuota.get('fecha_pago_legible'), 'N/A')} | "
        f"Total: {text_value(cuota.get('valor_total_legible'), '$0')} | "
        f"Capital: {text_value(cuota.get('capital_legible'), '$0')} | "
    )
    if cuota.get("estado_codigo") == 3:
        return (
            base +
            f"Intereses corrientes: {text_value(cuota.get('intereses_corrientes_legible'), '$0')} | "
            f"Costos: {text_value(cuota.get('costos_legible'), '$0')} | "
            f"Impuestos: {text_value(cuota.get('impuestos_legible'), '$0')} | "
            f"Mora heredada: {text_value(cuota.get('mora_heredada_legible'), '$0')} | "
            f"Cargos de cobranza: {text_value(cuota.get('cargos_cobranza_legible'), '$0')} | "
            f"Costos pendientes: {text_value(cuota.get('costos_pendientes_legible'), '$0')} | "
            f"Impuestos pendientes: {text_value(cuota.get('impuestos_pendientes_legible'), '$0')} | "
            f"Intereses de mora: {text_value(cuota.get('intereses_mora_legible'), '$0')} | "
            f"Pagos registrados: {text_value(cuota.get('pagos_registrados'), '0')}"
        )
    extra_mora = ""
    if cuota.get("estado_codigo") == 4 and to_int(cuota.get("intereses_mora_redondeado")):
        extra_mora = f" | Intereses de mora: {text_value(cuota.get('intereses_mora_legible'), '$0')}"
    return (
        base +
        f"Intereses: {text_value(cuota.get('intereses_legible'), '$0')} | "
        f"Costos: {text_value(cuota.get('costos_legible'), '$0')} | "
        f"Impuestos: {text_value(cuota.get('impuestos_legible'), '$0')}"
        f"{extra_mora} | Pagos registrados: {text_value(cuota.get('pagos_registrados'), '0')}"
    )


def importe_pago_legible(importe: Any, fallback: str = "$0") -> str:
    if not isinstance(importe, dict):
        return fallback
    return text_value(importe.get("valor_legible"), fallback)


def desglose_pago_texto(desglose: Any) -> str:
    if not isinstance(desglose, dict):
        return "Sin desglose aplicado"
    componentes = []
    for clave, _, label in COMPONENTES_PAGO_CUOTA:
        importe = desglose.get(clave) if isinstance(desglose.get(clave), dict) else {}
        if to_int(importe.get("valor_redondeado")) != 0:
            componentes.append(f"{label}: {importe_pago_legible(importe)}")
    return " | ".join(componentes) if componentes else "Sin desglose aplicado"


def build_pagos_cuota_pendiente_texto(pago_pendiente: Dict[str, Any]) -> str:
    if not isinstance(pago_pendiente, dict) or not pago_pendiente:
        return "No se recibio una cuota pendiente para consultar pagos registrados."

    pagos = pago_pendiente.get("pagos") if isinstance(pago_pendiente.get("pagos"), list) else []
    desglose_acumulado = pago_pendiente.get("desglose_pagado_acumulado")
    lineas_pagos = []
    for indice, pago in enumerate(pagos, 1):
        if not isinstance(pago, dict):
            continue
        fecha = pago.get("fecha_registro") if isinstance(pago.get("fecha_registro"), dict) else {}
        lineas_pagos.append(
            f"Pago {indice} | "
            f"Fecha: {text_value(fecha.get('valor_legible'), 'N/A')} | "
            f"ID de orden: {text_value(pago.get('id_orden'), 'N/A')} | "
            f"Valor registrado: {importe_pago_legible(pago.get('valor_registrado'))} | "
            f"Valor pagado en esta cuota: {importe_pago_legible(pago.get('valor_pagado_en_esta_cuota'))}\n"
            f"Aplicacion: {text_value(pago.get('aplicacion'), 'En esta cuota')} | "
            f"{desglose_pago_texto(pago.get('desglose_pagado_en_esta_cuota'))}"
        )

    detalle_transacciones = "\n".join(lineas_pagos) if lineas_pagos else "No hay pagos registrados en esta cuota."
    return (
        "Pagos registrados en la cuota pendiente\n"
        f"Cantidad: {text_value(pago_pendiente.get('pagos_registrados_cantidad'), '0')}\n"
        f"Valor pagado registrado: {importe_pago_legible(pago_pendiente.get('valor_pagado_registrado'))}\n"
        f"Valor pendiente a la fecha: {importe_pago_legible(pago_pendiente.get('valor_pendiente_a_la_fecha'))}\n"
        f"Desglose pagado acumulado: {desglose_pago_texto(desglose_acumulado)}\n"
        "Transacciones:\n"
        f"{detalle_transacciones}"
    )


def build_pago_text_fields_oficial(payload_oficial: Dict[str, Any]) -> Dict[str, str]:
    data = payload_oficial.get("data") if isinstance(payload_oficial.get("data"), dict) else {}
    credito = data.get("credito") if isinstance(data.get("credito"), dict) else {}
    resumen = data.get("resumen") if isinstance(data.get("resumen"), dict) else {}
    cuota_inicial = data.get("cuota_inicial") if isinstance(data.get("cuota_inicial"), dict) else {}
    pago_prioritario = data.get("pago_prioritario") if isinstance(data.get("pago_prioritario"), dict) else {}
    pago_pendiente = data.get("pago_pendiente") if isinstance(data.get("pago_pendiente"), dict) else {}
    detalle_cuotas = data.get("detalle_cuotas") if isinstance(data.get("detalle_cuotas"), list) else []
    cuotas_vencidas = data.get("cuotas_vencidas") if isinstance(data.get("cuotas_vencidas"), list) else []
    totales = data.get("totales_calculados") if isinstance(data.get("totales_calculados"), dict) else {}
    seleccion_credito = payload_oficial.get("seleccion_credito") if isinstance(payload_oficial.get("seleccion_credito"), dict) else {}
    desglose = pago_pendiente.get("desglose_completo") if isinstance(pago_pendiente.get("desglose_completo"), dict) else {}
    heredados_key = "componentes_heredados_por_atraso"
    corrientes_key = "componentes_corrientes"

    cuotas_reales_texto = "\n".join(linea_cuota_resumen_texto(c) for c in detalle_cuotas) or "No se recibieron cuotas reales."
    cuotas_pagadas_texto = "\n".join(linea_cuota_resumen_texto(c) for c in detalle_cuotas if c.get("estado_codigo") == 1) or "No hay cuotas pagadas."
    cuotas_vencidas_texto = "\n".join(linea_cuota_resumen_texto(c) for c in detalle_cuotas if c.get("estado_codigo") == 4) or "No hay cuotas vencidas."
    cuotas_pendientes_texto = "\n".join(linea_cuota_resumen_texto(c) for c in detalle_cuotas if c.get("estado_codigo") == 3) or "No hay cuotas pendientes."
    detalle_cuotas_texto = "\n".join(linea_detalle_cuota_oficial_texto(c) for c in detalle_cuotas) or "No se recibio detalle de cuotas."
    creditos_elegibles_texto = resumen_creditos_mora_texto(
        seleccion_credito.get("creditos_elegibles_resumen"),
        "No hay creditos elegibles informados.",
    )
    creditos_mismo_estado_texto = resumen_creditos_mora_texto(
        seleccion_credito.get("creditos_mismo_estado_resumen"),
        "No hay multiples creditos con el mismo estado elegible.",
    )
    cuota_vencida = cuotas_vencidas[0] if cuotas_vencidas else {}

    cuota_desglose_texto = build_cuota_pendiente_desglose_texto(pago_pendiente)
    pagos_cuota_pendiente_texto = build_pagos_cuota_pendiente_texto(pago_pendiente)
    detalle_cuotas_texto = f"{detalle_cuotas_texto}\n\n{pagos_cuota_pendiente_texto}"
    explicacion = text_value(
        pago_pendiente.get("explicacion_consolidacion"),
        "La cuota pendiente puede incluir valores propios de la cuota actual y valores heredados o asignados por cuotas vencidas anteriores, como mora heredada, cargos de cobranza, costos pendientes, impuestos pendientes e intereses de mora. Por eso el valor de la cuota pendiente puede ser mayor al valor corriente normal de una cuota.",
    )
    contexto_ia = (
        "Resumen de pagos One2Credit\n\n"
        "Fuente oficial: data.credit.flowPayments + data.credit.installments\n"
        f"Credito consultado: {text_value(credito.get('id'), 'N/A')}\n"
        f"Linea: {text_value(credito.get('linea'), 'N/A')}\n\n"
        "Seleccion de credito:\n"
        f"Criterio: {text_value(seleccion_credito.get('criterio'), 'consulta directa por id_credito')}\n"
        f"Creditos elegibles encontrados: {text_value(seleccion_credito.get('creditos_elegibles_count'), '0')}\n"
        f"Creditos con el mismo estado del seleccionado: {text_value(seleccion_credito.get('creditos_mismo_estado_count'), '0')}\n"
        f"Resumen creditos elegibles:\n{creditos_elegibles_texto}\n\n"
        "Validacion de cuotas:\n"
        f"{text_value(credito.get('validacion'), 'N/A')}\n\n"
        "Informacion general:\n"
        f"Total cuotas: {text_value(resumen.get('total_cuotas'), '0')}\n"
        f"Cuotas pagadas: {text_value(resumen.get('cuotas_pagadas'), '0')}\n"
        f"Cuotas pendientes o vencidas: {text_value(resumen.get('cuotas_pendientes_total'), '0')}\n"
        f"Cuotas inactivas estado 0: {text_value(resumen.get('inactivas_estado_0'), '0')}\n"
        f"Cuotas pendientes estado 3: {text_value(resumen.get('pendientes_estado_3'), '0')}\n"
        f"Cuotas vencidas estado 4: {text_value(resumen.get('vencidos_estado_4'), '0')}\n"
        f"Dias de atraso: {text_value(resumen.get('dias_de_atraso'), '0')}\n\n"
        "Cuota inicial:\n"
        f"Estado: {text_value(cuota_inicial.get('estado'), 'N/A')}\n"
        f"Pago aprobado: {text_value(cuota_inicial.get('approved'), 'False')}\n"
        f"Valor: {text_value(cuota_inicial.get('amount_legible'), '$0')}\n"
        f"Orden habilitada: {text_value(cuota_inicial.get('orden_habilitada'), 'False')}\n\n"
        "Pago prioritario:\n"
        f"Tipo: {text_value(pago_prioritario.get('tipo'), 'N/A')}\n"
        f"Valor: {text_value(pago_prioritario.get('valor_total_legible'), '$0')}\n"
        f"Motivo: {text_value(pago_prioritario.get('motivo'), 'N/A')}\n\n"
        "Cuota pendiente a gestionar:\n"
        f"Numero cuota: {text_value(pago_pendiente.get('numero_de_cuota'), 'N/A')}\n"
        f"Estado cuota: {text_value(pago_pendiente.get('estado_cuota'), 'N/A')}\n"
        f"Fecha pago: {text_value(pago_pendiente.get('fecha_pago_legible'), 'N/A')}\n"
        f"Valor total: {text_value(pago_pendiente.get('valor_total_legible'), '$0')}\n\n"
        f"{pagos_cuota_pendiente_texto}\n\n"
        "Cuotas reales del credito:\n"
        f"{cuotas_reales_texto}\n\n"
        "Desglose completo de la cuota pendiente:\n"
        f"{cuota_desglose_texto}\n\n"
        "Explicacion de consolidacion:\n"
        f"{explicacion}\n\n"
        "Regla para ISA:\n"
        f"No inventar cuotas. No mencionar cuotas mayores a {text_value(credito.get('max_numero_cuota'), '0')}. "
        "No usar datos de otros creditos. "
        "No confundir amount con valuePaid: amount es el valor registrado en la orden y "
        "valuePaid es el valor aplicado especificamente a la cuota pendiente. "
        "Si pago_prioritario es cuota_inicial, informar primero su valor y que las cuotas siguen inactivas. "
        f"Si el cliente pregunta por cuotas vencidas, usar solo: {cuotas_vencidas_texto}. "
        f"Si pregunta por cuota pendiente, usar solo: {cuotas_pendientes_texto}."
    )
    debug_texto = (
        "Debug pagos-mora\n"
        "Estado API: success\n"
        "Fuente oficial: data.credit.flowPayments + data.credit.installments\n"
        f"Credito ID: {text_value(credito.get('id'), 'N/A')}\n"
        f"Linea: {text_value(credito.get('linea'), 'N/A')}\n"
        f"Total cuotas detectadas: {text_value(resumen.get('total_cuotas'), '0')}\n"
        f"Max numero cuota: {text_value(credito.get('max_numero_cuota'), '0')}\n"
        f"Cuotas pagadas: {text_value(resumen.get('cuotas_pagadas'), '0')}\n"
        f"Cuotas inactivas: {text_value(resumen.get('inactivas_estado_0'), '0')}\n"
        f"Cuotas vencidas: {text_value(resumen.get('vencidos_estado_4'), '0')}\n"
        f"Cuotas pendientes: {text_value(resumen.get('pendientes_estado_3'), '0')}\n"
        f"Dias de atraso: {text_value(resumen.get('dias_de_atraso'), '0')}\n"
        f"Cuota pendiente detectada: {text_value(pago_pendiente.get('numero_de_cuota'), 'N/A')}\n"
        f"Valor cuota pendiente: {text_value(pago_pendiente.get('valor_total_legible'), '$0')}\n"
        f"Pagos registrados cuota pendiente: {text_value(pago_pendiente.get('pagos_registrados_cantidad'), '0')}\n"
        f"Valor pagado registrado cuota pendiente: {importe_pago_legible(pago_pendiente.get('valor_pagado_registrado'))}\n"
        f"Valor pendiente a la fecha: {importe_pago_legible(pago_pendiente.get('valor_pendiente_a_la_fecha'))}\n"
        f"Estado cuota inicial: {text_value(cuota_inicial.get('estado'), 'N/A')}\n"
        f"Valor cuota inicial: {text_value(cuota_inicial.get('amount_legible'), '$0')}\n"
        f"Tipo pago prioritario: {text_value(pago_prioritario.get('tipo'), 'N/A')}\n"
        f"Cuota vencida detectada: {text_value(cuota_vencida.get('numero_de_cuota'), 'N/A')}\n"
        f"Valor cuota vencida: {text_value(cuota_vencida.get('valor_total_legible'), '$0')}\n"
        f"Total pagado: {money(totales.get('total_pagado'))}\n"
        f"Total vencido: {money(totales.get('total_vencido'))}\n"
        f"Total pendiente no vencido: {money(totales.get('total_pendiente_no_vencido'))}\n"
        f"Total pendiente completo: {money(totales.get('total_pendiente'))}\n"
        f"Criterio seleccion credito: {text_value(seleccion_credito.get('criterio'), 'consulta directa por id_credito')}\n"
        f"Creditos elegibles: {text_value(seleccion_credito.get('creditos_elegibles_count'), '0')}\n"
        f"Creditos mismo estado: {text_value(seleccion_credito.get('creditos_mismo_estado_count'), '0')}\n"
        f"Validacion: no existen cuotas mayores a {text_value(credito.get('max_numero_cuota'), '0')}\n\n"
        f"{pagos_cuota_pendiente_texto}"
    )

    fields = {
        "pago_credito_id_texto": text_value(credito.get("id")),
        "pago_credito_linea_texto": text_value(credito.get("linea")),
        "pago_credito_estado_texto": text_value(credito.get("estado_codigo")),
        "pago_credito_total_cuotas_texto": text_value(credito.get("total_cuotas"), "0"),
        "pago_credito_max_numero_cuota_texto": text_value(credito.get("max_numero_cuota"), "0"),
        "pago_credito_validacion_texto": text_value(credito.get("validacion")),
        "pago_creditos_elegibles_count_texto": text_value(seleccion_credito.get("creditos_elegibles_count"), "0"),
        "pago_creditos_elegibles_resumen_texto": creditos_elegibles_texto,
        "pago_creditos_mismo_estado_count_texto": text_value(seleccion_credito.get("creditos_mismo_estado_count"), "0"),
        "pago_creditos_mismo_estado_resumen_texto": creditos_mismo_estado_texto,
        "pago_credito_seleccion_criterio_texto": text_value(seleccion_credito.get("criterio"), "consulta directa por id_credito"),
        "pago_total_cuotas_texto": text_value(resumen.get("total_cuotas"), "0"),
        "pago_cuotas_pagadas_texto": text_value(resumen.get("cuotas_pagadas"), "0"),
        "pago_cuotas_pendientes_total_texto": text_value(resumen.get("cuotas_pendientes_total"), "0"),
        "pago_inactivas_estado_0_texto": text_value(resumen.get("inactivas_estado_0"), "0"),
        "pago_pendientes_estado_3_texto": text_value(resumen.get("pendientes_estado_3"), "0"),
        "pago_vencidos_estado_4_texto": text_value(resumen.get("vencidos_estado_4"), "0"),
        "pago_dias_de_atraso_texto": text_value(resumen.get("dias_de_atraso"), "0"),
        "pago_cuotas_reales_resumen_texto": cuotas_reales_texto,
        "pago_cuotas_pagadas_detalle_texto": cuotas_pagadas_texto,
        "pago_cuotas_vencidas_detalle_texto": cuotas_vencidas_texto,
        "pago_cuotas_pendientes_detalle_texto": cuotas_pendientes_texto,
        "pago_cuota_inicial_estado_texto": text_value(cuota_inicial.get("estado")),
        "pago_cuota_inicial_approved_texto": text_value(cuota_inicial.get("approved"), "False"),
        "pago_cuota_inicial_valor_numero_texto": text_value(cuota_inicial.get("amount_redondeado"), "0"),
        "pago_cuota_inicial_valor_legible_texto": text_value(cuota_inicial.get("amount_legible"), "$0"),
        "pago_cuota_inicial_orden_habilitada_texto": text_value(cuota_inicial.get("orden_habilitada"), "False"),
        "pago_prioritario_tipo_texto": text_value(pago_prioritario.get("tipo")),
        "pago_prioritario_valor_numero_texto": text_value(pago_prioritario.get("valor_total_redondeado"), "0"),
        "pago_prioritario_valor_legible_texto": text_value(pago_prioritario.get("valor_total_legible"), "$0"),
        "pago_prioritario_motivo_texto": text_value(pago_prioritario.get("motivo")),
        "pago_cuota_pendiente_id_texto": text_value(pago_pendiente.get("id")),
        "pago_cuota_pendiente_numero_texto": text_value(pago_pendiente.get("numero_de_cuota")),
        "pago_cuota_pendiente_estado_texto": text_value(pago_pendiente.get("estado_cuota")),
        "pago_cuota_pendiente_fecha_texto": text_value(pago_pendiente.get("fecha_pago")),
        "pago_cuota_pendiente_fecha_legible_texto": text_value(pago_pendiente.get("fecha_pago_legible")),
        "pago_cuota_pendiente_total_numero_texto": text_value(pago_pendiente.get("valor_total_redondeado"), "0"),
        "pago_cuota_pendiente_total_texto": text_value(pago_pendiente.get("valor_total_legible"), "$0"),
        "pago_cuota_pendiente_total_general_texto": text_value(pago_pendiente.get("valor_total_legible"), "$0"),
        "pago_cuota_pendiente_dias_mora_texto": text_value(pago_pendiente.get("dias_de_mora_cuota"), "0"),
        "pago_cuota_pendiente_capital_texto": desglose_completo_money(desglose, corrientes_key, "capital"),
        "pago_cuota_pendiente_intereses_corrientes_texto": desglose_completo_money(desglose, corrientes_key, "intereses_corrientes"),
        "pago_cuota_pendiente_intereses_corrientes_dias_texto": text_value(get_nested(desglose, corrientes_key, "intereses_corrientes", "dias"), "0"),
        "pago_cuota_pendiente_intereses_corrientes_tasa_texto": text_value(get_nested(desglose, corrientes_key, "intereses_corrientes", "tasa"), "N/A"),
        "pago_cuota_pendiente_costos_texto": desglose_completo_money(desglose, corrientes_key, "costos"),
        "pago_cuota_pendiente_impuestos_texto": desglose_completo_money(desglose, corrientes_key, "impuestos"),
        "pago_cuota_pendiente_total_corriente_texto": money(get_nested(desglose, "totales", "total_componentes_corrientes", "valor_redondeado")),
        "pago_cuota_pendiente_mora_heredada_texto": desglose_completo_money(desglose, heredados_key, "mora_heredada"),
        "pago_cuota_pendiente_cargos_cobranza_texto": desglose_completo_money(desglose, heredados_key, "cargos_cobranza"),
        "pago_cuota_pendiente_cargos_cobranza_dias_texto": text_value(get_nested(desglose, heredados_key, "cargos_cobranza", "dias"), "0"),
        "pago_cuota_pendiente_cargos_cobranza_porcentaje_texto": text_value(get_nested(desglose, heredados_key, "cargos_cobranza", "porcentaje"), "N/A"),
        "pago_cuota_pendiente_costos_pendientes_texto": desglose_completo_money(desglose, heredados_key, "costos_pendientes"),
        "pago_cuota_pendiente_impuestos_pendientes_texto": desglose_completo_money(desglose, heredados_key, "impuestos_pendientes"),
        "pago_cuota_pendiente_intereses_mora_texto": desglose_completo_money(desglose, heredados_key, "intereses_mora"),
        "pago_cuota_pendiente_intereses_mora_dias_texto": text_value(get_nested(desglose, heredados_key, "intereses_mora", "dias"), "0"),
        "pago_cuota_pendiente_intereses_mora_tasa_texto": text_value(get_nested(desglose, heredados_key, "intereses_mora", "tasa"), "N/A"),
        "pago_cuota_pendiente_total_heredado_texto": money(get_nested(desglose, "totales", "total_componentes_heredados", "valor_redondeado")),
        "pago_total_pagado_texto": money(totales.get("total_pagado")),
        "pago_total_pendiente_texto": money(totales.get("total_pendiente")),
        "pago_total_vencido_texto": money(totales.get("total_vencido")),
        "pago_total_pendiente_no_vencido_texto": money(totales.get("total_pendiente_no_vencido")),
        "pago_intereses_pendientes_texto": money(totales.get("intereses_pendientes")),
        "pago_costos_pendientes_texto": money(totales.get("costos_pendientes")),
        "pago_impuestos_pendientes_texto": money(totales.get("impuestos_pendientes")),
        "pago_mora_heredada_total_texto": money(totales.get("mora_heredada_total")),
        "pago_intereses_mora_total_texto": money(totales.get("intereses_mora_total")),
        "pago_cargos_cobranza_total_texto": money(totales.get("cargos_cobranza_total")),
        "pago_costos_heredados_total_texto": money(totales.get("costos_heredados_total")),
        "pago_impuestos_heredados_total_texto": money(totales.get("impuestos_heredados_total")),
        "pago_cuota_vencida_numero_texto": text_value(cuota_vencida.get("numero_de_cuota")),
        "pago_cuota_vencida_fecha_legible_texto": text_value(cuota_vencida.get("fecha_pago_legible")),
        "pago_cuota_vencida_total_texto": text_value(cuota_vencida.get("valor_total_legible")),
        "pago_cuota_vencida_estado_texto": text_value(cuota_vencida.get("estado_cuota")),
        "pago_cuota_pendiente_desglose_texto": cuota_desglose_texto,
        "pago_cuota_pendiente_pagos_texto": pagos_cuota_pendiente_texto,
        "pago_cuota_pendiente_explicacion_consolidacion_texto": explicacion,
        "pago_detalle_cuotas_texto": detalle_cuotas_texto,
        "pago_data_contexto_ia": contexto_ia,
        "pago_debug_texto": debug_texto,
        "pago_data_json_texto": json.dumps(convertir_a_json_seguro(data), ensure_ascii=False, default=str),
        "Cuotas_pendientes": text_value(resumen.get("pendientes_estado_3"), "0"),
        "Cuotas_vencidas": text_value(resumen.get("vencidos_estado_4"), "0"),
        "dias_atraso": text_value(resumen.get("dias_de_atraso"), "0"),
        "valor_pagar_legible": text_value(
            pago_prioritario.get("valor_total_legible") or pago_pendiente.get("valor_total_legible"),
            "$0",
        ),
        "fecha_pago": text_value(
            pago_prioritario.get("fecha_pago_legible") or pago_pendiente.get("fecha_pago_legible")
        ),
    }
    fields.update(
        {
            "pago_cuota_principal_numero_texto": fields["pago_cuota_pendiente_numero_texto"],
            "pago_cuota_principal_valor_legible_texto": fields["valor_pagar_legible"],
        }
    )
    return fields


def extraer_credito_desde_respuesta_mora(response_data: Dict[str, Any]) -> Dict[str, Any]:
    data = response_data.get("data") if isinstance(response_data, dict) else {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
        return {}
    data = data if isinstance(data, dict) else {}
    credit = data.get("credit")
    if isinstance(credit, dict):
        return credit
    seleccion = seleccionar_credito_mora_desde_coleccion(response_data)
    credito_seleccionado = seleccion.get("credito_seleccionado_raw")
    if isinstance(credito_seleccionado, dict):
        return credito_seleccionado
    return {}


async def consultar_detalle_receivable_mora(
    id_credito_detalle: str,
    headers: Dict[str, str],
    client_id: str,
) -> Dict[str, Any]:
    try:
        ext_client = await ExternalClient.from_code(
            "KUENTA_RECEIVABLE_GET",
            client_id=client_id,
        )
        ext_client.set_path(f"/{id_credito_detalle}")
        ext_client.set_headers(headers)
        logger.info(
            "Consultando detalle receivable por ID resuelto | id_credito=%s | url_base=%s",
            id_credito_detalle,
            ext_client.url,
        )
        return await ext_client.run()
    except Exception as exc:
        logger.warning(
            "Fallback HTTP directo para detalle receivable | id_credito=%s | error=%s",
            id_credito_detalle,
            str(exc),
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"https://api.kuenta.co/v1/receivable/{id_credito_detalle}",
                headers=headers,
            )
        try:
            data = response.json()
        except ValueError:
            data = {"raw_response": response.text}
        return {
            "status": response.status_code,
            "data": data,
        }


# enpoints cobranzas
# Endpoint para obtener información de pagos en mora de un crédito
@app.post("/pagos-mora", tags=["Cobranzas"], summary="Obtener información de pagos en mora"
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
    consulta_por_cedula = not es_id_credito_kuenta(id_credito)
    params_receivables_mora = construir_params_receivables_mora(id_credito) if consulta_por_cedula else {}
    seleccion_credito_mora: Optional[Dict[str, Any]] = None

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
            if consulta_por_cedula:
                ext_client.set_url(KUENTA_RECEIVABLES_URL)
                ext_client.set_body(params_receivables_mora)
                logger.info(
                    "Consulta de pagos en mora por cedula/documento | busqueda=%s | url=%s | params=%s",
                    id_credito,
                    KUENTA_RECEIVABLES_URL,
                    json.dumps(params_receivables_mora, ensure_ascii=False, default=str),
                )
            else:
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
                if consulta_por_cedula:
                    logger.info(
                        "Fallback HTTP directo receivables por cedula/documento | busqueda=%s | params=%s",
                        id_credito,
                        json.dumps(params_receivables_mora, ensure_ascii=False, default=str),
                    )
                    http_response = await client.get(
                        KUENTA_RECEIVABLES_URL,
                        headers=headers,
                        params=params_receivables_mora,
                    )
                else:
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
            if consulta_por_cedula:
                seleccion_credito_mora = seleccionar_credito_mora_desde_coleccion(response_data)
                credito_lista = seleccion_credito_mora.get("credito_seleccionado_raw") or {}
                if not credito_lista:
                    seleccion_serializada = serializar_seleccion_credito_mora(seleccion_credito_mora)
                    error_msg = f"No se encontro credito elegible para pagos en mora para la busqueda enviada: {id_credito}"
                    logger.error(
                        "%s | seleccion=%s | respuesta=%s",
                        error_msg,
                        json.dumps(convertir_a_json_seguro(seleccion_serializada), ensure_ascii=False, default=str),
                        json.dumps(convertir_a_json_seguro(response_data), ensure_ascii=False, default=str),
                    )
                    return JSONResponse(
                        status_code=404,
                        content={
                            "estado": "error",
                            "codigo_error": "CreditNotFound",
                            "mensaje": "No se encontro credito elegible para pagos en mora",
                            "detalles": error_msg,
                            "consulta_por_cedula": consulta_por_cedula,
                            "parametros_consulta": params_receivables_mora,
                            "seleccion_credito": seleccion_serializada,
                            "status_counts": seleccion_serializada.get("status_counts"),
                            "creditos_recibidos_resumen": seleccion_serializada.get("creditos_recibidos_resumen"),
                        },
                    )

                id_credito_lista = credito_lista.get("ID") or credito_lista.get("id")
                if not id_credito_lista:
                    seleccion_serializada = serializar_seleccion_credito_mora(seleccion_credito_mora)
                    error_msg = f"La consulta de creditos no retorno ID para la busqueda enviada: {id_credito}"
                    logger.error(
                        "%s | credito_lista=%s",
                        error_msg,
                        json.dumps(convertir_a_json_seguro(credito_lista), ensure_ascii=False, default=str),
                    )
                    return JSONResponse(
                        status_code=502,
                        content={
                            "estado": "error",
                            "codigo_error": "CreditIdMissing",
                            "mensaje": "La consulta de creditos no retorno ID del credito",
                            "detalles": error_msg,
                            "consulta_por_cedula": consulta_por_cedula,
                            "parametros_consulta": params_receivables_mora,
                            "seleccion_credito": seleccion_serializada,
                        },
                    )

                logger.info(
                    "Credito seleccionado desde /receivables | busqueda=%s | id_credito=%s | referencia=%s | status=%s | elegibles=%s | mismo_estado=%s",
                    id_credito,
                    id_credito_lista,
                    credito_lista.get("reference"),
                    credito_lista.get("status"),
                    seleccion_credito_mora.get("creditos_elegibles_count"),
                    seleccion_credito_mora.get("creditos_mismo_estado_count"),
                )
                detalle_response = await consultar_detalle_receivable_mora(
                    str(id_credito_lista),
                    headers,
                    client_id,
                )
                detalle_status = detalle_response.get("status", 500)
                detalle_data = detalle_response.get("data", {})
                logger.info(
                    "Respuesta detalle /receivable/%s | status=%s | contiene_credit=%s",
                    id_credito_lista,
                    detalle_status,
                    bool(get_nested(detalle_data, "data", "credit")),
                )
                if detalle_status != 200:
                    seleccion_serializada = serializar_seleccion_credito_mora(seleccion_credito_mora)
                    return JSONResponse(
                        status_code=detalle_status if detalle_status in (400, 401, 403, 404) else 502,
                        content={
                            "estado": "error",
                            "codigo_error": "ReceivableDetailError",
                            "mensaje": "No se pudo obtener el detalle del credito seleccionado",
                            "detalles": f"Kuenta respondio status {detalle_status} al consultar /receivable/{id_credito_lista}",
                            "id_credito_resuelto": id_credito_lista,
                            "seleccion_credito": seleccion_serializada,
                            "respuesta": detalle_data,
                        },
                    )
                response_data = detalle_data
            # logger.info(f"Consulta de pagos en mora exitosa para crédito: {id_credito}")

            # Procesar installments si existen en la respuesta. Soporta:
            # - GET /receivable/{id}: data.credit
            # - GET /receivables?...: data.credits[0]
            processed_data = extraer_credito_desde_respuesta_mora(response_data)
            id_credito_resuelto = (
                processed_data.get("ID")
                or processed_data.get("id")
                or id_credito
            )
            if consulta_por_cedula:
                logger.info(
                    "Credito resuelto desde consulta /receivables | busqueda=%s | id_credito_resuelto=%s | referencia=%s | status=%s",
                    id_credito,
                    id_credito_resuelto,
                    processed_data.get("reference"),
                    processed_data.get("status"),
                )
            if not processed_data:
                seleccion_serializada = serializar_seleccion_credito_mora(seleccion_credito_mora)
                error_msg = f"No se encontro credito en mora para la busqueda enviada: {id_credito}"
                logger.error(
                    "%s | respuesta=%s",
                    error_msg,
                    json.dumps(convertir_a_json_seguro(response_data), ensure_ascii=False, default=str),
                )
                return JSONResponse(
                    status_code=404,
                    content={
                        "estado": "error",
                        "codigo_error": "CreditNotFound",
                        "mensaje": "Credito no encontrado",
                        "detalles": error_msg,
                        "consulta_por_cedula": consulta_por_cedula,
                        "parametros_consulta": params_receivables_mora,
                        "seleccion_credito": seleccion_serializada,
                    },
                )
            # acceder a las cuotas
            installments = processed_data.get("installments", [])
            detalle_cuotas = construir_detalle_cuotas_mora(installments)
            logger.info(
                "DETALLE_CUOTAS_MORA_ACTIVO | id_credito=%s | total_detalle_cuotas=%s",
                id_credito_resuelto,
                len(detalle_cuotas),
            )
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
            return_data = {
                "total_cuotas": len(installments) if isinstance(installments, list) else 0,
                "cuotas_pagadas": 0,
                "cuotas_pendientes_total": 0,
                "dias_de_atraso": resumen.get("debtDays", 0),
                "pendientes_estado_3": 0,
                "vencidos_estado_4": 0,
                "pago_pendiente": None,
                "detalle_cuotas": detalle_cuotas,
            }

            if installments and isinstance(installments, list):
                for installment in installments:
                    logger.debug(
                        f"Procesando installment: {installment.get('number')} con estado {installment.get('status')} para crédito: {id_credito}"
                    )
                    try:
                        status_installment = int(installment.get("status"))
                    except (TypeError, ValueError):
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
                    first_pending_installment = enriquecer_pago_pendiente_con_desglose(
                        first_pending_installment,
                        installments,
                    )
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
                    "detalle_cuotas": detalle_cuotas,
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
            payload_oficial = construir_data_pagos_mora_oficial(
                processed_data,
                id_credito,
                consulta_por_cedula,
            )
            seleccion_credito_respuesta = serializar_seleccion_credito_mora(seleccion_credito_mora)
            if seleccion_credito_respuesta.get("alertas_seleccion"):
                payload_oficial["alertas_integridad"].extend(
                    seleccion_credito_respuesta.get("alertas_seleccion") or []
                )
            payload_oficial["seleccion_credito"] = seleccion_credito_respuesta
            return_data = payload_oficial["data"]
            campos_pago_texto = build_pago_text_fields_oficial(payload_oficial)
            respuesta_final = {
                "estado": "success",
                "mensaje": "Informacion de pagos en mora obtenida correctamente",
                "source_of_truth": payload_oficial["source_of_truth"],
                "alertas_integridad": payload_oficial["alertas_integridad"],
                "seleccion_credito": seleccion_credito_respuesta,
                "data": return_data,
                **campos_pago_texto,
            }
            logger.info(
                "RESPUESTA_PAGOS_MORA_CON_DETALLE_CUOTAS | id_credito=%s | respuesta=%s",
                id_credito,
                json.dumps(
                    convertir_a_json_seguro(respuesta_final),
                    ensure_ascii=False,
                    default=str,
                ),
            )
            return JSONResponse(
                status_code=200,
                content=respuesta_final,
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
# Bitrix24 - Llamadas automatizadas de cartera
# ============================================================

@dataclass
class CriteriosBusquedaBitrix:
    """
    Criterios calculados para buscar el contacto del cliente en Bitrix.

    Solo se usan datos que pertenecen al cliente:
    - CEDULA
    - TELEFONO
    - CORREO

    WHATSAPP_DISPONIBLE no se usa porque pertenece a otra entidad/canal.
    """

    cedula: Optional[str]
    variantes_telefono: List[str]
    correo: Optional[str]
    nombre: Optional[str]
    id_libranza: Optional[str]
    

class ErrorBitrixAPI(Exception):
    """
    Excepción controlada para errores de comunicación o respuesta de Bitrix24.
    """

    def __init__(
        self,
        mensaje: str,
        metodo: Optional[str] = None,
        codigo_estado: Optional[int] = None,
        respuesta: Any = None,
    ):
        self.mensaje = mensaje
        self.metodo = metodo
        self.codigo_estado = codigo_estado
        self.respuesta = respuesta
        super().__init__(mensaje)


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


# ============================================================
# Normalización de textos, llaves y valores
# ============================================================

def normalizar_clave(valor: Any) -> str:
    """
    Normaliza nombres de variables.

    Ejemplos:
    - "contestó" -> "contesto"
    - "Interés Pagar" -> "interes_pagar"
    - "FECHA ACUERDO PAGO" -> "fecha_acuerdo_pago"
    """

    if valor is None:
        return ""

    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[^a-z0-9_]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")

    return texto


def normalizar_texto(valor: Any) -> str:
    """
    Normaliza valores para comparar reglas.

    Ejemplos:
    - "Sí" -> "si"
    - "acuerdo con fecha" -> "acuerdo_con_fecha"
    - True -> "true"
    """

    if valor is None:
        return ""

    if isinstance(valor, bool):
        return "true" if valor else "false"

    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")

    return texto


def limpiar_texto(valor: Any) -> Optional[str]:
    """
    Convierte cualquier valor a string limpio.

    Retorna None si viene vacío o nulo.
    """

    if valor is None:
        return None

    texto = str(valor).strip()
    return texto or None


def convertir_a_json_seguro(valor: Any) -> Any:
    """
    Convierte estructuras con excepciones u objetos no JSON a valores serializables.
    """
    if isinstance(valor, BaseException):
        return str(valor)

    if isinstance(valor, dict):
        return {clave: convertir_a_json_seguro(item) for clave, item in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [convertir_a_json_seguro(item) for item in valor]

    try:
        json.dumps(valor)
        return valor
    except (TypeError, ValueError):
        return str(valor)


def correo_valido_basico(valor: Any) -> bool:
    correo = limpiar_texto(valor)
    if not correo:
        return False

    patron = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
    return re.fullmatch(patron, correo) is not None


def normalizar_correo_o_none(valor: Any) -> Optional[str]:
    correo = limpiar_texto(valor)
    if not correo:
        return None

    correo = correo.lower()
    return correo if correo_valido_basico(correo) else None


def obtener_variable_entrada(
    variables_entrada: Dict[str, Any],
    nombre: str,
    defecto: Any = None,
) -> Any:
    """
    Obtiene una variable desde input_variables ignorando tildes,
    mayúsculas, minúsculas y separadores.
    """

    objetivo = normalizar_clave(nombre)

    for clave, valor in variables_entrada.items():
        if normalizar_clave(clave) == objetivo:
            return valor

    return defecto


VARIABLES_ENTRADA_DEBUG_WEBHOOK = [
    "ORIGEN",
    "OBJETIVO",
    "CEDULA",
    "CEDULA_TITULAR",
    "NOMBRE",
    "NOMBRE_ESTUDIANTE",
    "TELEFONO",
    "CORREO",
    "ID_LIBRANZA",
    "ID_CREDITO",
    "PAGADURIA",
    "UNIVERSIDAD",
    "MORA_TOTAL",
    "VALOR_MORA",
    "VALOR_CONFIRMADO",
]

VARIABLES_SALIDA_DEBUG_WEBHOOK = [
    "contesto",
    "contestó",
    "resultvalidacion",
    "validacion_identidad",
    "objetivo",
    "obj",
    "gestion_final",
    "gestion",
    "opcion_pago",
    "opcpago",
    "requiere_link_pago",
    "linkpago",
    "interes_pagar",
    "intencion_pago",
    "intpago",
    "pago_hoy",
    "pagohoy",
    "fechacuerdopago",
    "fecha_compromiso_pago",
    "fechapago",
    "valor_confirmado",
    "valor_a_pagar",
    "valorpago",
    "valor_pago_parcial",
    "valabono",
    "intencion_abono",
    "intabono",
    "motivo_principal",
    "motivo",
]


def resumir_variables_debug(
    variables: Optional[Dict[str, Any]],
    claves: List[str],
) -> Dict[str, Any]:
    """
    Devuelve solo variables relevantes para depurar sin repetir todo el payload.
    """
    variables = variables or {}
    resumen: Dict[str, Any] = {}

    for clave in claves:
        valor = obtener_variable_entrada(variables, clave)
        if valor is not None:
            resumen[clave] = convertir_a_json_seguro(valor)

    return resumen


def construir_sugerencias_error_webhook(
    tipo_error: str,
    error: Optional[Dict[str, Any]] = None,
) -> List[str]:
    error = error or {}
    campo = str(error.get("campo") or "").strip()
    valor = error.get("valor_recibido")
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    fecha_maxima = hoy + timedelta(days=30)
    sugerencias: List[str] = []

    if campo == "pago.fecha_expiracion":
        sugerencias.append(
            "Enviar la fecha de acuerdo/expiracion en formato DD/MM/YYYY."
        )
        sugerencias.append(
            f"La fecha debe estar entre {hoy.strftime('%d/%m/%Y')} y {fecha_maxima.strftime('%d/%m/%Y')}."
        )
        if valor:
            sugerencias.append(
                f"Valor recibido para fecha de expiracion: {valor}."
            )
    elif campo == "cliente.correo":
        sugerencias.append(
            "Enviar CORREO con formato valido, por ejemplo cliente@dominio.com."
        )
        sugerencias.append(
            "Si Bitrix no tiene correo, el webhook usa input_variables.CORREO como fallback."
        )
    elif campo == "pago.monto":
        sugerencias.append(
            "Enviar valor_confirmado/VALOR_CONFIRMADO numerico; se aceptan separadores como 3000000, 3.000.000 o 3,000,000."
        )
        sugerencias.append(
            "El monto principal debe salir del valor pactado por el cliente, no de MORA_TOTAL."
        )
    elif tipo_error == "contacto_bitrix_no_encontrado":
        sugerencias.append(
            "Verificar CEDULA, TELEFONO o CORREO para que coincidan con un contacto de Bitrix."
        )
    elif tipo_error == "error_bitrix":
        sugerencias.append(
            "Revisar metodo Bitrix, cuerpo enviado y respuesta cruda en el detalle del error."
        )
    elif tipo_error in {"error_proveedor_payvalida", "error_proveedor_pago"}:
        sugerencias.append(
            "Revisar respuesta real del proveedor y si la orden ya existe, esta vencida o fue pagada."
        )
    elif tipo_error == "validacion_payload":
        sugerencias.append(
            "Corregir los campos marcados en errores antes de reenviar el webhook."
        )
    elif tipo_error == "payload_gestion_incompleto":
        sugerencias.append(
            "Verificar que las variables minimas del objetivo esten presentes: identificacion del cliente, resultado de validacion, gestion y datos de pago si el flujo requiere link."
        )
    elif tipo_error == "json_invalido":
        sugerencias.append(
            "Enviar un JSON valido con input_variables y extracted_variables."
        )

    if not sugerencias:
        sugerencias.append(
            "Revisar el bloque debug para identificar etapa, variables clave y contexto del agente."
        )

    return sugerencias


def resumir_contacto_debug(contact: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(contact, dict):
        return None

    return convertir_a_json_seguro(
        {
            "id": contact.get("id") or contact.get("ID"),
            "name": contact.get("name") or contact.get("NAME"),
            "lastName": contact.get("lastName") or contact.get("LAST_NAME"),
            "email": contact.get("email") or contact.get("EMAIL"),
            "phone": contact.get("phone") or contact.get("PHONE"),
        }
    )


def resumir_deal_debug(deal_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(deal_result, dict):
        return None

    item = deal_result.get("item") if isinstance(deal_result.get("item"), dict) else deal_result
    if not isinstance(item, dict):
        return None

    return convertir_a_json_seguro(
        {
            "id": item.get("id") or item.get("ID"),
            "title": item.get("title") or item.get("TITLE"),
            "contactId": item.get("contactId") or item.get("CONTACT_ID"),
            "categoryId": item.get("categoryId") or item.get("CATEGORY_ID"),
            "stageId": item.get("stageId") or item.get("STAGE_ID"),
            "opportunity": item.get("opportunity") or item.get("OPPORTUNITY"),
            "currencyId": item.get("currencyId") or item.get("CURRENCY_ID"),
            "link_pago": item.get("UF_CRM_1779835103174"),
        }
    )


def resumir_orden_pago_debug(
    payment_order_result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(payment_order_result, dict):
        return None

    orden_externa = payment_order_result.get("orden_pago")
    if isinstance(orden_externa, dict):
        orden_externa = {
            "id_orden_pago": orden_externa.get("id_orden_pago"),
            "status_inicial": orden_externa.get("status_inicial"),
            "valor_orden_pago": orden_externa.get("valor_orden_pago"),
            "link_pago": orden_externa.get("link_pago"),
        }

    return convertir_a_json_seguro(
        {
            "id_orden_pago": payment_order_result.get("id_orden_pago"),
            "codigo_orden_interno": payment_order_result.get("codigo_orden_interno"),
            "referencia_externa": payment_order_result.get("referencia_externa"),
            "proveedor": payment_order_result.get("proveedor"),
            "estado": payment_order_result.get("estado"),
            "monto": payment_order_result.get("monto"),
            "moneda": payment_order_result.get("moneda"),
            "enlace_pago": payment_order_result.get("enlace_pago")
            or payment_order_result.get("link_pago"),
            "idempotente": payment_order_result.get("idempotente"),
            "orden_pago": orden_externa,
        }
    )


def resumir_actualizacion_link_debug(
    bitrix_link_update: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(bitrix_link_update, dict):
        return None

    return convertir_a_json_seguro(
        {
            "verificado": bitrix_link_update.get("verificado"),
            "intentos": bitrix_link_update.get("intentos"),
            "id_deal": bitrix_link_update.get("id_deal"),
            "campo_link": bitrix_link_update.get("campo_link"),
            "link_enviado": bitrix_link_update.get("link_enviado"),
            "link_en_bitrix": bitrix_link_update.get("link_en_bitrix"),
            "ultimo_error": bitrix_link_update.get("ultimo_error"),
            "respuesta_update": bitrix_link_update.get("respuesta_update"),
        }
    )


def construir_error_webhook_debug(
    *,
    tipo_error: str,
    mensaje: str,
    status_code: int,
    action: str = "failed_call_completed",
    input_variables: Optional[Dict[str, Any]] = None,
    output_vars: Optional[Dict[str, Any]] = None,
    contexto_agente: Optional[Dict[str, Any]] = None,
    criteria: Any = None,
    validation: Optional[Dict[str, Any]] = None,
    contact: Optional[Dict[str, Any]] = None,
    deal_result: Optional[Dict[str, Any]] = None,
    payment_order_result: Optional[Dict[str, Any]] = None,
    bitrix_link_update: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    criteria_dict = (
        getattr(criteria, "__dict__", None)
        if criteria is not None
        else None
    )
    contact_id = None
    if isinstance(contact, dict):
        contact_id = contact.get("id") or contact.get("ID")

    return convertir_a_json_seguro(
        {
            "ok": False,
            "action": action,
            "tipo_error": tipo_error,
            "mensaje": mensaje,
            "http_status": status_code,
            "validation": validation,
            "error": error,
            "debug": {
                "agent_context": contexto_agente,
                "lookup_criteria": criteria_dict,
                "client_found": contact is not None,
                "client_id": contact_id,
                "client": resumir_contacto_debug(contact),
                "deal_created": deal_result is not None,
                "deal": resumir_deal_debug(deal_result),
                "payment_order_created": payment_order_result is not None,
                "payment_order": resumir_orden_pago_debug(payment_order_result),
                "bitrix_link_update": resumir_actualizacion_link_debug(bitrix_link_update),
                "variables_entrada_clave": resumir_variables_debug(
                    input_variables,
                    VARIABLES_ENTRADA_DEBUG_WEBHOOK,
                ),
                "variables_salida_clave": resumir_variables_debug(
                    output_vars,
                    VARIABLES_SALIDA_DEBUG_WEBHOOK,
                ),
            },
            "sugerencias": construir_sugerencias_error_webhook(tipo_error, error),
        }
    )


def es_si(valor: Any) -> bool:
    """
    Retorna True si el valor representa afirmación.
    """

    return normalizar_texto(valor) in {
        "si",
        "s",
        "yes",
        "y",
        "true",
        "1",
        "afirmativo",
    }


def es_verdadero(valor: Any) -> bool:
    """
    Retorna True si el valor representa booleano verdadero.
    """

    if isinstance(valor, bool):
        return valor

    return normalizar_texto(valor) in {
        "true",
        "si",
        "s",
        "yes",
        "1",
        "contesto",
    }


SALIDA_ALIAS_CANONICOS: Dict[str, List[str]] = {
    # Base comun del agente con limite de 16 caracteres.
    "validaid": ["resultvalidacion", "validacion_identidad"],
    "obj": ["objetivo"],
    "gestion": ["gestion_final"],
    "opcpago": ["opcion_pago"],
    "linkpago": ["requiere_link_pago"],
    "intpago": ["interes_pagar", "intencion_pago"],
    "pagohoy": ["pago_hoy"],
    "fechapago": ["fechacuerdopago", "fecha_compromiso_pago"],
    "valorpago": ["valor_confirmado", "valor_a_pagar"],
    "detalle": ["detalle_acuerdo"],
    "motivo": ["motivo_principal"],
    "seguimiento": ["requseguimiento"],
    "sigcanal": ["canalsiguiente"],
    # One2Credit.
    "intabono": ["intencion_abono"],
    "valabono": ["valor_pago_parcial"],
    "tipogestion": ["tipo_gestion"],
    "alturamora": ["altura_mora"],
    # Variantes frecuentes.
    "resumen": ["resumenllamada"],
    "contesto": ["contesto"],
}


def asignar_variable_salida_normalizada(
    salida: Dict[str, Any],
    nombre: Any,
    valor: Any,
) -> None:
    """
    Inserta una variable de salida y sus alias canonicos.

    Esto permite que el agente use nombres cortos como `opcpago` o `valorpago`
    sin obligar al resto del flujo a conocer esos nombres.
    """

    clave = normalizar_clave(nombre)
    if not clave:
        return

    salida[clave] = valor

    for alias in SALIDA_ALIAS_CANONICOS.get(clave, []):
        salida.setdefault(alias, valor)


def construir_mapa_variables_salida(variables_extraidas: Any) -> Dict[str, Any]:
    """
    Convierte extracted_variables en un diccionario normalizado.

    Entrada:
    [
        {"name": "interes_pagar", "value": "si"},
        {"name": "contestó", "value": true}
    ]

    Salida:
    {
        "interes_pagar": "si",
        "contesto": true
    }
    """

    salida: Dict[str, Any] = {}

    if isinstance(variables_extraidas, list):
        for elemento in variables_extraidas:
            if not isinstance(elemento, dict):
                continue

            nombre = elemento.get("name")
            valor = elemento.get("value")

            if nombre:
                asignar_variable_salida_normalizada(salida, nombre, valor)

        return salida

    if isinstance(variables_extraidas, dict):
        for clave, valor in variables_extraidas.items():
            asignar_variable_salida_normalizada(salida, clave, valor)

        return salida

    return salida


def normalizar_variantes_telefono(valor: Any) -> List[str]:
    """
    Genera variantes del teléfono del cliente.

    Para 3002613153 produce:
    1. 573002613153
    2. 3002613153
    3. +573002613153

    Se conserva este orden porque el formato 573002613153 fue el que
    funcionó correctamente en la prueba real con Bitrix.
    """

    if valor is None:
        return []

    crudo = str(valor).strip()
    digitos = re.sub(r"\D", "", crudo)

    if not digitos:
        return []

    variantes: List[str] = []

    if len(digitos) == 10 and digitos.startswith("3"):
        variantes.append(f"57{digitos}")
        variantes.append(digitos)
        variantes.append(f"+57{digitos}")

    elif len(digitos) == 12 and digitos.startswith("57"):
        variantes.append(digitos)
        variantes.append(digitos[-10:])
        variantes.append(f"+{digitos}")

    else:
        variantes.append(digitos)

        if crudo.startswith("+"):
            variantes.append(crudo)

    variantes_unicas: List[str] = []

    for item in variantes:
        if item not in variantes_unicas:
            variantes_unicas.append(item)

    return variantes_unicas


def construir_criterios_busqueda_bitrix(
    variables_entrada: Dict[str, Any],
) -> CriteriosBusquedaBitrix:
    """
    Construye criterios de búsqueda desde input_variables.

    Usa:
    - CEDULA
    - TELEFONO
    - CORREO

    Ignora:
    - WHATSAPP_DISPONIBLE
    """

    cedula = limpiar_texto(obtener_variable_entrada(variables_entrada, "CEDULA"))
    telefono = obtener_variable_entrada(variables_entrada, "TELEFONO")
    correo = limpiar_texto(obtener_variable_entrada(variables_entrada, "CORREO"))
    nombre = limpiar_texto(obtener_variable_entrada(variables_entrada, "NOMBRE"))
    id_libranza = limpiar_texto(obtener_variable_entrada(variables_entrada, "ID_LIBRANZA"))

    return CriteriosBusquedaBitrix(
        cedula=cedula,
        variantes_telefono=normalizar_variantes_telefono(telefono),
        correo=correo.lower() if correo else None,
        nombre=nombre,
        id_libranza=id_libranza,
    )


def construir_select_busqueda_contacto() -> List[str]:
    """
    Construye el select limpio para búsqueda de contacto.

    Importante:
    No se incluye UF_CRM_1697774324 en el select de búsqueda.
    Ese campo se usa solo en el filter por cédula.
    """

    return CONTACT_LOOKUP_SELECT.copy()


# ============================================================
# Validación de intención positiva de pago
# ============================================================

VALORES_GESTION_POSITIVA = {
    "pago_hoy",
    "promesa_pago",
    "pago_fraccionado",
    "plan_mensual",
    "acuerdo_con_fecha",
    "acuerdo_pago",
    "acuerdo",
}

VALORES_GESTION_NEGATIVA = {
    "no_acuerdo",
    "sin_respuesta",
    "cierre_sin_acuerdo",
    "numero_equivocado",
}

VALORES_VALIDACION_IDENTIDAD_OK = {
    "validado",
    "valido",
    "validada",
}


def evaluar_intencion_pago(variables_salida: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evalúa si la llamada confirma intención positiva de pago.

    Reglas:
    - Debió contestar un humano.
    - La identidad debe estar validada.
    - Debe existir pago_hoy=Si o interes_pagar=Si con acuerdo/promesa/fecha.
    """

    resultado_validacion = normalizar_texto(variables_salida.get("resultvalidacion"))
    gestion_final = normalizar_texto(variables_salida.get("gestion_final"))
    objetivo = normalizar_texto(variables_salida.get("objetivo"))

    interes_pagar = variables_salida.get("interes_pagar")
    pago_hoy = variables_salida.get("pago_hoy")
    fecha_acuerdo_pago = variables_salida.get("fechacuerdopago")
    contesto = variables_salida.get("contesto")

    detalle_acuerdo = normalizar_texto(variables_salida.get("detalle_acuerdo"))
    resumen = normalizar_texto(variables_salida.get("resumen"))
    resumen_llamada = normalizar_texto(variables_salida.get("resumenllamada"))
    obj_detallada = normalizar_texto(variables_salida.get("objdetallada"))
    obs_detallada = normalizar_texto(variables_salida.get("obsjdetallada"))

    bloqueadores: List[str] = []
    advertencias: List[str] = []
    senales_positivas: List[str] = []

    if not es_verdadero(contesto):
        bloqueadores.append("La llamada no fue contestada por un humano o no hubo conversación efectiva.")
    else:
        senales_positivas.append("contesto=true")

    if resultado_validacion not in VALORES_VALIDACION_IDENTIDAD_OK:
        bloqueadores.append(f"Identidad no validada: resultvalidacion={resultado_validacion or 'vacio'}.")
    else:
        senales_positivas.append("resultvalidacion=validado")

    if objetivo and objetivo != "cobro_libranza":
        advertencias.append(f"Objetivo diferente al esperado: objetivo={objetivo}.")

    if gestion_final in VALORES_GESTION_NEGATIVA:
        bloqueadores.append(f"Gestión final negativa: gestion_final={gestion_final}.")

    tiene_gestion_positiva = gestion_final in VALORES_GESTION_POSITIVA
    tiene_fecha_pago = bool(str(fecha_acuerdo_pago or "").strip())

    texto_acuerdo = " ".join(
        [
            detalle_acuerdo,
            resumen,
            resumen_llamada,
            obj_detallada,
            obs_detallada,
        ]
    )

    tiene_texto_acuerdo = (
        "acuerdo" in texto_acuerdo
        and "sin_acuerdo" not in texto_acuerdo
        and "no_acuerdo" not in texto_acuerdo
    )

    if es_si(interes_pagar):
        senales_positivas.append("interes_pagar=Si")

    if es_si(pago_hoy):
        senales_positivas.append("pago_hoy=Si")

    if tiene_gestion_positiva:
        senales_positivas.append(f"gestion_final={gestion_final}")

    if tiene_fecha_pago:
        senales_positivas.append(f"fechacuerdopago={fecha_acuerdo_pago}")

    if tiene_texto_acuerdo:
        senales_positivas.append("texto_contiene_acuerdo")

    intencion_pago = (
        es_si(pago_hoy)
        or (
            es_si(interes_pagar)
            and (
                tiene_gestion_positiva
                or tiene_fecha_pago
                or tiene_texto_acuerdo
            )
        )
    )

    if not intencion_pago:
        bloqueadores.append(
            "No hay intención de pago suficiente. Se requiere pago_hoy=Si "
            "o interes_pagar=Si con acuerdo, promesa o fecha de pago."
        )

    aprobado = len(bloqueadores) == 0

    return {
    # Llaves nuevas en español
    "aprobado": aprobado,
    "intencion_pago": intencion_pago,
    "senales_positivas": senales_positivas,
    "bloqueadores": bloqueadores,
    "advertencias": advertencias,
    "normalizado": {
        "resultvalidacion": resultado_validacion,
        "gestion_final": gestion_final,
        "objetivo": objetivo,
        "interes_pagar": normalizar_texto(interes_pagar),
        "pago_hoy": normalizar_texto(pago_hoy),
        "fechacuerdopago": fecha_acuerdo_pago,
        "contesto": es_verdadero(contesto),
    },

    # Llaves de compatibilidad con el endpoint anterior
    "approved": aprobado,
    "payment_intent": intencion_pago,
    "positive_signals": senales_positivas,
    "blockers": bloqueadores,
    "warnings": advertencias,
    "normalized": {
        "resultvalidacion": resultado_validacion,
        "gestion_final": gestion_final,
        "objetivo": objetivo,
        "interes_pagar": normalizar_texto(interes_pagar),
        "pago_hoy": normalizar_texto(pago_hoy),
        "fechacuerdopago": fecha_acuerdo_pago,
        "contesto": es_verdadero(contesto),
    },
}


def obtener_variable_salida(
    variables_salida: Dict[str, Any],
    *nombres: str,
    defecto: Any = None,
) -> Any:
    """
    Obtiene una variable de salida por nombre normalizado.
    """

    for nombre in nombres:
        clave = normalizar_clave(nombre)
        if clave in variables_salida:
            return variables_salida.get(clave)

    return defecto


def obtener_variable_entrada_o_salida(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    *nombres: str,
    defecto: Any = None,
) -> Any:
    """
    Busca primero en salidas normalizadas y luego en entradas.
    """

    valor_salida = obtener_variable_salida(variables_salida, *nombres, defecto=None)
    if valor_salida is not None:
        return valor_salida

    for nombre in nombres:
        valor_entrada = obtener_variable_entrada(variables_entrada, nombre)
        if valor_entrada is not None:
            return valor_entrada

    return defecto


def interpretar_bool_opcional(valor: Any) -> Optional[bool]:
    """
    Interpreta booleanos declarados por agentes sin forzar defaults.
    """

    if value_is_missing(valor):
        return None

    if isinstance(valor, bool):
        return valor

    normalizado = normalizar_texto(valor)
    if normalizado in {"true", "si", "s", "yes", "y", "1", "afirmativo"}:
        return True
    if normalizado in {"false", "no", "n", "0", "negativo"}:
        return False
    return None


def value_is_missing(valor: Any) -> bool:
    return valor is None or str(valor).strip() == ""


def determinar_requiere_link_pago(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
) -> bool:
    """
    Decide si el evento debe intentar generar/recuperar link de pago.

    Para compatibilidad, si no llega objetivo explicito se conserva el flujo
    historico de cartera: llamada aprobada implica intento de pago Payvalida.
    """

    declarado = obtener_variable_entrada_o_salida(
        variables_entrada,
        variables_salida,
        "requiere_link_pago",
        "generar_link_pago",
        "crear_link_pago",
    )
    bool_declarado = interpretar_bool_opcional(declarado)
    if bool_declarado is not None:
        return bool_declarado

    objetivo = normalizar_texto(
        obtener_variable_entrada_o_salida(
            variables_entrada,
            variables_salida,
            "OBJETIVO",
            "objetivo",
        )
    )

    if objetivo in {
        "registrar_gestion",
        "registrar_gestion_refinanciacion",
        "refinanciacion_final_credito",
        "actualizar_contacto",
        "solo_bitrix",
    }:
        return False

    if any(token in objetivo for token in ("generar_link", "link_pago", "orden_pago", "cobranza", "cobro")):
        return True

    return not bool(objetivo)


def determinar_proveedor_pago(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
) -> str:
    proveedor = limpiar_texto(
        obtener_variable_entrada_o_salida(
            variables_entrada,
            variables_salida,
            "PROVEEDOR_PAGO",
            "proveedor_pago",
            "proveedor",
        )
    )
    if proveedor:
        return normalizar_texto(proveedor)

    origen = normalizar_texto(obtener_variable_entrada(variables_entrada, "ORIGEN"))
    objetivo = normalizar_texto(
        obtener_variable_entrada_o_salida(
            variables_entrada,
            variables_salida,
            "OBJETIVO",
            "objetivo",
        )
    )

    if (
        "one2credit" in origen
        or "one2credit" in objetivo
        or "educativa" in origen
        or "educativo" in origen
        or "educativa" in objetivo
        or "educativo" in objetivo
    ):
        return "api_externa"

    return "payvalida"


def construir_contexto_agente_universal(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
) -> Dict[str, Any]:
    origen = limpiar_texto(obtener_variable_entrada(variables_entrada, "ORIGEN")) or "agente_no_especificado"
    objetivo = limpiar_texto(
        obtener_variable_entrada_o_salida(
            variables_entrada,
            variables_salida,
            "OBJETIVO",
            "objetivo",
        )
    ) or "flujo_legacy_cartera"
    requiere_link_pago = determinar_requiere_link_pago(variables_entrada, variables_salida)
    proveedor_pago = determinar_proveedor_pago(variables_entrada, variables_salida)

    return {
        "origen": origen,
        "objetivo": objetivo,
        "requiere_bitrix": True,
        "requiere_link_pago": requiere_link_pago,
        "proveedor_pago": proveedor_pago,
        "usa_payvalida": requiere_link_pago and proveedor_pago == "payvalida",
    }


def evaluar_gestion_universal(
    variables_salida: Dict[str, Any],
    contexto_agente: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validacion generica para agentes que no necesariamente generan pago.
    """

    contesto = obtener_variable_salida(variables_salida, "contesto", "contestó", "contesto_llamada")
    validacion_identidad = obtener_variable_salida(
        variables_salida,
        "validacion_identidad",
        "resultvalidacion",
        "resultado_validacion",
    )
    gestion_final = normalizar_texto(obtener_variable_salida(variables_salida, "gestion_final"))
    tipo_gestion = normalizar_texto(obtener_variable_salida(variables_salida, "tipo_gestion"))

    bloqueadores: List[str] = []
    advertencias: List[str] = []
    senales_positivas: List[str] = []

    if not es_verdadero(contesto):
        bloqueadores.append("La gestion no fue contestada por el cliente o no hubo conversacion efectiva.")
    else:
        senales_positivas.append("contesto=true")

    identidad_normalizada = normalizar_texto(validacion_identidad)
    if identidad_normalizada not in VALORES_VALIDACION_IDENTIDAD_OK:
        bloqueadores.append(
            f"Identidad no validada: validacion_identidad={identidad_normalizada or 'vacio'}."
        )
    else:
        senales_positivas.append("validacion_identidad=validado")

    if gestion_final:
        senales_positivas.append(f"gestion_final={gestion_final}")
    else:
        advertencias.append("No llego gestion_final; se registrara solo la trazabilidad disponible.")

    aprobado = len(bloqueadores) == 0

    return {
        "aprobado": aprobado,
        "intencion_pago": bool(contexto_agente.get("requiere_link_pago")),
        "senales_positivas": senales_positivas,
        "bloqueadores": bloqueadores,
        "advertencias": advertencias,
        "normalizado": {
            "origen": contexto_agente.get("origen"),
            "objetivo": contexto_agente.get("objetivo"),
            "tipo_gestion": tipo_gestion,
            "gestion_final": gestion_final,
            "validacion_identidad": identidad_normalizada,
            "contesto": es_verdadero(contesto),
            "requiere_link_pago": bool(contexto_agente.get("requiere_link_pago")),
            "proveedor_pago": contexto_agente.get("proveedor_pago"),
        },
        "approved": aprobado,
        "payment_intent": bool(contexto_agente.get("requiere_link_pago")),
        "positive_signals": senales_positivas,
        "blockers": bloqueadores,
        "warnings": advertencias,
        "normalized": {
            "origen": contexto_agente.get("origen"),
            "objetivo": contexto_agente.get("objetivo"),
            "tipo_gestion": tipo_gestion,
            "gestion_final": gestion_final,
            "validacion_identidad": identidad_normalizada,
            "contesto": es_verdadero(contesto),
            "requiere_link_pago": bool(contexto_agente.get("requiere_link_pago")),
            "proveedor_pago": contexto_agente.get("proveedor_pago"),
        },
    }


KUENTA_ONE2CREDIT_ORG_ID = ORG_ID or "beafcbd8-bba7-4303-ad8d-cf33026717b3"
KUENTA_RECEIVABLES_URL = "https://api.kuenta.co/v1/receivables"
ESTADOS_CREDITO_ONE2CREDIT_CONSULTA_MORA = {7, 10, 16}
ESTADOS_CREDITO_ACTIVO_ONE2CREDIT = {
    7: "DESEMBOLSADO",
    10: "MORA",
}


def construir_headers_kuenta_one2credit(token: str) -> Dict[str, str]:
    return {
        "Config-Organization-ID": KUENTA_ONE2CREDIT_ORG_ID,
        "Organization-ID": KUENTA_ONE2CREDIT_ORG_ID,
        "Authorization": token,
    }


def extraer_lista_creditos_one2credit(respuesta: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = respuesta.get("data") if isinstance(respuesta, dict) else None
    if isinstance(data, dict):
        creditos = data.get("credits") or data.get("receivables") or data.get("items")
        if isinstance(creditos, list):
            return [credito for credito in creditos if isinstance(credito, dict)]
    if isinstance(respuesta.get("credits"), list):
        return [credito for credito in respuesta["credits"] if isinstance(credito, dict)]
    return []


def extraer_datos_credito_mora_one2credit(credito: Dict[str, Any]) -> Dict[str, Any]:
    summary = credito.get("summary") if isinstance(credito.get("summary"), dict) else {}
    credit_line = credito.get("creditLine") if isinstance(credito.get("creditLine"), dict) else {}

    return {
        "id_credito_mora": credito.get("ID") or credito.get("id"),
        "consecutivo": credito.get("consecutive"),
        "referencia_credito_mora": credito.get("reference"),
        "parent_id_credito_mora": credito.get("parentID") or credito.get("parentId"),
        "estado_credito_mora": credito.get("status"),
        "saldo_credito_mora": summary.get("balance"),
        "fecha_desembolso_mora": credito.get("disbursedAt"),
        "titulo_de_linea_mora": credit_line.get("title"),
    }


def obtener_estado_credito_one2credit(credito: Dict[str, Any]) -> Optional[int]:
    try:
        return int(credito.get("status"))
    except (TypeError, ValueError):
        return None


def seleccionar_creditos_activos_one2credit(
    creditos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Conserva el orden de Kuenta y filtra los estados de credito activos."""
    return [
        credito
        for credito in creditos
        if obtener_estado_credito_one2credit(credito)
        in ESTADOS_CREDITO_ACTIVO_ONE2CREDIT
    ]


def normalizar_importe_resumen_one2credit(valor: Any) -> Any:
    return valor_original_para_json(valor)


CAMPOS_MONETARIOS_RESUMEN_ONE2CREDIT = {
    "capital_inicial",
    "total_credito",
    "total_pagado",
    "saldo_general",
    "capital_pagado",
    "capital_pendiente",
    "intereses_pendientes",
    "intereses_mora_pendientes",
    "cargos_cobranza_pendientes",
    "deuda_vencida",
    "valor_cuota",
    "valor_pagado",
    "saldo_restante",
    "capital",
    "intereses_corrientes",
    "intereses_adicionales",
    "mora_heredada",
    "intereses_mora",
    "cargos_cobranza",
    "costos",
    "impuestos",
    "monto_registrado",
    "valor_aplicado_cuota",
}


def formatear_importe_resumen_one2credit(valor: Any) -> Optional[str]:
    valor_redondeado = redondear_valor_cuota(valor)
    if valor_redondeado is None:
        return None
    return f"{valor_redondeado:,}".replace(",", ".")


def agregar_formatos_importes_one2credit(valor: Any) -> Any:
    """Agrega campo_format junto a cada importe monetario del resumen."""
    if isinstance(valor, list):
        return [agregar_formatos_importes_one2credit(item) for item in valor]
    if not isinstance(valor, dict):
        return valor

    resultado: Dict[str, Any] = {}
    for campo, contenido in valor.items():
        resultado[campo] = agregar_formatos_importes_one2credit(contenido)
        if campo in CAMPOS_MONETARIOS_RESUMEN_ONE2CREDIT:
            resultado[f"{campo}_format"] = formatear_importe_resumen_one2credit(
                contenido
            )
    return resultado


def calcular_saldo_restante_cuota_one2credit(
    cuota: Dict[str, Any],
) -> Any:
    valor_cuota = decimal_pago_o_cero(cuota.get("payment"))
    valor_pagado = decimal_pago_o_cero(cuota.get("valuePaid"))
    saldo = max(valor_cuota - valor_pagado, Decimal("0"))
    return normalizar_importe_resumen_one2credit(saldo)


def construir_resumen_pago_cuota_one2credit(
    pago: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "fecha_registro": pago.get("registeredAt"),
        "fecha_referencia": pago.get("referencedAt"),
        "id_orden": pago.get("orderId"),
        "monto_registrado": normalizar_importe_resumen_one2credit(
            pago.get("amount")
        ),
        "valor_aplicado_cuota": normalizar_importe_resumen_one2credit(
            pago.get("valuePaid")
        ),
        "desglose_aplicado": {
            "capital": normalizar_importe_resumen_one2credit(
                pago.get("capitalPaid")
            ),
            "intereses_corrientes": normalizar_importe_resumen_one2credit(
                pago.get("interestPaid")
            ),
            "intereses_adicionales": normalizar_importe_resumen_one2credit(
                pago.get("additionalInterestPaid")
            ),
            "intereses_mora": normalizar_importe_resumen_one2credit(
                pago.get("debtInterestPaid")
            ),
            "cargos_cobranza": normalizar_importe_resumen_one2credit(
                pago.get("penaltyPaid")
            ),
            "costos": normalizar_importe_resumen_one2credit(
                pago.get("costsPaid")
            ),
            "impuestos": normalizar_importe_resumen_one2credit(
                pago.get("taxesPaid")
            ),
        },
        "metodo_pago": pago.get("paymentMethod"),
        "metodo_payvalida": pago.get("payvalidaMethod"),
        "es_pago_tardio": bool(pago.get("isLate")),
        "completo_cuota": bool(pago.get("isCompletion")),
    }


def construir_resumen_cuota_one2credit(
    cuota: Dict[str, Any],
) -> Dict[str, Any]:
    estado = mapear_estado_cuota(cuota.get("status"))
    pagos_raw = cuota.get("payments")
    pagos = (
        [pago for pago in pagos_raw if isinstance(pago, dict)]
        if isinstance(pagos_raw, list)
        else []
    )
    resumen = {
        "id": cuota.get("id"),
        "numero": cuota.get("number"),
        "estado": {
            "id": estado.get("estado_codigo"),
            "nombre": estado.get("estado_descripcion"),
        },
        "fecha_vencimiento": cuota.get("date"),
        "fecha_pago_completo": cuota.get("paidAt"),
        "valor_cuota": normalizar_importe_resumen_one2credit(
            cuota.get("payment")
        ),
        "valor_pagado": normalizar_importe_resumen_one2credit(
            cuota.get("valuePaid")
        ),
        "saldo_restante": calcular_saldo_restante_cuota_one2credit(cuota),
        "desglose_cuota": {
            "capital": normalizar_importe_resumen_one2credit(
                cuota.get("capital")
            ),
            "intereses_corrientes": normalizar_importe_resumen_one2credit(
                cuota.get("interest")
            ),
            "intereses_adicionales": normalizar_importe_resumen_one2credit(
                cuota.get("additionalInterest")
            ),
            "mora_heredada": normalizar_importe_resumen_one2credit(
                cuota.get("debtPayment")
            ),
            "intereses_mora": normalizar_importe_resumen_one2credit(
                cuota.get("debtInterest")
            ),
            "cargos_cobranza": normalizar_importe_resumen_one2credit(
                cuota.get("penalty")
            ),
            "costos": normalizar_importe_resumen_one2credit(
                cuota.get("costs")
            ),
            "impuestos": normalizar_importe_resumen_one2credit(
                cuota.get("taxes")
            ),
        },
        "desglose_pagado_acumulado": {
            "capital": normalizar_importe_resumen_one2credit(
                cuota.get("capitalPaid")
            ),
            "intereses_corrientes": normalizar_importe_resumen_one2credit(
                cuota.get("interestPaid")
            ),
            "intereses_adicionales": normalizar_importe_resumen_one2credit(
                cuota.get("additionalInterestPaid")
            ),
            "intereses_mora": normalizar_importe_resumen_one2credit(
                cuota.get("debtInterestPaid")
            ),
            "cargos_cobranza": normalizar_importe_resumen_one2credit(
                cuota.get("penaltyPaid")
            ),
            "costos": normalizar_importe_resumen_one2credit(
                cuota.get("costsPaid")
            ),
            "impuestos": normalizar_importe_resumen_one2credit(
                cuota.get("taxesPaid")
            ),
        },
        "cantidad_pagos_registrados": len(pagos),
    }
    if pagos:
        resumen["pagos_registrados"] = [
            construir_resumen_pago_cuota_one2credit(pago)
            for pago in pagos
        ]
    return resumen


def construir_resumen_credito_activo_one2credit(
    credito: Dict[str, Any],
) -> Dict[str, Any]:
    debtor = credito.get("debtor") if isinstance(credito.get("debtor"), dict) else {}
    debtor_profile = (
        credito.get("debtorProfile")
        if isinstance(credito.get("debtorProfile"), dict)
        else {}
    )
    natural = (
        debtor_profile.get("natural")
        if isinstance(debtor_profile.get("natural"), dict)
        else {}
    )
    credit_line = (
        credito.get("creditLine")
        if isinstance(credito.get("creditLine"), dict)
        else {}
    )
    summary = (
        credito.get("summary")
        if isinstance(credito.get("summary"), dict)
        else {}
    )
    installments_raw = credito.get("installments")
    installments = (
        sorted(
            [cuota for cuota in installments_raw if isinstance(cuota, dict)],
            key=numero_cuota_int,
        )
        if isinstance(installments_raw, list)
        else []
    )
    cuotas_resumidas = [
        construir_resumen_cuota_one2credit(cuota)
        for cuota in installments
    ]
    cuotas_pagadas = [
        cuota for cuota in cuotas_resumidas if get_nested(cuota, "estado", "id") == 1
    ]
    cuotas_pendientes = [
        cuota for cuota in cuotas_resumidas if get_nested(cuota, "estado", "id") == 3
    ]
    cuotas_vencidas = [
        cuota for cuota in cuotas_resumidas if get_nested(cuota, "estado", "id") == 4
    ]
    cuotas_inactivas = [
        cuota for cuota in cuotas_resumidas if get_nested(cuota, "estado", "id") == 0
    ]
    estado_credito = obtener_estado_credito_one2credit(credito)
    primer_nombre = natural.get("firstName")
    apellidos = natural.get("lastName")
    nombre_completo = " ".join(
        str(valor).strip()
        for valor in (primer_nombre, apellidos)
        if valor is not None and str(valor).strip()
    )

    resumen = {
        "cliente": {
            "id_deudor": credito.get("debtorID") or debtor.get("ID"),
            "id_perfil": credito.get("debtorProfileID") or debtor_profile.get("ID"),
            "nombre_completo": nombre_completo,
            "nombres": primer_nombre,
            "apellidos": apellidos,
            "tipo_documento": natural.get("idType"),
            "numero_documento": natural.get("idNumber"),
            "telefono": debtor.get("phone"),
            "correo": natural.get("email") or debtor.get("email"),
        },
        "linea_credito": {
            "id": credito.get("creditLineID") or credit_line.get("ID"),
            "nombre": credit_line.get("title") or credit_line.get("name"),
        },
        "credito": {
            "id": credito.get("ID") or credito.get("id"),
            "referencia": credito.get("reference"),
            "consecutivo": credito.get("consecutive"),
            "estado": {
                "id": estado_credito,
                "nombre": ESTADOS_CREDITO_ACTIVO_ONE2CREDIT.get(estado_credito),
            },
            "capital_inicial": normalizar_importe_resumen_one2credit(
                credito.get("principal")
            ),
            "fecha_creacion": credito.get("createdAt"),
            "fecha_desembolso": credito.get("disbursedAt"),
        },
        "saldo": {
            "total_credito": normalizar_importe_resumen_one2credit(
                summary.get("total")
            ),
            "total_pagado": normalizar_importe_resumen_one2credit(
                summary.get("paid")
            ),
            "saldo_general": normalizar_importe_resumen_one2credit(
                summary.get("balance")
            ),
            "capital_pagado": normalizar_importe_resumen_one2credit(
                summary.get("capitalPaid")
            ),
            "capital_pendiente": normalizar_importe_resumen_one2credit(
                summary.get("capitalBalance")
            ),
            "intereses_pendientes": normalizar_importe_resumen_one2credit(
                summary.get("interestBalance")
            ),
            "intereses_mora_pendientes": normalizar_importe_resumen_one2credit(
                summary.get("debtInterestBalance")
            ),
            "cargos_cobranza_pendientes": normalizar_importe_resumen_one2credit(
                summary.get("penaltyBalance")
            ),
            "deuda_vencida": normalizar_importe_resumen_one2credit(
                summary.get("debt")
            ),
            "dias_mora": summary.get("debtDays"),
        },
        "cuotas": {
            "total": len(cuotas_resumidas),
            "pagadas": len(cuotas_pagadas),
            "pendientes": len(cuotas_pendientes),
            "vencidas": len(cuotas_vencidas),
            "inactivas": len(cuotas_inactivas),
            "cuota_pendiente": cuotas_pendientes[0] if cuotas_pendientes else None,
            "cuotas_vencidas": cuotas_vencidas,
            "detalle": cuotas_resumidas,
        },
    }
    return agregar_formatos_importes_one2credit(resumen)


def construir_credito_activo_one2credit(credito: Dict[str, Any]) -> Dict[str, Any]:
    estado_id = obtener_estado_credito_one2credit(credito)
    return {
        "estado": {
            "id": estado_id,
            "nombre": ESTADOS_CREDITO_ACTIVO_ONE2CREDIT.get(estado_id),
        },
        "datos_credito": extraer_datos_credito_mora_one2credit(credito),
        "resumen_credito": construir_resumen_credito_activo_one2credit(credito),
        "credito": credito,
    }


async def consultar_creditos_one2credit_por_cedula(
    cedula: str,
    estados: Optional[List[int]] = None,
    order: str = "created_at:desc",
    status_counts: bool = False,
) -> Dict[str, Any]:
    method_name = "consultar_creditos_one2credit_por_cedula"
    token = await obtener_token()
    headers = construir_headers_kuenta_one2credit(token)
    estados_consulta = (
        ",".join(str(estado) for estado in estados)
        if estados
        else "0,1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,22"
    )
    params = {
        "offset": 0,
        "limit": 10,
        "include": "summary,installments",
        "status": estados_consulta,
        "q": cedula,
        "order": order,
    }
    if status_counts:
        params["statusCounts"] = "true"

    logger.info(
        "Consultando creditos One2Credit por cedula | cedula=%s | url=%s | params=%s",
        cedula,
        KUENTA_RECEIVABLES_URL,
        json.dumps(params, ensure_ascii=False, default=str),
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(KUENTA_RECEIVABLES_URL, params=params, headers=headers)

    try:
        data = response.json()
    except ValueError:
        data = {"raw_response": response.text}

    logger.info(
        "Respuesta creditos One2Credit | cedula=%s | status=%s | data=%s",
        cedula,
        response.status_code,
        json.dumps(data, ensure_ascii=False, default=str),
    )

    await insertar_log(
        method_name=method_name,
        client_id=cedula,
        error_message=f"Consulta creditos One2Credit status={response.status_code}",
        http_code=response.status_code,
        tipo="info" if response.status_code < 400 else "error",
        payload_enviado=json.dumps(params, ensure_ascii=False, default=str),
        respuesta_api=json.dumps(data, ensure_ascii=False, default=str),
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "tipo_error": "error_consulta_creditos_one2credit",
                "status_code": response.status_code,
                "respuesta": data,
            },
        )

    creditos = extraer_lista_creditos_one2credit(data)
    if not creditos:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "tipo_error": "credito_one2credit_no_encontrado",
                "mensaje": "No se encontraron creditos One2Credit para la cedula enviada.",
                "cedula": cedula,
                "respuesta": data,
            },
        )

    credito = creditos[0]
    datos_credito = extraer_datos_credito_mora_one2credit(credito)
    return {
        "credito": credito,
        "creditos": creditos,
        "datos_credito": datos_credito,
        "respuesta_proveedor": data,
    }


@app.get(
    "/one2credit/creditos-activos",
    tags=["One2Credit"],
    summary="Consultar creditos activos One2Credit por cedula",
)
async def consultar_creditos_activos_one2credit_endpoint(
    cedula: str = Query(
        ...,
        min_length=1,
        max_length=50,
        description="Cedula o documento del titular del credito.",
    ),
):
    cedula_limpia = cedula.strip()
    if not cedula_limpia:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "cedula_requerida_one2credit",
                "mensaje": "La cedula no puede estar vacia.",
            },
        )

    consulta = await consultar_creditos_one2credit_por_cedula(
        cedula_limpia,
        estados=list(ESTADOS_CREDITO_ACTIVO_ONE2CREDIT),
        order="updated_at:desc",
        status_counts=True,
    )
    creditos_consultados = consulta.get("creditos")
    if not isinstance(creditos_consultados, list):
        creditos_consultados = extraer_lista_creditos_one2credit(
            consulta.get("respuesta_proveedor", {})
        )

    creditos_activos = seleccionar_creditos_activos_one2credit(
        creditos_consultados
    )
    resultados = [
        construir_credito_activo_one2credit(credito)
        for credito in creditos_activos
    ]
    total_activos = len(resultados)

    if total_activos == 0:
        tipo_resultado = "sin_creditos_activos"
    elif total_activos == 1:
        tipo_resultado = "credito_activo_unico"
    else:
        tipo_resultado = "multiples_creditos_activos"

    return {
        "ok": True,
        "cedula": cedula_limpia,
        "tipo_resultado": tipo_resultado,
        "estados_considerados_activos": ESTADOS_CREDITO_ACTIVO_ONE2CREDIT,
        "total_creditos_consultados": len(creditos_consultados),
        "total_creditos_activos": total_activos,
        "credito_seleccionado": resultados[0] if total_activos == 1 else None,
        "creditos_activos": resultados,
    }


async def consultar_pagos_mora_interno_one2credit(id_credito: str) -> Dict[str, Any]:
    logger.info("Consultando /pagos-mora para credito One2Credit | id_credito=%s", id_credito)
    response = await obtener_pagos_mora(MoraData(id_credito=id_credito))
    status_code = getattr(response, "status_code", 500)
    try:
        body = json.loads(response.body.decode("utf-8"))
    except Exception:
        body = {"raw_response": getattr(response, "body", b"").decode("utf-8", errors="replace")}

    logger.info(
        "Respuesta /pagos-mora para credito One2Credit | id_credito=%s | status=%s | body=%s",
        id_credito,
        status_code,
        json.dumps(body, ensure_ascii=False, default=str),
    )

    if status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "tipo_error": "error_pagos_mora_one2credit",
                "id_credito": id_credito,
                "status_code": status_code,
                "respuesta": body,
            },
        )

    return body


def extraer_datos_pagos_mora_one2credit(respuesta_pagos_mora: Dict[str, Any]) -> Dict[str, Any]:
    data = respuesta_pagos_mora.get("data") if isinstance(respuesta_pagos_mora, dict) else {}
    data = data if isinstance(data, dict) else {}
    cuota_inicial = data.get("cuota_inicial") if isinstance(data.get("cuota_inicial"), dict) else {}
    pago_prioritario = data.get("pago_prioritario") if isinstance(data.get("pago_prioritario"), dict) else {}
    pago_pendiente = data.get("pago_pendiente") if isinstance(data.get("pago_pendiente"), dict) else {}
    valor_prioritario = pago_prioritario.get("valor_total_legible") or pago_pendiente.get("valor_total_legible")
    fecha_prioritaria = pago_prioritario.get("fecha_pago_legible") or pago_pendiente.get("fecha_pago_legible")
    id_cuota_orden = pago_prioritario.get("id_cuota_orden") or pago_pendiente.get("id")

    return {
        "C_pendiente_mas_Cvencida": data.get("cuotas_pendientes_total"),
        "dias_atraso": data.get("dias_de_atraso"),
        "valor_pagar_legible": valor_prioritario,
        "valor_pagar": pago_prioritario.get("valor_total_redondeado") or pago_pendiente.get("valor_total_redondeado"),
        "fecha_pago": fecha_prioritaria,
        "id_cuota_pendiente": id_cuota_orden,
        "pago_prioritario_tipo": pago_prioritario.get("tipo"),
        "pago_prioritario": pago_prioritario,
        "cuota_inicial": cuota_inicial,
        "orden_habilitada": bool(pago_prioritario.get("orden_habilitada")),
        "key_orden": pago_prioritario.get("key_orden"),
        "cuotas_total": data.get("total_cuotas"),
        "cuota_atraso": pago_pendiente.get("retrasado"),
        "label_cuota_atraso": pago_pendiente.get("label_fecha"),
        "Cuotas_pendientes": data.get("pendientes_estado_3"),
        "Cuotas_vencidas": data.get("vencidos_estado_4"),
    }


def construir_mensaje_pagos_mora_one2credit(datos_mora: Dict[str, Any]) -> str:
    if datos_mora.get("pago_prioritario_tipo") == "cuota_inicial":
        cuota_inicial = datos_mora.get("cuota_inicial") if isinstance(datos_mora.get("cuota_inicial"), dict) else {}
        mensaje = (
            "*CUOTA INICIAL PENDIENTE*\n"
            "----------------------------------------\n"
            f"*VALOR A PAGAR:* {datos_mora.get('valor_pagar_legible') or '$0'}\n"
            "----------------------------------------\n"
            "Las cuotas del credito se encuentran inactivas hasta registrar este pago."
        )
        if not cuota_inicial.get("orden_habilitada"):
            mensaje += "\nNo fue posible habilitar la orden por inconsistencias en la informacion recibida."
        logger.info("Mensaje cuota inicial One2Credit construido | %s", mensaje)
        return mensaje

    cuota_atraso = bool(datos_mora.get("cuota_atraso"))
    cuotas_pendientes = datos_mora.get("Cuotas_pendientes")
    dias_atraso = datos_mora.get("dias_atraso")
    valor_pagar = datos_mora.get("valor_pagar_legible") or "N/A"
    fecha_pago = datos_mora.get("fecha_pago") or "N/A"

    if cuota_atraso:
        mensaje = (
            f"*CUOTAS PENDIENTES:* {cuotas_pendientes}\n"
            "----------------------------------------\n"
            f"*DIAS DE ATRASO:* {dias_atraso}\n"
            "----------------------------------------\n"
            f"*VALOR PENDIENTE A PAGAR:*  {valor_pagar}\n"
            "---------------------------------------\n"
            f"*FECHA DE PAGO VENCIDA:*   {fecha_pago}"
        )
    else:
        mensaje = (
            f"*CUOTAS PENDIENTES:* {cuotas_pendientes}\n"
            "----------------------------------------\n"
            f"*DIAS DE ATRASO:* {dias_atraso}\n"
            "----------------------------------------\n"
            f"*PROXIMO VALOR A PAGAR:*  {valor_pagar}\n"
            "---------------------------------------\n"
            f"*PROXIMA FECHA DE PAGO:*   {fecha_pago}"
        )

    logger.info("Mensaje pagos mora One2Credit construido | %s", mensaje)
    return mensaje


def obtener_opcion_pago_one2credit(variables_salida: Dict[str, Any]) -> Optional[str]:
    valor = obtener_variable_salida(
        variables_salida,
        "opcion_pago",
        "opcion_de_pago",
        "tipo_pago",
        "tipo_de_pago",
        "gestion_pago",
        "modalidad_pago",
        "decision_pago",
    )
    texto = normalizar_texto(valor)
    if not texto:
        return None
    if "pago_total" in texto or texto == "total":
        return "pago_total"
    if "abona" in texto or "abono" in texto or "pago_parcial" in texto:
        return "abono_deuda"
    if "acuerdo" in texto:
        return "acuerdo_pago"
    return texto


def extraer_valor_pago_parcial_one2credit(variables_salida: Dict[str, Any]) -> Optional[int]:
    valor = obtener_variable_salida(
        variables_salida,
        "valor_pago_parcial",
        "valor_abono",
        "valor_a_abonar",
        "monto_abono",
        "monto_parcial",
        "valor_a_pagar",
    )
    return convertir_a_entero_o_none(valor)


def formatear_valor_fecha_payload(valor: Any, fecha: Any = "now") -> Dict[str, Any]:
    valor_procesado = convertir_a_entero_o_none(valor)
    if valor_procesado is None:
        raise ValueError("valor no es numerico o no se pudo interpretar.")

    if fecha is None or str(fecha).strip().lower() == "now":
        fecha_procesada = datetime.now(ZoneInfo("America/Bogota")).strftime("%d/%m/%Y")
    else:
        fecha_procesada = str(fecha).strip()

    return {
        "valor": {
            "original": valor,
            "valor_procesado": valor_procesado,
            "valor_legible": formatear_valor_moneda(valor_procesado),
        },
        "fecha": {
            "original": fecha,
            "valor_procesado": fecha_procesada,
        },
    }


@app.get("/formatear")
async def formatear_valor_fecha(valor: str = Query(...), fecha: str = Query("now")):
    try:
        return formatear_valor_fecha_payload(valor=valor, fecha=fecha)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


async def simular_orden_pago_total_one2credit(
    id_credito_mora: str,
    id_cuota_pendiente: str,
) -> Dict[str, Any]:
    method_name = "simular_orden_pago_total_one2credit"
    token = await obtener_token()
    headers = construir_headers_kuenta_one2credit(token)
    payables_base_url = (
        PAYABLE_URL.rstrip("/")
        if PAYABLE_URL and "payables" in PAYABLE_URL.lower()
        else "https://api.kuenta.co/v1/payables"
    )
    url = (
        f"{payables_base_url}/{id_credito_mora}/installments/"
        f"{id_cuota_pendiente}/orders/simulation"
    )
    params = {
        "method": "payvalida",
        "payvalidaMethod": "undefined",
        "paymentMethod": "payvalida",
    }

    logger.info(
        "Simulando orden pago total One2Credit | url=%s | params=%s",
        url,
        json.dumps(params, ensure_ascii=False, default=str),
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params=params, headers=headers)

    try:
        data = response.json()
    except ValueError:
        data = {"raw_response": response.text}

    await insertar_log(
        method_name=method_name,
        client_id=id_credito_mora,
        error_message=f"Simulacion orden pago total status={response.status_code}",
        http_code=response.status_code,
        tipo="info" if response.status_code < 400 else "error",
        payload_enviado=json.dumps(params, ensure_ascii=False, default=str),
        respuesta_api=json.dumps(data, ensure_ascii=False, default=str),
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "tipo_error": "error_simulacion_pago_total_one2credit",
                "status_code": response.status_code,
                "respuesta": data,
            },
        )

    final_amount = None
    data_block = data.get("data") if isinstance(data, dict) else {}
    if isinstance(data_block, dict):
        payment = data_block.get("payment") if isinstance(data_block.get("payment"), dict) else {}
        final_amount = payment.get("finalAmount")

    monto_final = convertir_a_entero_o_none(final_amount)
    if monto_final is None:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "monto_final_orden_pago_faltante",
                "mensaje": "La simulacion no retorno data.payment.finalAmount valido.",
                "respuesta": data,
            },
        )

    return {
        "monto_final_orden_pago": monto_final,
        "respuesta_simulacion": data,
    }


def extraer_body_json_response(response: Any) -> Dict[str, Any]:
    try:
        body = response.body.decode("utf-8") if isinstance(response.body, (bytes, bytearray)) else response.body
        data = json.loads(body) if body else {}
        return data if isinstance(data, dict) else {"data": data}
    except Exception:
        return {"raw_response": str(getattr(response, "body", ""))}


def extraer_datos_orden_pago_one2credit(respuesta_orden: Dict[str, Any]) -> Dict[str, Any]:
    data = respuesta_orden.get("data") if isinstance(respuesta_orden, dict) else {}
    data = data if isinstance(data, dict) else {}
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    meta = order.get("meta") if isinstance(order.get("meta"), dict) else {}

    return {
        "id_orden_pago": order.get("id"),
        "fecha_creacion_orden": order.get("createdAt"),
        "status_inicial": order.get("status"),
        "valor_orden_pago": order.get("amount"),
        "tipo_pago": order.get("type"),
        "link_pago": meta.get("checkout"),
    }


async def crear_orden_pago_one2credit_con_reintentos(
    id_credito_mora: str,
    id_cuota_pendiente: str,
    amount: int | float,
    max_intentos: int = 3,
    key: Optional[str] = None,
) -> Dict[str, Any]:
    intentos = max(1, min(int(max_intentos or 3), 3))
    ultimo_error = None
    payload = InstallmentOrderCreateRequest(
        amount=amount,
        provider="payvalida",
        collectionCosts=0,
        key=key,
    )

    for intento in range(1, intentos + 1):
        logger.info(
            "Creando orden One2Credit | intento=%s/%s | id_credito=%s | id_cuota=%s | amount=%s | key=%s",
            intento,
            intentos,
            id_credito_mora,
            id_cuota_pendiente,
            amount,
            key,
        )
        response = await create_installment_order(
            id_credito_mora=id_credito_mora,
            id_cuota_pendiente=id_cuota_pendiente,
            payload=payload,
        )
        status_code = getattr(response, "status_code", 500)
        body = extraer_body_json_response(response)

        if status_code < 400:
            datos_orden = extraer_datos_orden_pago_one2credit(body)
            if datos_orden.get("link_pago"):
                return {
                    "respuesta_creacion": body,
                    **datos_orden,
                    "intentos": intento,
                }
            ultimo_error = {
                "status_code": status_code,
                "mensaje": "La orden fue creada pero no retorno data.order.meta.checkout.",
                "respuesta": body,
            }
        else:
            ultimo_error = {
                "status_code": status_code,
                "respuesta": body,
            }

        if intento < intentos:
            await asyncio.sleep(intento)

    raise HTTPException(
        status_code=502,
        detail={
            "ok": False,
            "tipo_error": "orden_one2credit_sin_link",
            "mensaje": "No se pudo crear una orden One2Credit con link de pago despues de reintentos.",
            "ultimo_error": ultimo_error,
        },
    )


def construir_mensaje_link_pago_one2credit(valor_pagar_legible: Any, link_pago: str) -> str:
    mensaje = (
        f"VALOR A PAGAR:\n{valor_pagar_legible or 'N/A'}\n"
        "-----------------------------------------\n"
        f"LINK DE PAGO:\n{link_pago}"
    )
    logger.info("Mensaje link pago One2Credit construido | %s", mensaje)
    return mensaje


async def resolver_orden_pago_one2credit(
    opcion_pago: Optional[str],
    variables_salida: Dict[str, Any],
    datos_credito: Dict[str, Any],
    datos_mora: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    id_credito_mora = limpiar_texto(datos_credito.get("id_credito_mora"))
    id_cuota_pendiente = limpiar_texto(datos_mora.get("id_cuota_pendiente"))
    pago_prioritario_tipo = limpiar_texto(datos_mora.get("pago_prioritario_tipo"))

    if not opcion_pago:
        return None

    if opcion_pago == "acuerdo_pago":
        return {
            "estado": "PENDIENTE_FLUJO_ACUERDO_PAGO",
            "mensaje": "Opcion Acuerdo de Pago tipificada; faltan las peticiones de este flujo.",
        }

    if pago_prioritario_tipo == "cuota_inicial" and opcion_pago in ("pago_total", "abono_deuda"):
        monto_cuota_inicial = convertir_a_entero_o_none(datos_mora.get("valor_pagar"))
        orden_habilitada = bool(datos_mora.get("orden_habilitada"))
        if (
            not id_credito_mora
            or id_cuota_pendiente != "zero"
            or monto_cuota_inicial is None
            or monto_cuota_inicial <= 0
            or not orden_habilitada
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "tipo_error": "datos_orden_cuota_inicial_incompletos",
                    "mensaje": "La cuota inicial esta pendiente, pero sus datos no permiten crear una orden segura.",
                    "datos_credito": datos_credito,
                    "datos_mora": datos_mora,
                },
            )

        orden = await crear_orden_pago_one2credit_con_reintentos(
            id_credito_mora=id_credito_mora,
            id_cuota_pendiente="zero",
            amount=monto_cuota_inicial,
            key="cuota_inicial",
        )
        valor_legible = datos_mora.get("valor_pagar_legible") or formatear_valor_moneda(monto_cuota_inicial)
        return {
            "opcion_pago": "cuota_inicial",
            "opcion_pago_solicitada": opcion_pago,
            "pago_prioritario_tipo": "cuota_inicial",
            "monto_cuota_inicial": monto_cuota_inicial,
            "valor_pagar_legible": valor_legible,
            "mensaje_link_pago": construir_mensaje_link_pago_one2credit(
                valor_pagar_legible=valor_legible,
                link_pago=orden["link_pago"],
            ),
            **orden,
        }

    if not id_credito_mora or not id_cuota_pendiente:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "datos_orden_one2credit_incompletos",
                "mensaje": "Se requiere id_credito_mora e id_cuota_pendiente para crear orden.",
                "datos_credito": datos_credito,
                "datos_mora": datos_mora,
            },
        )

    if opcion_pago == "pago_total":
        simulacion = await simular_orden_pago_total_one2credit(
            id_credito_mora=id_credito_mora,
            id_cuota_pendiente=id_cuota_pendiente,
        )
        monto = simulacion["monto_final_orden_pago"]
        orden = await crear_orden_pago_one2credit_con_reintentos(
            id_credito_mora=id_credito_mora,
            id_cuota_pendiente=id_cuota_pendiente,
            amount=monto,
        )
        valor_legible = datos_mora.get("valor_pagar_legible") or formatear_valor_moneda(monto)
        return {
            "opcion_pago": opcion_pago,
            "monto_final_orden_pago": monto,
            "valor_pagar_legible": valor_legible,
            "mensaje_link_pago": construir_mensaje_link_pago_one2credit(
                valor_pagar_legible=valor_legible,
                link_pago=orden["link_pago"],
            ),
            **orden,
            **simulacion,
        }

    if opcion_pago == "abono_deuda":
        intencion_abono = obtener_variable_salida(
            variables_salida,
            "intencion_abono",
            "interes_abonar",
            "confirma_abono",
            "intencion_pago",
            "interes_pagar",
        )
        if not es_si(intencion_abono) and not es_verdadero(intencion_abono):
            return {
                "estado": "ABONO_SIN_INTENCION_POSITIVA",
                "mensaje": "No se crea orden porque no hay intencion positiva de abono.",
            }

        valor_parcial = extraer_valor_pago_parcial_one2credit(variables_salida)
        if valor_parcial is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "tipo_error": "valor_pago_parcial_requerido",
                    "mensaje": "La opcion Abona Tu deuda requiere valor_pago_parcial o equivalente en variables de salida.",
                },
            )

        formateado = formatear_valor_fecha_payload(valor=valor_parcial, fecha="now")
        valor_procesado = formateado["valor"]["valor_procesado"]
        orden = await crear_orden_pago_one2credit_con_reintentos(
            id_credito_mora=id_credito_mora,
            id_cuota_pendiente=id_cuota_pendiente,
            amount=valor_procesado,
        )
        valor_legible = formateado["valor"]["valor_legible"]
        return {
            "opcion_pago": opcion_pago,
            "valor_parcial_original": formateado["valor"]["original"],
            "fecha_peticion_formateada": formateado["fecha"]["valor_procesado"],
            "valor_parcial_procesado": valor_procesado,
            "valor_pagar_legible": valor_legible,
            "mensaje_link_pago": construir_mensaje_link_pago_one2credit(
                valor_pagar_legible=valor_legible,
                link_pago=orden["link_pago"],
            ),
            **orden,
        }

    return {
        "estado": "OPCION_PAGO_NO_SOPORTADA",
        "opcion_pago": opcion_pago,
    }


async def procesar_preconsulta_pago_one2credit_desde_evento(
    variables_entrada: Dict[str, Any],
    variables_salida: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    variables_salida = variables_salida or {}
    cedula = limpiar_texto(
        obtener_variable_entrada(variables_entrada, "CEDULA")
        or obtener_variable_entrada(variables_entrada, "CEDULA_TITULAR")
    )
    if not cedula:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "cedula_requerida_one2credit",
                "mensaje": "CEDULA o CEDULA_TITULAR es obligatoria para consultar creditos One2Credit.",
            },
        )

    consulta_creditos = await consultar_creditos_one2credit_por_cedula(cedula)
    datos_credito = consulta_creditos["datos_credito"]
    estado_credito = datos_credito.get("estado_credito_mora")
    try:
        estado_credito_int = int(estado_credito)
    except (TypeError, ValueError):
        estado_credito_int = None

    resultado = {
        "proveedor": "one2credit_kuenta",
        "etapa": "consulta_credito",
        "cedula": cedula,
        "datos_credito": datos_credito,
        "estado_valido_para_pagos_mora": estado_credito_int in ESTADOS_CREDITO_ONE2CREDIT_CONSULTA_MORA,
    }

    if estado_credito_int not in ESTADOS_CREDITO_ONE2CREDIT_CONSULTA_MORA:
        resultado.update(
            {
                "estado": "NO_APLICA_PAGOS_MORA",
                "mensaje": (
                    "El credito One2Credit no esta en estado 10, 7 o 16; "
                    "no se consulta /pagos-mora en esta etapa."
                ),
            }
        )
        logger.info(
            "Credito One2Credit no aplica para /pagos-mora | %s",
            json.dumps(resultado, ensure_ascii=False, default=str),
        )
        return resultado

    id_credito_mora = limpiar_texto(datos_credito.get("id_credito_mora"))
    if not id_credito_mora:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "id_credito_mora_faltante",
                "mensaje": "La consulta de creditos no retorno id_credito_mora.",
                "datos_credito": datos_credito,
            },
        )

    respuesta_pagos_mora = await consultar_pagos_mora_interno_one2credit(id_credito_mora)
    datos_mora = extraer_datos_pagos_mora_one2credit(respuesta_pagos_mora)
    mensaje = construir_mensaje_pagos_mora_one2credit(datos_mora)
    opcion_pago = obtener_opcion_pago_one2credit(variables_salida)
    orden_pago = await resolver_orden_pago_one2credit(
        opcion_pago=opcion_pago,
        variables_salida=variables_salida,
        datos_credito=datos_credito,
        datos_mora=datos_mora,
    )

    mensaje_final = (
        orden_pago.get("mensaje_link_pago")
        if isinstance(orden_pago, dict) and orden_pago.get("mensaje_link_pago")
        else mensaje
    )

    resultado.update(
        {
            "etapa": "pagos_mora_consultado",
            "estado": "ORDEN_PAGO_GENERADA" if isinstance(orden_pago, dict) and orden_pago.get("link_pago") else "PAGOS_MORA_CONSULTADO",
            "opcion_pago": opcion_pago,
            "datos_mora": datos_mora,
            "mensaje_gestion": mensaje_final,
            "mensaje_pagos_mora": mensaje,
            "orden_pago": orden_pago,
            "respuesta_pagos_mora": respuesta_pagos_mora,
        }
    )
    logger.info(
        "Preconsulta pagos One2Credit completada | %s",
        json.dumps(resultado, ensure_ascii=False, default=str),
    )
    return resultado


# ============================================================
# Cliente Bitrix24 con reintentos
# ============================================================

async def llamar_bitrix(
    metodo: str,
    cuerpo: Optional[Dict[str, Any]] = None,
    max_reintentos: int = 3,
    espera_segundos: float = 1.0,
) -> Dict[str, Any]:
    """
    Ejecuta POST contra un método REST de Bitrix24 con manejo de errores.

    Reintenta:
    - timeout
    - error de conexión
    - HTTP 429
    - HTTP 5xx
    - errores de API reintentables como QUERY_LIMIT_EXCEEDED

    No reintenta:
    - HTTP 200 sin resultados
    - HTTP 200 con item sin id
    - HTTP 4xx funcional distinto de 429
    """

    cuerpo = cuerpo or {}

    if not BITRIX_BASE_URL:
        raise ErrorBitrixAPI(
            mensaje="BITRIX_BASE_URL no está configurado.",
            metodo=metodo,
        )

    url = f"{BITRIX_BASE_URL}/{metodo}.json"
    ultimo_error: Optional[Exception] = None

    errores_api_reintentables = {
        "QUERY_LIMIT_EXCEEDED",
        "TOO_MANY_REQUESTS",
        "OPERATION_TIME_LIMIT",
    }

    for intento in range(1, max_reintentos + 1):
        logger.info(
            "Bitrix request iniciado | metodo=%s | intento=%s/%s | cuerpo=%s",
            metodo,
            intento,
            max_reintentos,
            json.dumps(cuerpo, ensure_ascii=False, default=str),
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                respuesta_http = await client.post(url, json=cuerpo)

            try:
                datos = respuesta_http.json()
            except ValueError as exc:
                logger.error(
                    "Bitrix respondió contenido no JSON | metodo=%s | estado=%s | texto=%s",
                    metodo,
                    respuesta_http.status_code,
                    respuesta_http.text[:1000],
                )
                raise ErrorBitrixAPI(
                    mensaje="Bitrix24 respondió contenido no JSON.",
                    metodo=metodo,
                    codigo_estado=respuesta_http.status_code,
                    respuesta=respuesta_http.text[:1000],
                ) from exc

            if respuesta_http.status_code == 200:
                if "error" in datos:
                    codigo_error = str(datos.get("error", "")).upper()

                    if codigo_error in errores_api_reintentables and intento < max_reintentos:
                        logger.warning(
                            "Bitrix respondió error de API reintentable | metodo=%s | error=%s | intento=%s/%s",
                            metodo,
                            codigo_error,
                            intento,
                            max_reintentos,
                        )
                        await asyncio.sleep(espera_segundos * intento)
                        continue

                    logger.error(
                        "Bitrix respondió error de API | metodo=%s | error=%s | descripcion=%s",
                        metodo,
                        datos.get("error"),
                        datos.get("error_description"),
                    )
                    raise ErrorBitrixAPI(
                        mensaje=datos.get("error_description") or datos.get("error") or "Error Bitrix24.",
                        metodo=metodo,
                        codigo_estado=respuesta_http.status_code,
                        respuesta=datos,
                    )

                logger.info(
                    "Bitrix request exitoso | metodo=%s | intento=%s | estado=200",
                    metodo,
                    intento,
                )
                return datos

            if respuesta_http.status_code == 429 or respuesta_http.status_code >= 500:
                logger.warning(
                    "Bitrix respondió error reintentable | metodo=%s | estado=%s | intento=%s/%s",
                    metodo,
                    respuesta_http.status_code,
                    intento,
                    max_reintentos,
                )

                ultimo_error = ErrorBitrixAPI(
                    mensaje="Bitrix24 respondió error reintentable.",
                    metodo=metodo,
                    codigo_estado=respuesta_http.status_code,
                    respuesta=datos,
                )

                if intento < max_reintentos:
                    await asyncio.sleep(espera_segundos * intento)
                    continue

                raise ultimo_error

            logger.error(
                "Bitrix respondió error HTTP no reintentable | metodo=%s | estado=%s | respuesta=%s",
                metodo,
                respuesta_http.status_code,
                json.dumps(datos, ensure_ascii=False, default=str),
            )

            raise ErrorBitrixAPI(
                mensaje="Bitrix24 respondió error HTTP no reintentable.",
                metodo=metodo,
                codigo_estado=respuesta_http.status_code,
                respuesta=datos,
            )

        except httpx.TimeoutException as exc:
            ultimo_error = exc

            logger.warning(
                "Timeout llamando Bitrix | metodo=%s | intento=%s/%s | error=%s",
                metodo,
                intento,
                max_reintentos,
                str(exc),
            )

            if intento < max_reintentos:
                await asyncio.sleep(espera_segundos * intento)
                continue

            raise ErrorBitrixAPI(
                mensaje="Timeout al llamar Bitrix24.",
                metodo=metodo,
            ) from exc

        except httpx.RequestError as exc:
            ultimo_error = exc

            logger.warning(
                "Error de conexión llamando Bitrix | metodo=%s | intento=%s/%s | error=%s",
                metodo,
                intento,
                max_reintentos,
                str(exc),
            )

            if intento < max_reintentos:
                await asyncio.sleep(espera_segundos * intento)
                continue

            raise ErrorBitrixAPI(
                mensaje="Error de conexión al llamar Bitrix24.",
                metodo=metodo,
            ) from exc

    raise ErrorBitrixAPI(
        mensaje=f"No fue posible completar la llamada a Bitrix24. Último error: {ultimo_error}",
        metodo=metodo,
    )



def extraer_items_bitrix(respuesta_bitrix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extrae items desde respuestas de crm.item.list.
    """

    resultado = respuesta_bitrix.get("result") or {}

    if isinstance(resultado, dict) and isinstance(resultado.get("items"), list):
        return resultado["items"]

    if isinstance(resultado, list):
        return resultado

    return []


def contacto_bitrix_valido(contacto: Optional[Dict[str, Any]]) -> bool:
    """
    Valida que el contacto retornado por Bitrix sea utilizable.

    Para crear el deal se necesita:
    contactIds: [id_contacto]
    """

    if not contacto:
        return False

    id_contacto = contacto.get("id") or contacto.get("ID")

    if id_contacto is None:
        logger.warning(
            "Bitrix retornó un contacto sin id. Se tratará como inválido | contacto=%s",
            json.dumps(contacto, ensure_ascii=False, default=str),
        )
        return False

    try:
        int(id_contacto)
        return True
    except (TypeError, ValueError):
        logger.warning(
            "Bitrix retornó un id de contacto inválido | id=%s | contacto=%s",
            id_contacto,
            json.dumps(contacto, ensure_ascii=False, default=str),
        )
        return False


# ============================================================
# Búsqueda de contacto en Bitrix
# ============================================================

async def buscar_contacto_por_telefono(
    variantes_telefono: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por teléfono probando una variante por petición.

    Orden típico:
    1. 573002613153
    2. 3002613153
    3. +573002613153

    El ciclo termina apenas Bitrix retorna un contacto con id válido.
    """

    if not variantes_telefono:
        logger.info("No hay teléfono del cliente para consultar.")
        return None

    logger.info("Buscando contacto por teléfono | variantes=%s", variantes_telefono)

    for telefono in variantes_telefono:
        valor_telefono: Any = telefono

        # Replica la petición manual que funcionó:
        # "filter": {"@phone": 573002613153}
        if str(telefono).isdigit():
            valor_telefono = int(telefono)

        cuerpo = {
            "entityTypeId": CONTACT_ENTITY_TYPE_ID,
            "select": construir_select_busqueda_contacto(),
            "filter": {
                "@phone": valor_telefono,
            },
        }

        logger.info(
            "Intentando búsqueda por variante de teléfono | telefono=%s | cuerpo=%s",
            telefono,
            json.dumps(cuerpo, ensure_ascii=False, default=str),
        )

        datos = await llamar_bitrix("crm.item.list", cuerpo)
        items = extraer_items_bitrix(datos)

        logger.info(
            "Respuesta Bitrix por variante | telefono=%s | total_items=%s | items=%s",
            telefono,
            len(items),
            json.dumps(items, ensure_ascii=False, default=str),
        )

        if not items:
            logger.info("Sin contacto para variante | telefono=%s", telefono)
            continue

        primer_item = items[0]

        if contacto_bitrix_valido(primer_item):
            logger.info(
                "Contacto encontrado por teléfono | telefono=%s | id=%s",
                telefono,
                primer_item.get("id") or primer_item.get("ID"),
            )
            return primer_item

        logger.warning(
            "Bitrix retornó item sin id para variante | telefono=%s | item=%s",
            telefono,
            json.dumps(primer_item, ensure_ascii=False, default=str),
        )

    logger.info("No se encontró contacto válido por ninguna variante de teléfono.")
    return None


async def buscar_contacto_por_cedula(cedula: str) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por cédula/documento de identidad.

    Usa UF_CRM_1697774324 solo como filtro.
    No lo incluye en select para evitar respuestas sin id.
    """

    cuerpo = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "select": construir_select_busqueda_contacto(),
        "filter": {
            BITRIX_CONTACT_CEDULA_FIELD: cedula,
        },
    }

    logger.info(
        "Buscando contacto por cédula | cedula=%s | campo=%s | cuerpo=%s",
        cedula,
        BITRIX_CONTACT_CEDULA_FIELD,
        json.dumps(cuerpo, ensure_ascii=False, default=str),
    )

    datos = await llamar_bitrix("crm.item.list", cuerpo)
    items = extraer_items_bitrix(datos)

    logger.info(
        "Respuesta búsqueda por cédula | items=%s",
        json.dumps(items, ensure_ascii=False, default=str),
    )

    if not items:
        logger.info("Bitrix respondió 200 pero no encontró contacto por cédula.")
        return None

    primer_item = items[0]

    if not contacto_bitrix_valido(primer_item):
        logger.warning(
            "Bitrix encontró item por cédula pero sin id válido | item=%s",
            json.dumps(primer_item, ensure_ascii=False, default=str),
        )
        return None

    logger.info("Contacto encontrado por cédula | id=%s", primer_item.get("id"))
    return primer_item

async def buscar_contacto_por_correo(correo: str) -> Optional[Dict[str, Any]]:
    """
    Busca contacto por correo electrónico.
    """

    cuerpo = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "select": construir_select_busqueda_contacto(),
        "filter": {
            "email": correo,
        },
    }

    logger.info(
        "Buscando contacto por correo | correo=%s | cuerpo=%s",
        correo,
        json.dumps(cuerpo, ensure_ascii=False, default=str),
    )

    datos = await llamar_bitrix("crm.item.list", cuerpo)
    items = extraer_items_bitrix(datos)

    logger.info(
        "Respuesta búsqueda por correo | items=%s",
        json.dumps(items, ensure_ascii=False, default=str),
    )

    if not items:
        logger.info("Bitrix respondió 200 pero no encontró contacto por correo.")
        return None

    primer_item = items[0]

    if not contacto_bitrix_valido(primer_item):
        logger.warning(
            "Bitrix encontró item por correo pero sin id válido | item=%s",
            json.dumps(primer_item, ensure_ascii=False, default=str),
        )
        return None

    logger.info("Contacto encontrado por correo | id=%s", primer_item.get("id"))
    return primer_item


async def buscar_contacto_desde_variables_entrada(
    variables_entrada: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Orquesta la búsqueda del contacto en Bitrix.

    Orden:
    1. Teléfono, probando variante por variante.
    2. Cédula.
    3. Correo.

    Si encuentra un contacto con id, retorna inmediatamente.
    """

    criterios = construir_criterios_busqueda_bitrix(variables_entrada)

    logger.info(
        "Criterios de búsqueda construidos | %s",
        json.dumps(criterios.__dict__, ensure_ascii=False, default=str),
    )

    if criterios.variantes_telefono:
        contacto = await buscar_contacto_por_telefono(criterios.variantes_telefono)

        if contacto_bitrix_valido(contacto):
            return contacto

        logger.info("No se encontró por teléfono. Se intentará por cédula.")

    if criterios.cedula:
        contacto = await buscar_contacto_por_cedula(criterios.cedula)

        if contacto_bitrix_valido(contacto):
            return contacto

        logger.info("No se encontró por cédula. Se intentará por correo.")

    if criterios.correo:
        contacto = await buscar_contacto_por_correo(criterios.correo)

        if contacto_bitrix_valido(contacto):
            return contacto

    logger.warning(
        "No se encontró contacto válido con teléfono, cédula ni correo | criterios=%s",
        json.dumps(criterios.__dict__, ensure_ascii=False, default=str),
    )

    return None


# ============================================================
# Mapeo y creación de deal en RESULTADO LLAMADA IA
# ============================================================

def convertir_a_entero_o_none(valor: Any) -> Optional[int]:
    """
    Convierte valores numéricos a int.

    Soporta:
    - 2445083
    - "2,445,083"
    - "$2.445.083"
    """

    if valor is None:
        return None

    texto = str(valor).strip()

    if not texto:
        return None

    texto = re.sub(r"[^\d,.\-]", "", texto)

    if not texto:
        return None

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes[-1]) <= 2 else texto.replace(",", "")
    elif "." in texto:
        partes = texto.split(".")
        if len(partes) > 2:
            texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) <= 2 else "".join(partes)
        elif len(partes[-1]) > 2:
            texto = texto.replace(".", "")

    try:
        return int(Decimal(texto).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def construir_comentarios_trazabilidad_gestion(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
) -> Optional[str]:
    """
    Guarda trazabilidad de campos especificos de producto sin mezclarlos con
    campos legacy de libranza.
    """

    campos_entrada = [
        "ORIGEN",
        "OBJETIVO",
        "ID_CREDITO",
        "ID_LIBRANZA",
        "UNIVERSIDAD",
        "PAGADURIA",
        "NOMBRE_LINEA_CREDITO",
        "ESTADO_CREDITO",
        "VALOR_MORA",
        "MORA_TOTAL",
        "VALOR_CONFIRMADO",
        "VALOR_FINANCIADO",
        "VALOR_DESEMBOLSADO",
        "CUOTAS",
        "DIAS_ATRASO",
        "FECHA_SOLICITUD",
        "FECHA_DESEMBOLSO",
        "NOMBRE_COTITULAR",
        "IDENTIFICACION_COTITULAR",
    ]
    campos_salida = [
        "tipo_gestion",
        "altura_mora",
        "gestion_final",
        "requiere_link_pago",
        "proveedor_pago",
        "interes_refinanciar",
        "cuotas_restantes",
        "valor_a_pagar",
        "fecha_compromiso_pago",
        "resumenllamada",
        "resumen_llamada",
        "detalle_acuerdo",
    ]

    lineas: List[str] = []

    for campo in campos_entrada:
        valor = obtener_variable_entrada(variables_entrada, campo)
        if valor is not None and str(valor).strip() != "":
            lineas.append(f"{campo}: {valor}")

    for campo in campos_salida:
        valor = variables_salida.get(normalizar_clave(campo))
        if valor is not None and str(valor).strip() != "":
            lineas.append(f"{campo}: {valor}")

    if not lineas:
        return None

    return "\n".join(lineas)[:4000]


def construir_campos_resultado_llamada_ia(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    id_contacto: int,
    enlace_pago: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construye los fields para crm.item.add en la etapa RESULTADO LLAMADA IA.

    Embudo:
    - HOR CARTERA -> categoryId = 0

    Etapa:
    - RESULTADO LLAMADA IA -> stageId = UC_L3QC2Y
    """

    pagaduria = limpiar_texto(obtener_variable_entrada(variables_entrada, "PAGADURIA"))
    cuotas_en_mora = limpiar_texto(obtener_variable_entrada(variables_entrada, "CUOTAS_EN_MORA"))

    valor_total_mora = convertir_a_entero_o_none(
        obtener_variable_entrada(variables_entrada, "VALOR_TOTAL_MORA")
        or obtener_variable_entrada(variables_entrada, "MORA_TOTAL")
        or obtener_variable_entrada(variables_entrada, "MORA")
    )

    valor_confirmado = convertir_a_entero_o_none(
        variables_salida.get("valor_confirmado")
        or variables_salida.get("valorconfirmado")
        or variables_salida.get("valor_a_pagar")
        or variables_salida.get("monto_acordado")
        or variables_salida.get("monto_pactado")
        or obtener_variable_entrada(variables_entrada, "VALOR_CONFIRMADO")
    )
    valor_confirmado_legible = limpiar_texto(
        variables_salida.get("valor_confirmado_legible")
        or variables_salida.get("valor_confirmado_original")
        or variables_salida.get("valor_confirmado")
        or variables_salida.get("valor_a_pagar")
        or obtener_variable_entrada(variables_entrada, "VALOR_CONFIRMADO")
    )
    valor_confirmado_dinero_bitrix = (
        f"{valor_confirmado}|COP"
        if valor_confirmado is not None
        else None
    )

    resumen_llamada = limpiar_texto(
        variables_salida.get("resumenllamada")
        or variables_salida.get("resumen_llamada")
        or variables_salida.get("resumen_gestion")
    )
    detalle_acuerdo = limpiar_texto(variables_salida.get("detalle_acuerdo"))
    gestion_final = limpiar_texto(variables_salida.get("gestion_final"))
    motivo_principal = limpiar_texto(
        variables_salida.get("motivo_principal")
        or variables_salida.get("tipo_gestion")
        or variables_salida.get("altura_mora")
    )
    interes_pagar = limpiar_texto(
        variables_salida.get("interes_pagar")
        or variables_salida.get("intencion_pago")
        or variables_salida.get("interes_refinanciar")
    )
    pago_hoy = limpiar_texto(variables_salida.get("pago_hoy"))
    fecha_acuerdo_pago = limpiar_texto(
        variables_salida.get("fechacuerdopago")
        or variables_salida.get("fecha_compromiso_pago")
        or variables_salida.get("fecha_limite_pago")
        or variables_salida.get("fecha_acuerdo_pago")
    )

    id_libranza = limpiar_texto(obtener_variable_entrada(variables_entrada, "ID_LIBRANZA"))
    telefono_cliente = limpiar_texto(obtener_variable_entrada(variables_entrada, "TELEFONO"))
    cedula_cliente = limpiar_texto(obtener_variable_entrada(variables_entrada, "CEDULA"))
    nombre_cliente = limpiar_texto(
        obtener_variable_entrada(variables_entrada, "NOMBRE")
        or obtener_variable_entrada(variables_entrada, "NOMBRE_ESTUDIANTE")
    ) or "Cliente"
    comentarios_trazabilidad = construir_comentarios_trazabilidad_gestion(
        variables_entrada=variables_entrada,
        variables_salida=variables_salida,
    )
    if valor_confirmado_legible:
        linea_valor = f"VALOR_CONFIRMADO_LEGIBLE: {valor_confirmado_legible}"
        comentarios_trazabilidad = (
            f"{comentarios_trazabilidad}\n{linea_valor}"
            if comentarios_trazabilidad
            else linea_valor
        )

    titulo = f"Resultado llamada IA - {nombre_cliente}"

    if cedula_cliente:
        titulo += f" - CC {cedula_cliente}"

    campos: Dict[str, Any] = {
        "title": titulo,
        "categoryId": BITRIX_DEAL_CATEGORY_ID,
        "stageId": BITRIX_DEAL_STAGE_ID,
        "comments": comentarios_trazabilidad,

        # Relación con el contacto encontrado.
        "contactId": id_contacto,
        "contactIds": [id_contacto],

        # Campos personalizados solicitados.
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
        "UF_CRM_1779835103174": limpiar_texto(enlace_pago),
        "UF_CRM_1780334065381": valor_confirmado_dinero_bitrix,
    }

    if BITRIX_ASSIGNED_BY_ID:
        campos["assignedById"] = BITRIX_ASSIGNED_BY_ID

    if valor_total_mora is not None:
        campos["opportunity"] = valor_total_mora
        campos["currencyId"] = "COP"

    return campos


def convertir_error_bitrix_a_http(exc: ErrorBitrixAPI) -> HTTPException:
    """
    Convierte ErrorBitrixAPI en HTTPException para responder al cliente.
    """

    return HTTPException(
        status_code=502,
        detail={
            "ok": False,
            "tipo_error": "error_api_bitrix",
            "mensaje": exc.mensaje,
            "metodo_bitrix": exc.metodo,
            "codigo_estado_bitrix": exc.codigo_estado,
            "respuesta_bitrix": exc.respuesta,
        },
    )



async def crear_deal_resultado_llamada_ia(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    contacto: Dict[str, Any],
    codigo_proceso: str,
    destino_bitrix: Dict[str, Any],
    mapeos_campos: Dict[str, Dict[str, Any]],
    enlace_pago: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Crea una tarjeta/deal en HOR CARTERA / RESULTADO LLAMADA IA.

    Requiere contacto válido porque debe enviar:
    contactIds: [id_contacto]
    """

    if not contacto_bitrix_valido(contacto):
        raise ErrorBitrixAPI(
            mensaje="No se puede crear deal porque el contacto no tiene id válido.",
            metodo="crm.item.add",
            respuesta=contacto,
        )

    id_contacto = int(contacto.get("id") or contacto.get("ID"))

    campos = construir_campos_deal_proceso(
        codigo_proceso=codigo_proceso,
        variables_entrada=variables_entrada,
        variables_salida=variables_salida,
        id_contacto=id_contacto,
        category_id=int(destino_bitrix["category_id"]),
        stage_id=str(destino_bitrix["stage_id"]),
        enlace_pago=enlace_pago,
        assigned_by_id=BITRIX_ASSIGNED_BY_ID,
        mapeos_campos=mapeos_campos,
    )

    cuerpo = {
        "entityTypeId": DEAL_ENTITY_TYPE_ID,
        "fields": campos,
        "useOriginalUfNames": "Y",
    }

    logger.info(
        "Creando deal Bitrix | proceso=%s | category_id=%s | stage_id=%s | cuerpo=%s",
        codigo_proceso,
        destino_bitrix.get("category_id"),
        destino_bitrix.get("stage_id"),
        json.dumps(cuerpo, ensure_ascii=False, default=str),
    )

    datos = await llamar_bitrix("crm.item.add", cuerpo)

    resultado_deal = datos.get("result") if isinstance(datos, dict) else None
    item_creado = (
        resultado_deal.get("item")
        if isinstance(resultado_deal, dict) and isinstance(resultado_deal.get("item"), dict)
        else resultado_deal
    )
    logger.info(
        "Deal creado correctamente | proceso=%s | id_deal=%s | category_id=%s | stage_id=%s",
        codigo_proceso,
        item_creado.get("id") if isinstance(item_creado, dict) else None,
        item_creado.get("categoryId") if isinstance(item_creado, dict) else destino_bitrix.get("category_id"),
        item_creado.get("stageId") if isinstance(item_creado, dict) else destino_bitrix.get("stage_id"),
    )

    return resultado_deal


def extraer_item_bitrix_get(respuesta_bitrix: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extrae item desde respuestas de crm.item.get.
    """
    resultado = respuesta_bitrix.get("result") or {}

    if isinstance(resultado, dict) and isinstance(resultado.get("item"), dict):
        return resultado["item"]

    if isinstance(resultado, dict) and resultado.get("id"):
        return resultado

    return None


async def obtener_deal_bitrix_por_id(id_deal: int) -> Dict[str, Any]:
    """
    Consulta un deal Bitrix por ID usando crm.item.get.
    """
    cuerpo = {
        "entityTypeId": DEAL_ENTITY_TYPE_ID,
        "id": id_deal,
        "useOriginalUfNames": "Y",
    }

    datos = await llamar_bitrix("crm.item.get", cuerpo)
    item = extraer_item_bitrix_get(datos)

    if not item:
        raise ErrorBitrixAPI(
            mensaje="Bitrix no retornó el deal solicitado.",
            metodo="crm.item.get",
            respuesta=datos,
        )

    return item


async def obtener_contacto_bitrix_por_id(id_contacto: int) -> Dict[str, Any]:
    """
    Consulta un contacto Bitrix por ID usando crm.item.get.
    """
    cuerpo = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "id": id_contacto,
        "select": construir_select_busqueda_contacto(),
    }

    datos = await llamar_bitrix("crm.item.get", cuerpo)
    item = extraer_item_bitrix_get(datos)

    if not contacto_bitrix_valido(item):
        raise ErrorBitrixAPI(
            mensaje="Bitrix no retornó un contacto válido para el deal.",
            metodo="crm.item.get",
            respuesta=datos,
        )

    return item


async def actualizar_link_pago_en_deal_bitrix(
    id_deal: int,
    enlace_pago: str,
    codigo_campo_link: str = "UF_CRM_1779835103174",
) -> Dict[str, Any]:
    """
    Actualiza el campo personalizado Link de pago en el deal Bitrix.
    """
    cuerpo = {
        "entityTypeId": DEAL_ENTITY_TYPE_ID,
        "id": id_deal,
        "fields": {
            codigo_campo_link: enlace_pago,
        },
        "useOriginalUfNames": "Y",
    }

    datos = await llamar_bitrix("crm.item.update", cuerpo)
    return datos.get("result") or datos


async def actualizar_y_verificar_link_pago_en_deal_bitrix(
    id_deal: int,
    enlace_pago: str,
    codigo_campo_link: str = "UF_CRM_1779835103174",
    max_intentos: int = 3,
) -> Dict[str, Any]:
    intentos = max(1, min(int(max_intentos or 3), 3))
    errores = []
    ultimo_update = None
    ultimo_deal = None
    ultimo_link = None

    for intento in range(1, intentos + 1):
        try:
            logger.info(
                "Verificando link de pago en deal Bitrix | intento=%s/%s | id_deal=%s | esperado=%s",
                intento,
                intentos,
                id_deal,
                enlace_pago,
            )
            ultimo_deal = await obtener_deal_bitrix_por_id(id_deal)
            ultimo_link = extraer_link_pago_de_deal(ultimo_deal, codigo_campo_link)

            if ultimo_link == enlace_pago:
                logger.info(
                    "Link de pago ya verificado en deal Bitrix | intento=%s/%s | id_deal=%s",
                    intento,
                    intentos,
                    id_deal,
                )
                return {
                    "actualizado": ultimo_update is not None,
                    "verificado": True,
                    "intentos": intento,
                    "link_en_bitrix": ultimo_link,
                    "update_result": ultimo_update,
                    "deal_actualizado": ultimo_deal,
                    "errores": errores,
                }

            logger.info(
                "Actualizando link de pago en deal Bitrix | intento=%s/%s | id_deal=%s | actual=%s | esperado=%s",
                intento,
                intentos,
                id_deal,
                ultimo_link,
                enlace_pago,
            )
            ultimo_update = await actualizar_link_pago_en_deal_bitrix(
                id_deal=id_deal,
                enlace_pago=enlace_pago,
                codigo_campo_link=codigo_campo_link,
            )
            ultimo_deal = await obtener_deal_bitrix_por_id(id_deal)
            ultimo_link = extraer_link_pago_de_deal(ultimo_deal, codigo_campo_link)

            if ultimo_link == enlace_pago:
                logger.info(
                    "Link de pago actualizado y verificado en deal Bitrix | intento=%s/%s | id_deal=%s",
                    intento,
                    intentos,
                    id_deal,
                )
                return {
                    "actualizado": True,
                    "verificado": True,
                    "intentos": intento,
                    "link_en_bitrix": ultimo_link,
                    "update_result": ultimo_update,
                    "deal_actualizado": ultimo_deal,
                    "errores": errores,
                }

        except Exception as exc:
            error_serializable = convertir_a_json_seguro(
                {
                    "intento": intento,
                    "tipo": type(exc).__name__,
                    "mensaje": str(exc),
                }
            )
            errores.append(error_serializable)
            logger.error(
                "Fallo verificando/actualizando link en deal Bitrix | intento=%s/%s | id_deal=%s | error=%s",
                intento,
                intentos,
                id_deal,
                str(exc),
            )

    logger.error(
        "No se verifico link de pago en deal Bitrix despues de %s intentos | id_deal=%s | esperado=%s | ultimo=%s",
        intentos,
        id_deal,
        enlace_pago,
        ultimo_link,
    )
    return {
        "actualizado": ultimo_update is not None,
        "verificado": False,
        "intentos": intentos,
        "link_en_bitrix": ultimo_link,
        "update_result": ultimo_update,
        "deal_actualizado": ultimo_deal,
        "errores": errores,
    }


async def actualizar_correo_contacto_bitrix_si_falta(
    contacto: Dict[str, Any],
    correo: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Completa el email del contacto en Bitrix cuando el contacto no lo tiene
    y el webhook recibio un correo valido.
    """
    correo_normalizado = normalizar_correo_o_none(correo)
    if not correo_normalizado:
        return None

    correo_actual = normalizar_correo_o_none(extraer_valor_contacto(contacto, "email"))
    if correo_actual:
        return None

    id_contacto = contacto.get("id") or contacto.get("ID")
    try:
        id_contacto = int(id_contacto)
    except (TypeError, ValueError):
        return None

    cuerpo = {
        "entityTypeId": CONTACT_ENTITY_TYPE_ID,
        "id": id_contacto,
        "fields": {
            "email": correo_normalizado,
        },
    }

    datos = await llamar_bitrix("crm.item.update", cuerpo)
    contacto["email"] = correo_normalizado
    return datos.get("result") or datos


def extraer_link_pago_de_deal(
    deal: Dict[str, Any],
    codigo_campo_link: str = "UF_CRM_1779835103174",
) -> Optional[str]:
    return limpiar_texto(deal.get(codigo_campo_link))


def extraer_id_contacto_de_deal(deal: Dict[str, Any]) -> Optional[int]:
    id_contacto = deal.get("contactId") or deal.get("CONTACT_ID")

    if id_contacto is None and isinstance(deal.get("contactIds"), list) and deal["contactIds"]:
        id_contacto = deal["contactIds"][0]

    try:
        return int(id_contacto)
    except (TypeError, ValueError):
        return None


def construir_datos_pago_desde_deal_confirmado(
    deal: Dict[str, Any],
    contacto: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Reconstruye input_variables/output_vars desde los campos guardados en el deal.
    """
    correo_contacto = extraer_valor_contacto(contacto, "email")
    telefono_contacto = extraer_valor_contacto(contacto, "phone")

    input_variables = {
        "CEDULA": limpiar_texto(deal.get("UF_CRM_1778865296676")),
        "CORREO": limpiar_texto(correo_contacto),
        "NOMBRE": limpiar_texto(deal.get("title")),
        "TELEFONO": limpiar_texto(telefono_contacto or deal.get("UF_CRM_1778865231524")),
        "PAGADURIA": limpiar_texto(deal.get("UF_CRM_1773780818920")),
        "MORA_TOTAL": deal.get("UF_CRM_1773841728850"),
        "MORA": deal.get("UF_CRM_1773841728850"),
        "CUOTA": deal.get("UF_CRM_1773781100010"),
        "ID_LIBRANZA": limpiar_texto(deal.get("UF_CRM_1778865126453")),
        "VALOR_CONFIRMADO": deal.get("UF_CRM_1773841764862"),
    }

    output_vars = {
        "valor_confirmado": deal.get("UF_CRM_1773841764862"),
        "fechacuerdopago": limpiar_texto(deal.get("UF_CRM_1778864914788")),
        "gestion_final": limpiar_texto(deal.get("UF_CRM_1778864749613")),
        "motivo_principal": limpiar_texto(deal.get("UF_CRM_1778864857436")),
        "interes_pagar": limpiar_texto(deal.get("UF_CRM_1778864879029")),
        "pago_hoy": limpiar_texto(deal.get("UF_CRM_1778864893956")),
        "resumenllamada": limpiar_texto(deal.get("UF_CRM_1773841785299")),
        "detalle_acuerdo": limpiar_texto(deal.get("UF_CRM_1773841804045")),
    }

    validation = {
        "aprobado": True,
        "intencion_pago": True,
        "origen_validacion": "deal_confirmado_bitrix",
        "id_deal_bitrix": deal.get("id"),
        "stageId": deal.get("stageId"),
    }

    return input_variables, output_vars, validation


def extraer_valor_contacto(contacto: Dict[str, Any], campo: str) -> Optional[str]:
    """
    Extrae email/phone aunque Bitrix lo retorne como string, lista o dict.
    """
    valor = contacto.get(campo)

    if isinstance(valor, str):
        return valor

    if isinstance(valor, list) and valor:
        primero = valor[0]
        if isinstance(primero, dict):
            return primero.get("value") or primero.get("VALUE")
        return str(primero)

    if isinstance(valor, dict):
        return valor.get("value") or valor.get("VALUE")

    return None


@app.api_route("/enviar-id-confirmado", methods=["GET", "POST"])
async def crear_orden_pago_desde_deal_confirmado(
    request: Request,
    ID_DEAL: Optional[int] = Query(None),
    id_deal: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    ID: Optional[int] = Query(None),
):
    """
    Webhook GET para automatizaciones Bitrix que envían el ID del deal confirmado.
    """
    method_name = "crear_orden_pago_desde_deal_confirmado"
    id_deal_final = ID_DEAL or id_deal or deal_id or ID
    raw_body = await request.body()
    body_text = raw_body.decode("utf-8", errors="replace")

    logger.info(
        "Endpoint iniciado | %s /enviar-id-confirmado | url=%s | query=%s | client=%s | body_bytes=%s | body=%s",
        request.method,
        str(request.url),
        dict(request.query_params),
        request.client.host if request.client else None,
        len(raw_body),
        body_text,
    )
    logger.info("Parametro deal resuelto en /enviar-id-confirmado | id_deal=%s", id_deal_final)

    if not id_deal_final or id_deal_final <= 0:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "mensaje": "Debe enviar un ID_DEAL numérico mayor a cero.",
                "parametros_aceptados": ["ID_DEAL", "id_deal", "deal_id", "ID"],
            },
        )

    try:
        deal = await obtener_deal_bitrix_por_id(id_deal_final)
        logger.info(
            "Deal consultado en /enviar-id-confirmado | id_deal=%s | link_actual=%s | deal=%s",
            id_deal_final,
            extraer_link_pago_de_deal(deal),
            json.dumps(deal, ensure_ascii=False, default=str),
        )
        enlace_existente = extraer_link_pago_de_deal(deal)
        if enlace_existente:
            respuesta = {
                "ok": True,
                "action": "payment_link_already_registered_in_deal",
                "id_deal": id_deal_final,
                "payment_order_created": False,
                "payment_link": enlace_existente,
                "bitrix_link_updated": False,
                "deal": {"item": deal},
            }
            logger.info(
                "Respuesta /enviar-id-confirmado con link existente | %s",
                json.dumps(respuesta, ensure_ascii=False, default=str),
            )
            return respuesta

        id_contacto = extraer_id_contacto_de_deal(deal)

        if id_contacto is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "mensaje": "El deal no tiene contactId/contactIds válido para crear orden de pago.",
                    "id_deal": id_deal_final,
                },
            )

        contacto = await obtener_contacto_bitrix_por_id(id_contacto)
        logger.info(
            "Contacto consultado en /enviar-id-confirmado | id_contacto=%s | contacto=%s",
            id_contacto,
            json.dumps(contacto, ensure_ascii=False, default=str),
        )
        input_variables, output_vars, validation = construir_datos_pago_desde_deal_confirmado(
            deal=deal,
            contacto=contacto,
        )
        logger.info(
            "Datos reconstruidos desde deal confirmado | input_variables=%s | output_vars=%s | validation=%s",
            json.dumps(input_variables, ensure_ascii=False, default=str),
            json.dumps(output_vars, ensure_ascii=False, default=str),
            json.dumps(validation, ensure_ascii=False, default=str),
        )
        deal_result = {"item": deal}

        solicitud_pago = construir_solicitud_pago_desde_bitrix(
            input_variables=input_variables,
            output_vars=output_vars,
            validation=validation,
            contact=contacto,
            deal_result=deal_result,
        )

        contexto_pago = construir_contexto_auditoria_http(
            request=request,
            operacion="crear_orden_desde_deal_confirmado_bitrix",
            payload_recibido=modelo_a_diccionario(solicitud_pago),
        )

        payment_order = await ServicioOrdenesPago().crear_orden_pago(
            solicitud=solicitud_pago,
            contexto_auditoria=contexto_pago,
        )
        logger.info(
            "Orden Payvalida obtenida desde /enviar-id-confirmado | %s",
            json.dumps(payment_order, ensure_ascii=False, default=str),
        )

        enlace_pago = payment_order.get("enlace_pago")
        bitrix_link_update = None

        if enlace_pago:
            bitrix_link_update = await actualizar_y_verificar_link_pago_en_deal_bitrix(
                id_deal=id_deal_final,
                enlace_pago=enlace_pago,
                max_intentos=3,
            )
            if isinstance(bitrix_link_update.get("deal_actualizado"), dict):
                deal_result = {"item": bitrix_link_update["deal_actualizado"]}
            if not bitrix_link_update.get("verificado"):
                raise ErrorBitrixAPI(
                    mensaje="No se pudo verificar el link de pago en el campo Bitrix del deal.",
                    metodo="crm.item.update",
                    respuesta=bitrix_link_update,
                )

        respuesta = {
            "ok": True,
            "action": "payment_order_created_from_confirmed_deal",
            "id_deal": id_deal_final,
            "client_found": True,
            "client": contacto,
            "deal": deal_result,
            "payment_order_created": True,
            "payment_order": payment_order,
            "payment_link": enlace_pago,
            "bitrix_link_updated": bitrix_link_update is not None,
            "bitrix_link_verified": bool(bitrix_link_update and bitrix_link_update.get("verificado")),
            "bitrix_link_update": bitrix_link_update,
            #"agent_context": contexto_agente,
        }
        logger.info(
            "Respuesta /enviar-id-confirmado | %s",
            json.dumps(respuesta, ensure_ascii=False, default=str),
        )
        return respuesta

    except ErrorBitrixAPI as exc:
        logger.error("Error Bitrix en /enviar-id-confirmado | %s", exc.mensaje)
        await error_notify(method_name, str(id_deal_final), exc.mensaje)
        raise HTTPException(
            status_code=502,
            detail=construir_error_webhook_debug(
                action="failed_enviar_id_confirmado",
                tipo_error="error_bitrix",
                mensaje=exc.mensaje,
                status_code=502,
                contact=locals().get("contacto"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "id_deal": id_deal_final,
                    "mensaje": exc.mensaje,
                    "metodo_bitrix": exc.metodo,
                    "codigo_estado_bitrix": exc.codigo_estado,
                    "respuesta_bitrix": exc.respuesta,
                },
            ),
        )

    except ErrorValidacionPayvalida as exc:
        logger.error("Validación Payvalida falló en /enviar-id-confirmado | %s", exc.mensaje)
        error_payvalida = exc.a_respuesta()
        raise HTTPException(
            status_code=422,
            detail=construir_error_webhook_debug(
                action="failed_enviar_id_confirmado",
                tipo_error="validacion_payvalida",
                mensaje=exc.mensaje,
                status_code=422,
                contact=locals().get("contacto"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    **error_payvalida,
                    "id_deal": id_deal_final,
                },
            ),
        )

    except ErrorProveedorPago as exc:
        logger.error("Proveedor Payvalida falló en /enviar-id-confirmado | %s", str(exc))
        raise HTTPException(
            status_code=502,
            detail=construir_error_webhook_debug(
                action="failed_enviar_id_confirmado",
                tipo_error="error_proveedor_payvalida",
                mensaje=str(exc),
                status_code=502,
                contact=locals().get("contacto"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "id_deal": id_deal_final,
                    "mensaje": str(exc),
                },
            ),
        )

    except HTTPException:
        raise

    except ValueError as exc:
        logger.error("Mapeo Bitrix a Payvalida falló en /enviar-id-confirmado | %s", str(exc))
        raise HTTPException(
            status_code=422,
            detail=construir_error_webhook_debug(
                action="failed_enviar_id_confirmado",
                tipo_error="payload_gestion_incompleto",
                mensaje=str(exc),
                status_code=422,
                contact=locals().get("contacto"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "id_deal": id_deal_final,
                    "mensaje": str(exc),
                },
            ),
        )

    except Exception as exc:
        logger.error("Error interno en /enviar-id-confirmado | %s", str(exc))
        logger.error(traceback.format_exc())
        await error_notify(method_name, str(id_deal_final), str(exc))
        raise HTTPException(
            status_code=500,
            detail=construir_error_webhook_debug(
                action="failed_enviar_id_confirmado",
                tipo_error="error_interno",
                mensaje=str(exc),
                status_code=500,
                contact=locals().get("contacto"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "id_deal": id_deal_final,
                    "mensaje": str(exc),
                    "traceback": traceback.format_exc(),
                },
            ),
        )


async def obtener_etapas_embudo_bitrix(category_id: int) -> List[Dict[str, Any]]:
    """Consulta y normaliza las etapas disponibles de un embudo Bitrix."""
    datos = await llamar_bitrix(
        "crm.dealcategory.stage.list",
        {"ID": int(category_id)},
    )
    resultado = datos.get("result") if isinstance(datos, dict) else None
    if not isinstance(resultado, list):
        raise ErrorBitrixAPI(
            mensaje="Bitrix no retorno una lista valida de etapas.",
            metodo="crm.dealcategory.stage.list",
            respuesta=datos,
        )
    return [
        {
            "category_id": int(category_id),
            "stage_id": etapa.get("STATUS_ID"),
            "nombre": etapa.get("NAME"),
            "orden": etapa.get("SORT"),
        }
        for etapa in resultado
        if isinstance(etapa, dict) and etapa.get("STATUS_ID")
    ]


@app.get("/bitrix/embudos/{category_id}/etapas")
async def listar_etapas_embudo_bitrix(category_id: int):
    try:
        return {
            "ok": True,
            "category_id": category_id,
            "etapas": await obtener_etapas_embudo_bitrix(category_id),
        }
    except ErrorBitrixAPI as exc:
        raise convertir_error_bitrix_a_http(exc) from exc


@app.put("/bitrix/configuracion/procesos/{codigo_proceso}")
async def configurar_destino_proceso_bitrix(
    codigo_proceso: str,
    payload: BitrixProcessDestinationRequest,
):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    if not codigo:
        raise HTTPException(status_code=422, detail={"tipo_error": "codigo_proceso_vacio"})

    try:
        etapas = await obtener_etapas_embudo_bitrix(payload.category_id)
        etapa = next(
            (item for item in etapas if item["stage_id"] == payload.stage_id),
            None,
        )
        if etapa is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "tipo_error": "etapa_no_pertenece_al_embudo",
                    "codigo_proceso": codigo,
                    "category_id": payload.category_id,
                    "stage_id": payload.stage_id,
                },
            )
        guardado = await asignar_destino_bitrix_proceso(
            codigo_proceso=codigo,
            nombre_proceso=payload.nombre_proceso,
            category_id=payload.category_id,
            stage_id=payload.stage_id,
            nombre_etapa=etapa.get("nombre"),
            activo=payload.activo,
        )
        return {"ok": True, "action": "process_destination_upserted", "configuracion": guardado}
    except HTTPException:
        raise
    except ErrorBitrixAPI as exc:
        raise convertir_error_bitrix_a_http(exc) from exc
    except Exception as exc:
        logger.error("Error guardando destino Bitrix | proceso=%s | error=%s", codigo, str(exc))
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc


@app.get("/bitrix/configuracion/procesos")
async def obtener_destinos_procesos_bitrix(activo: Optional[bool] = Query(default=None)):
    try:
        configuraciones = await listar_destinos_bitrix_procesos(activo)
        return {"ok": True, "configuraciones": configuraciones}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc


@app.get("/bitrix/configuracion/procesos/{codigo_proceso}")
async def obtener_destino_proceso_bitrix(codigo_proceso: str):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    try:
        configuracion = await obtener_destino_bitrix_proceso(codigo, solo_activo=False)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc
    if configuracion is None:
        raise HTTPException(status_code=404, detail={"tipo_error": "proceso_no_configurado"})
    return {"ok": True, "configuracion": configuracion}


@app.patch("/bitrix/configuracion/procesos/{codigo_proceso}/desactivar")
async def desactivar_destino_proceso_bitrix(codigo_proceso: str):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    try:
        actualizado = await desactivar_destino_bitrix_proceso(codigo)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc
    if not actualizado:
        raise HTTPException(status_code=404, detail={"tipo_error": "proceso_no_configurado"})
    return {"ok": True, "action": "process_destination_disabled", "codigo_proceso": codigo}


@app.put("/bitrix/configuracion/procesos/{codigo_proceso}/campos/{campo_logico}")
async def configurar_campo_proceso_bitrix(
    codigo_proceso: str,
    campo_logico: str,
    payload: BitrixProcessFieldMappingRequest,
):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = normalizar_clave(campo_logico)
    predeterminado = MAPEOS_CAMPOS_PREDETERMINADOS.get(codigo, {}).get(campo)
    if predeterminado is None:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "campo_logico_no_soportado",
                "campos_soportados": campos_logicos_soportados(codigo),
            },
        )
    if (
        codigo == "renovaciones"
        and campo == "universidad"
        and payload.codigo_campo_bitrix
        == CAMPO_BITRIX_UNIVERSIDAD_RENOVACIONES_DEPRECADO
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "tipo_error": "campo_bitrix_deprecado",
                "mensaje": "El campo de texto anterior para universidad ya no puede utilizarse.",
                "codigo_campo_bitrix": payload.codigo_campo_bitrix,
            },
        )
    try:
        existente = await obtener_mapeo_campo_configurado(codigo, campo)
        base = existente or predeterminado
        guardado = await asignar_mapeo_campo_proceso(
            codigo_proceso=codigo,
            campo_logico=campo,
            codigo_campo_bitrix=payload.codigo_campo_bitrix,
            tipo_campo=payload.tipo_campo or base.get("tipo_campo") or "texto",
            valor_positivo=(
                payload.valor_positivo
                if payload.valor_positivo is not None
                else base.get("valor_positivo")
            ),
            valor_negativo=(
                payload.valor_negativo
                if payload.valor_negativo is not None
                else base.get("valor_negativo")
            ),
            activo=payload.activo,
        )
        return {"ok": True, "action": "process_field_upserted", "mapeo": guardado}
    except Exception as exc:
        logger.error(
            "Error guardando campo Bitrix | proceso=%s | campo=%s | error=%s",
            codigo,
            campo,
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc


@app.get("/bitrix/configuracion/procesos/{codigo_proceso}/campos")
async def obtener_campos_proceso_bitrix(
    codigo_proceso: str,
    activo: Optional[bool] = Query(default=None),
):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    try:
        configurados = await listar_mapeos_campos_configurados(codigo, activo)
        resueltos = await resolver_mapeos_campos_proceso(codigo)
        return {
            "ok": True,
            "codigo_proceso": codigo,
            "configurados": configurados,
            "resueltos": resueltos,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc


@app.get("/bitrix/configuracion/procesos/{codigo_proceso}/campos/{campo_logico}")
async def obtener_campo_proceso_bitrix(codigo_proceso: str, campo_logico: str):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = normalizar_clave(campo_logico)
    if campo not in MAPEOS_CAMPOS_PREDETERMINADOS.get(codigo, {}):
        raise HTTPException(status_code=404, detail={"tipo_error": "campo_logico_no_soportado"})
    try:
        configurado = await obtener_mapeo_campo_configurado(codigo, campo)
        efectivo = (await resolver_mapeos_campos_proceso(codigo)).get(campo)
        return {
            "ok": True,
            "codigo_proceso": codigo,
            "campo_logico": campo,
            "configurado": configurado,
            "efectivo": efectivo,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc


@app.patch("/bitrix/configuracion/procesos/{codigo_proceso}/campos/{campo_logico}/desactivar")
async def desactivar_campo_proceso_bitrix(codigo_proceso: str, campo_logico: str):
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = normalizar_clave(campo_logico)
    try:
        actualizado = await desactivar_mapeo_campo_proceso(codigo, campo)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "tipo_error": "error_configuracion_bitrix_db"},
        ) from exc
    if not actualizado:
        raise HTTPException(status_code=404, detail={"tipo_error": "campo_no_configurado"})
    return {
        "ok": True,
        "action": "process_field_disabled",
        "codigo_proceso": codigo,
        "campo_logico": campo,
    }


@app.post("/bitrix/debug/search-client")
async def bitrix_debug_search_client(payload: BitrixDebugSearchClientRequest):
    """
    Endpoint de prueba para buscar cliente en Bitrix usando input_variables.
    """

    method_name = "bitrix_debug_search_client"
    input_variables = payload.model_dump(exclude_none=True)
    output_vars = {}
    contexto_agente = {
        "origen": "bitrix_debug_search_client",
        "objetivo": "debug_busqueda_cliente",
        "requiere_bitrix": True,
        "requiere_link_pago": False,
    }
    criteria = None
    contact = None
    validation = None
    deal_result = None
    payment_order_result = None
    bitrix_link_update = None

    try:
        criteria = construir_criterios_busqueda_bitrix(input_variables)

        logger.info(
            "Endpoint iniciado | POST /bitrix/debug/search-client | criteria=%s",
            json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
        )

        contact = await buscar_contacto_desde_variables_entrada(input_variables)

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

    except ErrorBitrixAPI as exc:
        logger.error("Error Bitrix en /bitrix/debug/search-client | %s", exc.mensaje)
        identificador_debug = (
            str(criteria.cedula or criteria.correo or criteria.variantes_telefono)
            if criteria is not None
            else str(input_variables.get("CEDULA") or input_variables.get("CORREO") or "N/A")
        )
        await error_notify(
            method_name,
            identificador_debug,
            exc.mensaje,
        )
        raise HTTPException(
            status_code=502,
            detail=construir_error_webhook_debug(
                tipo_error="error_bitrix",
                mensaje=exc.mensaje,
                status_code=502,
                input_variables=input_variables,
                criteria=criteria,
                validation=validation,
                contact=contact,
                deal_result=deal_result,
                payment_order_result=payment_order_result,
                bitrix_link_update=bitrix_link_update,
                error={
                    "mensaje": exc.mensaje,
                    "metodo_bitrix": exc.metodo,
                    "codigo_estado_bitrix": exc.codigo_estado,
                    "respuesta_bitrix": exc.respuesta,
                },
            ),
        )

    except Exception as exc:
        logger.error("Error interno en /bitrix/debug/search-client | %s", str(exc))
        logger.error(traceback.format_exc())
        identificador_debug = (
            str(criteria.cedula or criteria.correo or criteria.variantes_telefono)
            if criteria is not None
            else str(input_variables.get("CEDULA") or input_variables.get("CORREO") or "N/A")
        )
        await error_notify(
            method_name,
            identificador_debug,
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=construir_error_webhook_debug(
                tipo_error="error_interno",
                mensaje=str(exc),
                status_code=500,
                input_variables=input_variables,
                criteria=criteria,
                validation=validation,
                contact=contact,
                deal_result=deal_result,
                payment_order_result=payment_order_result,
                bitrix_link_update=bitrix_link_update,
                error={
                    "mensaje": str(exc),
                    "traceback": traceback.format_exc(),
                },
            ),
        )


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

    logger.info("JSON entrante recibido | bytes=%s", len(raw_body))

    try:
        incoming_json = json.loads(body_text) if body_text.strip() else {}
        input_recibido = incoming_json.get("input_variables", {}) if isinstance(incoming_json, dict) else {}
        extraidas_recibidas = incoming_json.get("extracted_variables", {}) if isinstance(incoming_json, dict) else {}
        cantidad_extraidas = (
            len(extraidas_recibidas)
            if isinstance(extraidas_recibidas, (dict, list))
            else 0
        )
        logger.info(
            "JSON entrante parseado | id=%s | status=%s | input_keys=%s | extracted_count=%s",
            incoming_json.get("id") if isinstance(incoming_json, dict) else None,
            incoming_json.get("status") if isinstance(incoming_json, dict) else None,
            sorted(input_recibido.keys()) if isinstance(input_recibido, dict) else [],
            cantidad_extraidas,
        )
    except json.JSONDecodeError as exc:
        logger.error("JSON entrante invalido | %s", exc)
        raise HTTPException(
            status_code=400,
            detail=convertir_a_json_seguro(
                {
                    "ok": False,
                    "action": "failed_call_completed",
                    "tipo_error": "json_invalido",
                    "mensaje": "El cuerpo recibido no es un JSON valido.",
                    "http_status": 400,
                    "error": {
                        "mensaje": str(exc),
                        "linea": exc.lineno,
                        "columna": exc.colno,
                        "posicion": exc.pos,
                    },
                    "debug": {
                        "bytes_recibidos": len(raw_body),
                        "body_preview": body_text[:1000],
                    },
                    "sugerencias": construir_sugerencias_error_webhook("json_invalido"),
                }
            ),
        )

    try:
        payload = BitrixCallCompletedRequest.model_validate(incoming_json)
    except ValidationError as exc:
        errores_validacion = convertir_a_json_seguro(exc.errors())
        logger.error(
            "Error validación BitrixCallCompletedRequest | %s",
            json.dumps(errores_validacion, ensure_ascii=False, indent=2, default=str),
        )
        input_debug = (
            incoming_json.get("input_variables")
            if isinstance(incoming_json, dict) and isinstance(incoming_json.get("input_variables"), dict)
            else {}
        )
        extracted_debug = (
            incoming_json.get("extracted_variables")
            if isinstance(incoming_json, dict)
            else []
        )
        output_debug = construir_mapa_variables_salida(extracted_debug)
        raise HTTPException(
            status_code=422,
            detail=convertir_a_json_seguro(
                {
                    "ok": False,
                    "action": "failed_call_completed",
                    "tipo_error": "validacion_payload",
                    "mensaje": "El payload no cumple el contrato esperado del webhook.",
                    "http_status": 422,
                    "errores": errores_validacion,
                    "debug": {
                        "variables_entrada_clave": resumir_variables_debug(
                            input_debug,
                            VARIABLES_ENTRADA_DEBUG_WEBHOOK,
                        ),
                        "variables_salida_clave": resumir_variables_debug(
                            output_debug,
                            VARIABLES_SALIDA_DEBUG_WEBHOOK,
                        ),
                    },
                    "sugerencias": construir_sugerencias_error_webhook("validacion_payload"),
                }
            ),
        )

    input_variables = payload.input_variables.model_dump(exclude_none=True)
    extracted_variables = [
        item.model_dump(exclude_none=True) for item in payload.extracted_variables
    ]

    output_vars = construir_mapa_variables_salida(extracted_variables)
    recording_url = limpiar_texto(
        incoming_json.get("recording_url") or incoming_json.get("recordingUrl")
    )
    if recording_url:
        output_vars["recording_url"] = recording_url
    codigo_proceso = resolver_codigo_proceso(input_variables, output_vars)
    if codigo_proceso == "cobranzas":
        contexto_agente = construir_contexto_agente_universal(input_variables, output_vars)
        contexto_agente["objetivo"] = codigo_proceso
    else:
        contexto_agente = {
            "origen": limpiar_texto(obtener_variable_entrada(input_variables, "ORIGEN"))
            or "agente_no_especificado",
            "objetivo": codigo_proceso,
            "requiere_bitrix": True,
        }
    criteria = construir_criterios_busqueda_bitrix(input_variables)

    try:
        logger.info(
            "Input variables validadas | %s",
            json.dumps(input_variables, ensure_ascii=False, default=str),
        )

        if codigo_proceso not in PROCESOS_SOPORTADOS:
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "action": "failed_call_completed",
                    "tipo_error": "proceso_no_soportado",
                    "mensaje": f"Proceso no soportado: {codigo_proceso or 'vacio'}.",
                    "codigo_proceso": codigo_proceso,
                    "procesos_soportados": sorted(PROCESOS_SOPORTADOS),
                },
            )

        variables_trazabilidad = filtrar_variables_salida_proceso(
            codigo_proceso,
            output_vars,
        )
        logger.info(
            "Variables del proceso validadas | proceso=%s | variables=%s",
            codigo_proceso,
            json.dumps(variables_trazabilidad, ensure_ascii=False, default=str),
        )

        validation = evaluar_interes_proceso(codigo_proceso, output_vars)

        logger.info(
            "Contexto proceso | %s",
            json.dumps(
                {
                    clave: valor
                    for clave, valor in contexto_agente.items()
                    if codigo_proceso == "cobranzas"
                    or clave in {"origen", "objetivo", "requiere_bitrix"}
                },
                ensure_ascii=False,
                default=str,
            ),
        )

        logger.info(
            "Resultado validacion interes/proceso | %s",
            json.dumps(validation, ensure_ascii=False, default=str),
        )

        if not validation.get("approved", validation.get("aprobado", False)):
            logger.info(
                "Proceso sin interes explicito. No se consulta configuracion ni Bitrix | blockers=%s",
                json.dumps(validation.get("blockers", validation.get("bloqueadores", [])), ensure_ascii=False, default=str),
            )

            return {
                "ok": True,
                "action": "skipped_no_interest",
                "validation": validation,
                "lookup_criteria": criteria.__dict__,
                "client_found": False,
                "client": None,
                "deal_created": False,
                "deal": None,
                "payment_order_created": False,
                "payment_order": None,
                "agent_context": contexto_agente,
                "codigo_proceso": codigo_proceso,
                "reason": "El proceso no contiene interes explicito.",
            }

        try:
            destino_bitrix = await obtener_destino_bitrix_proceso(codigo_proceso)
            mapeos_campos_bitrix = (
                await resolver_mapeos_campos_proceso(codigo_proceso)
                if destino_bitrix is not None
                else {}
            )
        except Exception as exc:
            logger.error(
                "Error consultando configuracion Bitrix del proceso | proceso=%s | error=%s",
                codigo_proceso,
                str(exc),
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "ok": False,
                    "action": "failed_call_completed",
                    "tipo_error": "error_configuracion_bitrix_db",
                    "mensaje": "No fue posible consultar la configuracion Bitrix del proceso.",
                    "codigo_proceso": codigo_proceso,
                },
            ) from exc

        if not destino_bitrix:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "action": "failed_call_completed",
                    "tipo_error": "ruta_bitrix_no_configurada",
                    "mensaje": "El proceso no tiene un embudo y etapa activos configurados.",
                    "codigo_proceso": codigo_proceso,
                },
            )

        logger.info(
            "Interes aprobado. Iniciando consulta Bitrix | proceso=%s | category_id=%s | stage_id=%s | criteria=%s",
            codigo_proceso,
            destino_bitrix.get("category_id"),
            destino_bitrix.get("stage_id"),
            json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
        )

        contact = await buscar_contacto_desde_variables_entrada(input_variables)

        if contact:
            logger.info(
                "CLIENTE ENCONTRADO EN BITRIX24 | %s",
                json.dumps(contact, ensure_ascii=False, default=str),
            )
            try:
                correo_actualizado = await actualizar_correo_contacto_bitrix_si_falta(
                    contacto=contact,
                    correo=obtener_variable_entrada(input_variables, "CORREO"),
                )
                if correo_actualizado:
                    logger.info(
                        "Correo de contacto Bitrix actualizado desde input_variables | contacto_id=%s",
                        contact.get("id") or contact.get("ID"),
                    )
            except ErrorBitrixAPI as exc:
                logger.warning(
                    "No se pudo actualizar correo del contacto Bitrix; se continua con fallback local | contacto_id=%s | error=%s",
                    contact.get("id") or contact.get("ID"),
                    exc.mensaje,
                )
        else:
            logger.warning(
                "CLIENTE NO ENCONTRADO EN BITRIX24 | criteria=%s",
                json.dumps(criteria.__dict__, ensure_ascii=False, default=str),
            )

        deal_result = None
        payment_order_result = None
        bitrix_link_update = None

        if CREATE_DEAL_ON_VALID_PAYMENT:
            logger.info(
                "Creacion de deal habilitada | proceso=%s | contexto=%s",
                codigo_proceso,
                json.dumps(contexto_agente, ensure_ascii=False, default=str),
            )

            if not contacto_bitrix_valido(contact):
                logger.error("No se crea deal porque no hay contacto valido con id.")
                raise HTTPException(
                    status_code=404,
                    detail=construir_error_webhook_debug(
                        tipo_error="contacto_bitrix_no_encontrado",
                        mensaje="No se encontro contacto valido en Bitrix para asociar el deal.",
                        status_code=404,
                        input_variables=input_variables,
                        output_vars=output_vars,
                        contexto_agente=contexto_agente,
                        criteria=criteria,
                        validation=validation,
                        contact=contact,
                        deal_result=deal_result,
                        payment_order_result=payment_order_result,
                        bitrix_link_update=bitrix_link_update,
                    ),
                )

            try:
                enlace_pago = None

                if codigo_proceso == "cobranzas" and contexto_agente.get("usa_payvalida"):
                    solicitud_pago = construir_solicitud_pago_desde_bitrix_sin_deal(
                        input_variables=input_variables,
                        output_vars=output_vars,
                        validation=validation,
                        contact=contact,
                    )

                    contexto_pago = construir_contexto_auditoria_http(
                        request=request,
                        operacion="crear_orden_desde_bitrix",
                        payload_recibido=modelo_a_diccionario(solicitud_pago),
                    )

                    payment_order_result = await ServicioOrdenesPago().crear_orden_pago(
                        solicitud=solicitud_pago,
                        contexto_auditoria=contexto_pago,
                    )

                    logger.info(
                        "Orden Payvalida creada/reutilizada antes de crear deal Bitrix | %s",
                        json.dumps(payment_order_result, ensure_ascii=False, default=str),
                    )

                    enlace_pago = payment_order_result.get("enlace_pago")

                elif codigo_proceso == "cobranzas" and contexto_agente.get("requiere_link_pago"):
                    if contexto_agente.get("proveedor_pago") == "api_externa":
                        payment_order_result = await procesar_preconsulta_pago_one2credit_desde_evento(
                            variables_entrada=input_variables,
                            variables_salida=output_vars,
                        )
                        mensaje_gestion = limpiar_texto(payment_order_result.get("mensaje_gestion"))
                        if mensaje_gestion:
                            output_vars.setdefault("resumenllamada", mensaje_gestion)
                            output_vars.setdefault("detalle_acuerdo", mensaje_gestion)
                        orden_pago_externa = payment_order_result.get("orden_pago")
                        if isinstance(orden_pago_externa, dict):
                            enlace_pago = limpiar_texto(orden_pago_externa.get("link_pago"))
                            valor_orden_pago = orden_pago_externa.get("valor_orden_pago") or orden_pago_externa.get("monto_final_orden_pago") or orden_pago_externa.get("valor_parcial_procesado")
                            if valor_orden_pago is not None:
                                output_vars.setdefault("valor_confirmado", valor_orden_pago)
                    else:
                        payment_order_result = {
                            "proveedor": contexto_agente.get("proveedor_pago"),
                            "estado": "PENDIENTE_INTEGRACION",
                            "idempotente": False,
                            "mensaje": (
                                "El evento requiere link de pago, pero el proveedor configurado "
                                "no es Payvalida. Queda preparado para conectar el adaptador externo."
                            ),
                        }
                    logger.info(
                        "Gestion requiere link con proveedor externo | %s",
                        json.dumps(payment_order_result, ensure_ascii=False, default=str),
                    )

                deal_result = await crear_deal_resultado_llamada_ia(
                    variables_entrada=input_variables,
                    variables_salida=output_vars,
                    contacto=contact,
                    codigo_proceso=codigo_proceso,
                    destino_bitrix=destino_bitrix,
                    mapeos_campos=mapeos_campos_bitrix,
                    enlace_pago=enlace_pago,
                )

                item_deal_creado = deal_result.get("item") if isinstance(deal_result, dict) else None
                id_deal_creado = item_deal_creado.get("id") if isinstance(item_deal_creado, dict) else None
                logger.info(
                    "Deal creado desde webhook | proceso=%s | id_deal=%s | category_id=%s | stage_id=%s",
                    codigo_proceso,
                    id_deal_creado,
                    destino_bitrix.get("category_id"),
                    destino_bitrix.get("stage_id"),
                )
                if enlace_pago and id_deal_creado:
                    codigo_campo_link = (
                        mapeos_campos_bitrix.get("enlace_pago", {}).get("codigo_campo_bitrix")
                        or "UF_CRM_1779835103174"
                    )
                    bitrix_link_update = await actualizar_y_verificar_link_pago_en_deal_bitrix(
                        id_deal=int(id_deal_creado),
                        enlace_pago=enlace_pago,
                        codigo_campo_link=codigo_campo_link,
                        max_intentos=3,
                    )

                    if isinstance(bitrix_link_update.get("deal_actualizado"), dict):
                        deal_result = {"item": bitrix_link_update["deal_actualizado"]}

                    if enlace_pago and not bitrix_link_update.get("verificado"):
                        raise ErrorBitrixAPI(
                            mensaje="No se pudo verificar el link de pago en el campo Bitrix del deal.",
                            metodo="crm.item.update",
                            respuesta=bitrix_link_update,
                        )

                    logger.info(
                        "Deal Bitrix verificado con link de pago | id_deal=%s | link=%s | verificado=%s",
                        id_deal_creado,
                        bitrix_link_update.get("link_en_bitrix"),
                        bitrix_link_update.get("verificado"),
                    )
                elif id_deal_creado and codigo_proceso == "cobranzas":
                    logger.info(
                        "Deal Bitrix creado sin verificacion de link porque no se genero enlace | id_deal=%s",
                        id_deal_creado,
                    )

            except ErrorValidacionPayvalida as exc:
                logger.error("Validacion Payvalida fallo antes de crear deal | %s", exc.mensaje)
                error_payvalida = exc.a_respuesta()
                raise HTTPException(
                    status_code=422,
                    detail=construir_error_webhook_debug(
                        tipo_error="validacion_payvalida",
                        mensaje=exc.mensaje,
                        status_code=422,
                        input_variables=input_variables,
                        output_vars=output_vars,
                        contexto_agente=contexto_agente,
                        criteria=criteria,
                        validation=validation,
                        contact=contact,
                        deal_result=deal_result,
                        payment_order_result=payment_order_result,
                        bitrix_link_update=bitrix_link_update,
                        error=error_payvalida,
                    ),
                )
            except ErrorProveedorPago as exc:
                logger.error("Proveedor Payvalida fallo antes de crear deal | %s", str(exc))
                raise HTTPException(
                    status_code=502,
                    detail=construir_error_webhook_debug(
                        tipo_error="error_proveedor_payvalida",
                        mensaje=str(exc),
                        status_code=502,
                        input_variables=input_variables,
                        output_vars=output_vars,
                        contexto_agente=contexto_agente,
                        criteria=criteria,
                        validation=validation,
                        contact=contact,
                        deal_result=deal_result,
                        payment_order_result=payment_order_result,
                        bitrix_link_update=bitrix_link_update,
                        error={"mensaje": str(exc)},
                    ),
                )
            except ValueError as exc:
                logger.error("Mapeo/validacion de gestion fallo antes de crear deal | %s", str(exc))
                raise HTTPException(
                    status_code=422,
                    detail=construir_error_webhook_debug(
                        tipo_error="payload_gestion_incompleto",
                        mensaje=str(exc),
                        status_code=422,
                        input_variables=input_variables,
                        output_vars=output_vars,
                        contexto_agente=contexto_agente,
                        criteria=criteria,
                        validation=validation,
                        contact=contact,
                        deal_result=deal_result,
                        payment_order_result=payment_order_result,
                        bitrix_link_update=bitrix_link_update,
                        error={"mensaje": str(exc)},
                    ),
                )
        else:
            logger.info(
                "Creacion de deal deshabilitada por configuracion | proceso=%s",
                codigo_proceso,
            )

        return {
            "ok": True,
            "action": "processed_call_completed",
            "validation": validation,
            "lookup_criteria": criteria.__dict__,
            "ignored_fields": {
                "WHATSAPP_DISPONIBLE": "No se uso para buscar cliente porque pertenece a otra entidad/canal."
            },
            "client_found": contact is not None,
            "client": contact,
            "deal_created": deal_result is not None,
            "deal": deal_result,
            "payment_order_created": bool(
                payment_order_result
                and (
                    payment_order_result.get("id_orden_pago")
                    or payment_order_result.get("codigo_orden_interno")
                    or payment_order_result.get("enlace_pago")
                    or payment_order_result.get("link_pago")
                    or (
                        isinstance(payment_order_result.get("orden_pago"), dict)
                        and payment_order_result["orden_pago"].get("link_pago")
                    )
                )
            ),
            "payment_order": payment_order_result,
            "bitrix_link_updated": bitrix_link_update is not None,
            "bitrix_link_verified": bool(bitrix_link_update and bitrix_link_update.get("verificado")),
            "bitrix_link_update": bitrix_link_update,
            "agent_context": contexto_agente,
            "codigo_proceso": codigo_proceso,
            "destino_bitrix": destino_bitrix,
            "mapeos_campos_bitrix": mapeos_campos_bitrix,
        }

        if CREATE_DEAL_ON_VALID_PAYMENT:
            logger.info("CREATE_DEAL_ON_VALID_PAYMENT=true. Se intentará crear deal.")

            if not contacto_bitrix_valido(contact):
                logger.error("No se crea deal porque no hay contacto válido con id.")
                raise HTTPException(
                    status_code=404,
                    detail={
                        "ok": False,
                        "message": "No se encontró contacto válido en Bitrix para asociar el deal.",
                        "lookup_criteria": criteria.__dict__,
                    },
                )

            deal_result = await crear_deal_resultado_llamada_ia(
                variables_entrada=input_variables,
                variables_salida=output_vars,
                contacto=contact,)

            logger.info(
                "Deal creado desde webhook | %s",
                json.dumps(deal_result, ensure_ascii=False, default=str),
            )

            try:
                solicitud_pago = construir_solicitud_pago_desde_bitrix(
                    input_variables=input_variables,
                    output_vars=output_vars,
                    validation=validation,
                    contact=contact,
                    deal_result=deal_result,
                )

                contexto_pago = construir_contexto_auditoria_http(
                    request=request,
                    operacion="crear_orden_desde_bitrix",
                    payload_recibido=modelo_a_diccionario(solicitud_pago),
                )

                payment_order_result = await ServicioOrdenesPago().crear_orden_pago(
                    solicitud=solicitud_pago,
                    contexto_auditoria=contexto_pago,
                )

                logger.info(
                    "Orden Payvalida creada desde webhook Bitrix | %s",
                    json.dumps(payment_order_result, ensure_ascii=False, default=str),
                )

                enlace_pago = payment_order_result.get("enlace_pago")
                item_deal_creado = (
                    deal_result.get("item")
                    if isinstance(deal_result, dict) and isinstance(deal_result.get("item"), dict)
                    else deal_result
                )
                id_deal_creado = item_deal_creado.get("id") if isinstance(item_deal_creado, dict) else None
                if enlace_pago and id_deal_creado:
                    bitrix_link_update = await actualizar_link_pago_en_deal_bitrix(
                        id_deal=id_deal_creado,
                        enlace_pago=enlace_pago,
                    )

            except ErrorValidacionPayvalida as exc:
                logger.error("Validación Payvalida falló después de crear deal | %s", exc.mensaje)
                raise HTTPException(
                    status_code=422,
                    detail={
                        "ok": False,
                        "deal_created": True,
                        "deal": deal_result,
                        "payment_order_created": False,
                        "tipo_error": "validacion_payvalida",
                        "error": exc.a_respuesta(),
                    },
                )
            except ErrorProveedorPago as exc:
                logger.error("Proveedor Payvalida falló después de crear deal | %s", str(exc))
                raise HTTPException(
                    status_code=502,
                    detail={
                        "ok": False,
                        "deal_created": True,
                        "deal": deal_result,
                        "payment_order_created": False,
                        "tipo_error": "error_proveedor_payvalida",
                        "mensaje": str(exc),
                    },
                )
            except ValueError as exc:
                logger.error("Mapeo Bitrix a Payvalida falló después de crear deal | %s", str(exc))
                raise HTTPException(
                    status_code=422,
                    detail={
                        "ok": False,
                        "deal_created": True,
                        "deal": deal_result,
                        "payment_order_created": False,
                        "tipo_error": "payload_payvalida_incompleto",
                        "mensaje": str(exc),
                    },
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
            "payment_order_created": payment_order_result is not None,
            "payment_order": payment_order_result,
            "bitrix_link_updated": bitrix_link_update is not None,
            "bitrix_link_update": bitrix_link_update,
        }

    except ErrorBitrixAPI as exc:
        logger.error(
            "Error Bitrix en /webhooks/call-completed-bitrix | %s", exc.mensaje
        )
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.variantes_telefono),
            exc.mensaje,
        )
        raise HTTPException(
            status_code=502,
            detail=construir_error_webhook_debug(
                tipo_error="error_bitrix",
                mensaje=exc.mensaje,
                status_code=502,
                input_variables=input_variables,
                output_vars=output_vars,
                contexto_agente=contexto_agente,
                criteria=criteria,
                validation=locals().get("validation"),
                contact=locals().get("contact"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order_result"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "mensaje": exc.mensaje,
                    "metodo_bitrix": exc.metodo,
                    "codigo_estado_bitrix": exc.codigo_estado,
                    "respuesta_bitrix": exc.respuesta,
                },
            ),
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.error("Error interno en /webhooks/call-completed-bitrix | %s", str(exc))
        logger.error(traceback.format_exc())
        await error_notify(
            method_name,
            str(criteria.cedula or criteria.correo or criteria.variantes_telefono),
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=construir_error_webhook_debug(
                tipo_error="error_interno",
                mensaje=str(exc),
                status_code=500,
                input_variables=input_variables,
                output_vars=output_vars,
                contexto_agente=contexto_agente,
                criteria=criteria,
                validation=locals().get("validation"),
                contact=locals().get("contact"),
                deal_result=locals().get("deal_result"),
                payment_order_result=locals().get("payment_order_result"),
                bitrix_link_update=locals().get("bitrix_link_update"),
                error={
                    "mensaje": str(exc),
                    "traceback": traceback.format_exc(),
                },
            ),
        )
    
    

