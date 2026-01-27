from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from utils.notify_error import error_notify, get_cached_logs,send_log_email, send_log_telegram,info_notify
from utils.enviar_correo_IA import procesar_webhook_renovacion, procesar_webhook_webinar, procesar_llamada_renovacion_Y_refinanciamiento
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
from models.models import WebhookPayload
import traceback
import httpx
import logging
import asyncio
from fastapi import Request
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from utils.config import settings
import re
import unicodedata
import aiomysql
        
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
    fecha_inicio: Optional[str] = None # Ej: "2026-01-09"
    fecha_fin: Optional[str] = None    # Ej: "2026-01-10"
    
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
                "offset": 0
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
            method_name="startup_server",
            client_id="system",
            info_message=message
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
            method_name="shutdown_server",
            client_id="system",
            error_message=message
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
                method_name="server_reload",
                client_id="system",
                info_message=message
            )
            logger.info("Servidor reiniciado y notificaciones enviadas")
    except Exception as e:
        logger.error(f"Error al enviar notificación de reinicio: {e}")
    
    return await call_next(request)

# Mensajes amigables para el cliente
MENSAJES_CLIENTE = {
    "error_conexion": "Lo sentimos, en este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
    "error_datos": "Los valores ingresados no son válidos. Por favor verifica que el monto y la cuota inicial sean números válidos.",
    "error_servicio": "En este momento nuestro servicio no está disponible. Por favor intenta más tarde.",
    "error_simulacion": "No pudimos completar la simulación de tu crédito. Por favor verifica los datos e intenta nuevamente.",
    "cuotas_no_encontradas": "No se pudo obtener el detalle de las cuotas para tu crédito. Por favor intenta nuevamente.",
    "error_general": "Hubo un problema al procesar tu solicitud. Por favor intenta nuevamente más tarde."
}

MENSAJES_USUARIO = {
    "valor_invalido": "El monto ingresado no es válido. Por favor ingresa un valor numérico, por ejemplo: 2500000 o $2.500.000",
    "linea_no_existe": "Lo sentimos, el producto financiero seleccionado no está disponible en este momento. Por favor intenta nuevamente más tarde.",
    "semestre_invalido": "El semestre ingresado no es válido. Por favor selecciona una opción entre 'primer semestre' y 'décimo semestre'.",
    "plazo_invalido": "El plazo seleccionado no es válido. Por favor escoge entre 1 y 6 meses.",
    "error_conexion": "En este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
    "error_calculo": "Hubo un problema al calcular tu financiamiento. Por favor verifica los valores ingresados e intenta nuevamente.",
    "datos_faltantes": "Por favor completa todos los campos requeridos para calcular tu financiamiento."
}

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# Variables de configuración usadas en los endpoints principales
API_URL = settings.API_URL
ORG_ID = settings.ORG_ID
PAYABLE_URL = settings.PAYABLE_URL
GET_PAYABLE_URL = settings.GET_PAYABLE_URL
ASSISTANCE_URL = settings.ASSISTANCE_URL

# --- Sistema de Cache en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

# Normaliza nombres/titulos a un slug estable (sin tildes, minusculas y sin simbolos)
def slugify_nombre(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFD", value)
    ascii_str = ''.join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    ascii_str = re.sub(r"[^a-zA-Z0-9\s-]", "", ascii_str)
    ascii_str = re.sub(r"\s+", " ", ascii_str).strip().lower()
    return ascii_str.replace(" ", "-")

# Modelo Pydantic para el payload sin validaciones estrictas
from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any

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

    @field_validator('principal', 'initialFee', mode='before')
    @classmethod
    def validate_floats(cls, v):
        """Convierte strings a float si es necesario"""
        if isinstance(v, str):
            if v.strip() == "":
                raise ValueError("El campo no puede estar vacío")
            try:
                return float(v.replace(',', '.').strip())
            except ValueError:
                raise ValueError(f"No se puede convertir '{v}' a número decimal")
        return v

    @field_validator('time', 'paymentFrequency', mode='before')
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

    @field_validator('disbursementMethod', mode='before')
    @classmethod
    def validate_disbursement(cls, v):
        """Valida disbursementMethod, rechaza strings vacíos"""
        if v == "" or v is None:
            return None
        if isinstance(v, str):
            return v.strip() if v.strip() else None
        return v

    @field_validator('creditLineId', mode='before')
    @classmethod
    def validate_creditlineId(cls, v):
        """Valida creditLineId, no puede estar vacío"""
        if not v or (isinstance(v, str) and v.strip() == ""):
            raise ValueError("creditLineId no puede estar vacío")
        return str(v).strip()

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
        "detalles": "Por favor verifica el código y vuelve a intentar."
    },
    "ExpiredCode": {
        "mensaje": "El código TOTP ha expirado.",
        "detalles": "Solicita un nuevo código e intenta nuevamente."
    },
    "MaxAttemptsExceeded": {
        "mensaje": "Has excedido el número máximo de intentos.",
        "detalles": "Por seguridad, tu sesión ha sido bloqueada temporalmente. Intenta más tarde."
    },
    "UserNotFound": {
        "mensaje": "No se encontró el usuario.",
        "detalles": "Por favor verifica los datos e intenta nuevamente."
    },
    "UnauthorizedRequest": {
        "mensaje": "No tienes permiso para realizar esta acción.",
        "detalles": "Por favor contacta con soporte técnico."
    }
}

#confirmar codigo totp realizando un bucle para confirmar en cada intento
#recibe el codigo, id_debtor, id_asistance
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
                await error_notify(method_name, client_id, "No se pudo obtener el token de acceso")
                return JSONResponse(
                    status_code=401,
                    content={
                        "estado": "error",
                        "codigo_error": "TokenError",
                        "mensaje": "No se pudo autenticar la solicitud.",
                        "detalles": "Por favor intenta nuevamente en unos minutos."
                    }
                )

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": access_token
            }

            # Construir payload correctamente con interpolación
            payload_totp = {"totp": codigo_totp}  # payload para confirmacion TOTP
            ext_client_totp = None  # cliente externo opcional para TOTP
            try:  # intenta cargar configuracion desde BD
                ext_client_totp = await ExternalClient.from_code("KUENTA_TOTP_APPROVE")  # codigo de servicio en BD
            except ValueError:  # si no existe en BD
                ext_client_totp = None  # fallback a settings
            
            # validacion de la URL de asistencia, SI ext_client_totp tiene URL, usa esa, sino usa ASSISTANCE_URL de settings    
            assistance_base_url = ext_client_totp.url if ext_client_totp and ext_client_totp.url else ASSISTANCE_URL  # base desde BD o settings
            assistance_base_url = assistance_base_url.rstrip("/") + "/"  # asegura slash final
            assistance_url_template = f"{assistance_base_url}{{id_debtor}}/assistances/{{id_asistance}}/approve"  # plantilla con placeholders, se concatena la url base con el path dinamico
            assistance_url = assistance_url_template.format(  # construye URL final, asignando parametros
                id_debtor=ConfirmarTOTPRequest.id_debtor,  # parametro id_debtor
                id_asistance=ConfirmarTOTPRequest.id_asistance,  # parametro id_asistance
            )  # URL final de confirmacion

            logger.info(f"Iniciando confirmación de TOTP para deudor: {ConfirmarTOTPRequest.id_debtor} \n con url: {assistance_url}")

            # ===== REINTENTOS CON MANEJO INTELIGENTE DE ERRORES =====

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    logger.info(f"Intento {attempt}/{MAX_RETRIES} para confirmar TOTP")

                    response_data = {}  # contenedor de respuesta
                    
                    if ext_client_totp:  # usa servicio externo si existe
                        ext_client_totp.set_headers(headers)  # se le asigna los headers para la peticion
                        ext_client_totp.set_url(assistance_url)  # URL final de TOTP
                        ext_client_totp.set_body(payload_totp)  # payload de TOTP
                        
                        # imprimir configuracion del cliente externo para debug
                        logger.info(f"Configuración ExternalClient TOTP para ejecutar la peticion: {ext_client_totp.__dict__}")
                        
                        response = await ext_client_totp.run()  # ejecuta peticion externa
                        
                        if not isinstance(response, dict):  # valida tipo de respuesta
                            await error_notify(
                                method_name,
                                client_id,
                                "Respuesta invalida del servicio externo TOTP"
                            )
                            raise Exception("Respuesta invalida del servicio externo")  # error si no es dict
                        
                        status_code = response.get("status", 500)  # status HTTP simulado

                        response_data = response.get("data") or {}  # data de respuesta
                        if not isinstance(response_data, dict):  # valida data
                            response_data = {}  # fallback a dict vacio
                        response = type("Resp", (), {"status_code": status_code})()  # wrapper para status_code
                    else:  # fallback a httpx directo
                        response = await client.post(  # request httpx original
                            assistance_url,  # URL de confirmacion
                            headers=headers,  # headers con token
                            json=payload_totp  # payload TOTP
                        )
                        status_code = response.status_code  # status HTTP real
                        # Intentar parsear la respuesta como JSON
                        try:
                            response_data = response.json()  # parse JSON
                        except Exception as json_err:
                            logger.error(f"Error al parsear JSON: {json_err}")
                            #insertar log del error de parseo en la base de datos
                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"Error al parsear JSON en intento {attempt}: {str(json_err)}",
                                http_code=status_code,
                                tipo="error"
                            )
                            
                            response_data = {}

                    logger.info(f"Respuesta de API (status {response.status_code}): {response_data}")

                    # ===== CASO EXITOSO: status 200 y "status": "success" =====
                    if response.status_code == 200:
                        if response_data.get("status") == "success":
                            logger.info(f"TOTP confirmado exitosamente en intento {attempt}")
                            await info_notify(
                                method_name=method_name,
                                client_id=client_id,
                                info_message=f"TOTP confirmado exitosamente"
                            )
                            return JSONResponse(
                                status_code=200,
                                content={
                                    "estado": "success",
                                    "mensaje": "Código TOTP confirmado exitosamente.",
                                    "data": response_data.get("data", {}),
                                    "detalles": "Tu identidad ha sido verificada correctamente."
                                }
                            )
                        else:
                            error_traceback = traceback.format_exc()
                            # Status 200 pero "status": "fail" en la respuesta
                            error_code = response_data.get("data", {}).get("code", "UnknownError")
                            error_msg = response_data.get("data", {}).get("error", "Error desconocido")
                            logger.warning(f"TOTP rechazado (status 200 pero fail): {error_code} - {error_msg}")
                            last_error_data = response_data
                            last_error_type = "STATUS_200_BUT_FAIL"
                            
                            #insertar log del error en la base de datos
                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"TOTP rechazado en intento {attempt}: {error_code} - {error_msg}, error completo: {last_error_data}",
                                http_code=response.status_code,
                                tipo="error",
                                traceback_str=error_traceback
                            )

                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(RETRY_DELAY * attempt)
                                continue
                            else:
                                # Agotados los reintentos
                                return await _handle_totp_error(error_code, error_msg, method_name, client_id)

                    # ===== CASO DE ERROR CON DETALLES: status 412 u otro error =====
                    elif response.status_code in [400, 412, 422]:
                        error_code = response_data.get("data", {}).get("code", "InvalidRequest")
                        error_msg = response_data.get("data", {}).get("error", "Error en la solicitud")
                        logger.warning(f"Intento {attempt}: Error {response.status_code} - {error_code}: {error_msg}")
                        last_error_data = response_data
                        last_error_type = f"HTTP_{response.status_code}_CLIENT_ERROR"

                        # Estos errores no se reintenten (son errores del cliente)
                        await error_notify(
                            method_name,
                            client_id,
                            f"Error {response.status_code} al confirmar TOTP: {error_code} - {error_msg}"
                        )
                        return await _handle_totp_error(error_code, error_msg, method_name, client_id)

                    # ===== CASO DE ERROR DEL SERVIDOR: 500+ =====
                    elif response.status_code >= 500:
                        logger.warning(f"Intento {attempt}: Error del servidor ({response.status_code})")
                        last_error_data = response_data
                        last_error_type = f"HTTP_{response.status_code}_SERVER_ERROR"

                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue
                        else:
                            await error_notify(
                                method_name,
                                client_id,
                                f"Error del servidor después de {MAX_RETRIES} intentos: {response.status_code}"
                            )
                            return JSONResponse(
                                status_code=503,
                                content={
                                    "estado": "error",
                                    "codigo_error": "ServerError",
                                    "mensaje": "El servicio no está disponible en este momento.",
                                    "detalles": "Por favor intenta nuevamente más tarde."
                                }
                            )

                    # ===== OTROS CASOS DE ERROR HTTP =====
                    else:
                        logger.warning(f"Intento {attempt}: Error HTTP inesperado ({response.status_code})")
                        last_error_data = response_data
                        last_error_type = f"HTTP_{response.status_code}_UNEXPECTED_ERROR"

                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * attempt)
                            continue
                        else:
                            await error_notify(
                                method_name,
                                client_id,
                                f"Error HTTP inesperado: {response.status_code}"
                            )
                            return JSONResponse(
                                status_code=500,
                                content={
                                    "estado": "error",
                                    "codigo_error": "UnknownError",
                                    "mensaje": "Ocurrió un error inesperado.",
                                    "detalles": "Por favor intenta nuevamente más tarde."
                                }
                            )

                # ===== MANEJO DE EXCEPCIONES DE CONEXIÓN =====
                except httpx.ConnectTimeout:
                    logger.warning(f"Intento {attempt}: Timeout de conexión")
                    last_error_type = "EXCEPTION_CONNECT_TIMEOUT"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(f"[{last_error_type}] Timeout de conexión agotado después de {MAX_RETRIES} intentos")
                        await error_notify(method_name, client_id, f"Timeout de conexión después de {MAX_RETRIES} intentos")
                        return JSONResponse(
                            status_code=503,
                            content={
                                "estado": "error",
                                "codigo_error": "ConnectionTimeout",
                                "mensaje": "No se pudo conectar con el servicio.",
                                "detalles": "Por favor intenta nuevamente más tarde."
                            }
                        )

                except httpx.ReadTimeout:
                    logger.warning(f"Intento {attempt}: Timeout de lectura")
                    last_error_type = "EXCEPTION_READ_TIMEOUT"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(f"[{last_error_type}] Timeout de lectura agotado después de {MAX_RETRIES} intentos")
                        await error_notify(method_name, client_id, f"Timeout de lectura después de {MAX_RETRIES} intentos")
                        return JSONResponse(
                            status_code=503,
                            content={
                                "estado": "error",
                                "codigo_error": "ReadTimeout",
                                "mensaje": "El servicio tardó demasiado en responder.",
                                "detalles": "Por favor intenta nuevamente más tarde."
                            }
                        )

                except httpx.ConnectError as e:
                    logger.warning(f"Intento {attempt}: Error de conexión: {e}")
                    last_error_type = "EXCEPTION_CONNECT_ERROR"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(f"[{last_error_type}] Error de conexión agotado después de {MAX_RETRIES} intentos: {str(e)}")
                        await error_notify(method_name, client_id, f"Error de conexión después de {MAX_RETRIES} intentos")
                        return JSONResponse(
                            status_code=502,
                            content={
                                "estado": "error",
                                "codigo_error": "ConnectionError",
                                "mensaje": "No se pudo alcanzar el servicio.",
                                "detalles": "Por favor intenta nuevamente en unos minutos."
                            }
                        )

                except Exception as e:
                    logger.error(f"Intento {attempt}: Error inesperado: {str(e)}", exc_info=True)
                    last_error_type = f"EXCEPTION_GENERAL_{type(e).__name__}"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    else:
                        logger.error(f"[{last_error_type}] Error general agotado después de {MAX_RETRIES} intentos: {str(e)}")
                        await error_notify(method_name, client_id, f"Error inesperado después de {MAX_RETRIES} intentos: {str(e)}")
                        return JSONResponse(
                            status_code=500,
                            content={
                                "estado": "error",
                                "codigo_error": "InternalError",
                                "mensaje": "Ocurrió un error interno.",
                                "detalles": "Por favor intenta nuevamente más tarde."
                            }
                        )

            # ===== SI SE AGOTAN TODOS LOS REINTENTOS =====
            logger.error(f"Se agotaron todos los reintentos ({MAX_RETRIES}) para confirmar TOTP")
            
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
            
            await error_notify(method_name, client_id, f"Se agotaron todos los reintentos para confirmar TOTP")
            return JSONResponse(
                status_code=500,
                content={
                    "estado": "error",
                    "codigo_error": "MaxRetriesExceeded",
                    "mensaje": "No se pudo confirmar el código después de varios intentos.",
                    "detalles": "Por favor solicita un nuevo código e intenta nuevamente."
                }
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
            traceback_str=error_traceback
        )
        await error_notify(method_name, client_id, f"Error general en confirmar_totp: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Error interno al confirmar TOTP"
        )


# ===== FUNCIÓN AUXILIAR PARA MANEJAR ERRORES TOTP =====
async def _handle_totp_error(error_code: str, error_msg: str, method_name: str, client_id: str) -> JSONResponse:
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
        "UnauthorizedRequest": 403
    }
    http_status = status_code_map.get(error_code, 412)

    # Obtener mensaje personalizado o usar el de la API
    error_info = ERRORES_TOTP.get(error_code, {
        "mensaje": f"Error al confirmar el código: {error_code}",
        "detalles": "Por favor intenta nuevamente o contacta con soporte."
    })
    error_traceback = traceback.format_exc()
    logger.error(f"Error TOTP - Código: {error_code}, Mensaje: {error_msg}")
    await error_notify(method_name, client_id, f"Error TOTP: {error_code} - {error_msg}")
    await insertar_log(
        method_name=method_name,
        client_id=client_id,
        error_message=f"Error TOTP: {error_code} - {error_msg}",
        http_code=http_status,
        tipo="error",
        traceback_str=error_traceback
    )
    return JSONResponse(
        status_code=http_status,
        content={
            "estado": "error",
            "codigo_error": error_code,
            "mensaje": error_info["mensaje"],
            "detalles": error_info["detalles"],
            "error_original": error_msg
        }
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
    TIMEOUT = 15     # timeout en segundos por solicitud

    method_name = "product-lines"
    parent_id_notify_error = f"parent_id para la busqueda del la linea={parent_id}"
    target_slug = slugify_nombre(name) if name else ""

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            access_token = await obtener_token(client)
            if not access_token:
                msg = "No se pudo obtener el token de acceso"
                await error_notify(method_name, parent_id_notify_error, msg)
                return {
                    "estado": "error",
                    "mensaje": MENSAJES_CLIENTE["error_conexion"],
                    "detalles_usuario": "No se pudo obtener el token de acceso. Por favor intenta nuevamente mas tarde."
                }

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": f"{access_token}"
            }

            # Servicio externo para listar lineas (configurado en BD)
            ext_client = None
            try:
                ext_client = await ExternalClient.from_code("KUENTA_LINES_LIST")
            except ValueError:
                ext_client = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if ext_client:
                        ext_client.set_headers(headers)
                        if not ext_client.url:
                            ext_client.set_url(API_URL)
                        response = await ext_client.run()
                        if not isinstance(response, dict):
                            raise Exception("Respuesta invalida del servicio externo")
                        status_code = response.get("status", 500)
                        if not isinstance(status_code, int):
                            raise Exception("Respuesta de servicio sin status valido")
                        if status_code >= 400:
                            error_traceback = traceback.format_exc()
                            error_detail = response.get("data")
                            logger.error(f"Error HTTP {status_code} en API externa: {error_detail}")
                            await error_notify(
                                method_name,
                                parent_id_notify_error,
                                f"Error en API externa: {error_detail} error capturado: {status_code}"
                            )
                            await insertar_log(
                                method_name=method_name,
                                client_id=parent_id_notify_error,
                                error_message=f"Error en API externa: {error_detail} error capturado: {status_code}",
                                http_code=status_code,
                                tipo="error",
                                traceback_str=error_traceback
                            )
                            if 500 <= status_code < 600 and attempt < MAX_RETRIES:
                                await asyncio.sleep(RETRY_DELAY * attempt)
                                continue
                            return {
                                "estado": "error",
                                "mensaje": MENSAJES_CLIENTE["error_servicio"],
                                "detalles_usuario": "El servicio externo no esta disponible. Por favor intenta mas tarde."
                            }

                        data = response.get("data") or {}
                        if not isinstance(data, dict):
                            data = {}
                    else:
                        response = await client.get(API_URL, headers=headers)
                        response.raise_for_status()
                        data = response.json()
                    #logger.info(f"Respuesta recibida de API para buscar la linea: {data}")
                    lines = data.get("data", {}).get("lines", [])

                    candidates = []
                    for line in lines:
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
                        slug_match = bool(target_slug) and (slug_name == target_slug or slug_title == target_slug)

                        if not (parent_match or slug_match):
                            continue

                        candidates.append({
                            "line": line,
                            "matched_by": "parentId" if parent_match else "slug"
                        })

                    # Coincidencias parciales para rescate (si nada exacto)
                    if not candidates and target_slug:
                        for line in lines:
                            if line.get("archived"):
                                continue
                            if entity_id and line.get("entityID") != entity_id:
                                continue
                            slug_name = slugify_nombre(line.get("name", ""))
                            slug_title = slugify_nombre(line.get("title", ""))
                            if target_slug in slug_name or target_slug in slug_title:
                                candidates.append({"line": line, "matched_by": "partial-slug"})

                    if not candidates:
                        msg = f"No se encontro la linea con parentId {parent_id} ni slug {target_slug}"
                        await error_notify(method_name, parent_id_notify_error, msg)
                        sugerencias = [slugify_nombre(l.get("name", "")) for l in lines][:10]
                        #insertar log del error en la base de datos
                        await insertar_log(
                            method_name=method_name,
                            client_id=parent_id_notify_error,
                            error_message=msg,
                            http_code=404,
                            tipo="error"
                        )
                        
                        return {
                            "estado": "error",
                            "mensaje": MENSAJES_CLIENTE["error_servicio"],
                            "detalles_usuario": "No se encontro la linea de producto solicitada. Verifica el nombre o intenta mas tarde.",
                            "sugerencias": sugerencias
                        }

                    def parse_updated(val: str):
                        try:
                            return datetime.fromisoformat(val.replace("Z", "+00:00"))
                        except Exception:
                            return datetime.min

                    candidates.sort(
                        key=lambda item: (
                            parse_updated(item["line"].get("updatedAt")),
                            item["line"].get("version", 0)
                        ),
                        reverse=True
                    )

                    selected = candidates[0]
                    line = selected["line"]

                    logger.info(f"Linea encontrada via {selected['matched_by']}: {line}")
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

                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                    logger.warning(f"Intento {attempt}/{MAX_RETRIES} fallido por timeout o conexion: {e}")
                    await error_notify(method_name, parent_id_notify_error, f"Intento {attempt} error capturado: {e}")
                    #insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=parent_id_notify_error,
                        error_message=f"Intento {attempt} error capturado: {e}",
                        http_code=0,
                        tipo="error"
                    )
                    if attempt == MAX_RETRIES:
                        return {
                            "estado": "error",
                            "mensaje": MENSAJES_CLIENTE["error_conexion"],
                            "detalles_usuario": "No se pudo conectar con el servicio externo. Por favor intenta nuevamente mas tarde."
                        }
                    await asyncio.sleep(RETRY_DELAY * attempt)

                except httpx.HTTPStatusError as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"Error HTTP {e.response.status_code} en API externa: {e.response.text}")
                    await error_notify(method_name, parent_id_notify_error, f"Error en API externa: {e.response.text} error capturado: {e}")
                    #insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=parent_id_notify_error,
                        error_message=f"Error en API externa: {e.response.text} error capturado: {e}",
                        http_code=e.response.status_code,
                        tipo="error",
                        traceback_str=error_traceback
                    )
                    if 500 <= e.response.status_code < 600 and attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    return {
                        "estado": "error",
                        "mensaje": MENSAJES_CLIENTE["error_servicio"],
                        "detalles_usuario": "El servicio externo no esta disponible. Por favor intenta mas tarde."
                    }

            msg = "Error persistente al consultar API externa"
            await error_notify(method_name, parent_id_notify_error, msg)
            return {
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_conexion"],
                "detalles_usuario": "No se pudo obtener respuesta del servicio externo. Por favor intenta mas tarde."
            }

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error general en webhook_product_lines: {e}")
        await error_notify(method_name, parent_id_notify_error, f"Error general: {e}")
        await insertar_log(
            method_name=method_name,
            client_id=parent_id_notify_error,
            error_message=f"Error general: {e}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback
        )
        return {
            "estado": "error",
            "mensaje": MENSAJES_CLIENTE["error_general"],
            "detalles_usuario": "Ocurrio un error inesperado. Por favor intenta nuevamente mas tarde."
        }


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
            7: "Económico"
        }.get(category, "Otro")
        
        # Crear información del campo
        field_info = {
            "label": label,
            "type": field.get("field", {}).get("type", "text"),
            "id": field.get("ID")
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
        "by_category": by_category
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
            7: "Económico"
        }.get(category, "Otro")
        
        field_info = {
            "label": label,
            "type": field.get("field", {}).get("type", "text"),
            "id": field.get("ID")
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
        "by_category": by_category
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
                "Authorization": token
            }

            # Servicio externo para crear payable (configurado en BD)
            ext_client_post = None
            try:
                # Intentar cargar configuración de servicio externo consultando la base de datos
                ext_client_post = await ExternalClient.from_code("KUENTA_PAYABLE_CREATE")
            except ValueError:
                ext_client_post = None

            # ===== REINTENTOS PARA POST PAYABLE =====
            max_retries = 3
            response_credit_id = None
            last_error_response = None
            
            for attempt in range(max_retries):
                
                try:
                    logger.info(f"Intento {attempt+1}/{max_retries}: POST a {PAYABLE_URL}")
                    
                    response_data = {}
                    
                    # valida las configuraciones del servicio externo que se obtuvieron de la base de datos
                    if ext_client_post:
                        # asigna los headers ya que las variables sensibles no se guardan en la base de datos
                        ext_client_post.set_headers(headers)
                        # asigna la url si no está definida en la configuración del servicio externo
                        if not ext_client_post.url:
                            ext_client_post.set_url(PAYABLE_URL)
                        # asigna el payload
                        ext_client_post.set_body(new_payload)
                        
                        # ejecuta el servicio externo
                        response = await ext_client_post.run()
                        
                        logger.info(f"   Response completo del servicio externo: {str(response)}")
                        
                        if not isinstance(response, dict):
                            raise Exception("Respuesta invalida del servicio externo")
                        status_code = response.get("status", 500)
                        response_data = response.get("data") or "no se accedio a data"
                        if not isinstance(response_data, dict):
                            response_data = {}
                    else:
                        response = await client.post(
                            PAYABLE_URL,
                            json=new_payload,
                            headers=headers
                        )
                        response_data = response.json()
                        status_code = response.status_code

                    #logger.info(f"   Response Data: {str(response_data)}")
                    
                    # ===== CASO 1: ÉXITO (201) =====
                    if status_code == 201 or status_code == 200:
                        #logger.info("Payable creado exitosamente (HTTP 201)")
                        credit = response_data.get("data", {}).get("credit", {})
                        
                        #logger.info(f"Respuesta completa de Kuenta: {str(credit)}")
                        response_credit_id = credit.get("ID")
                        #logger.info(f"ID Crédito: {response_credit_id}")
                        logger.info(f"Response: HTTP {status_code}, \n ID del credito: {response_data.get('data', {}).get('credit', {}).get('ID', 'no se pudo obtener ID')}")
                        break  # Salir del loop de reintentos
                    
                    # ===== CASO 2: ERROR 400 =====
                    elif status_code == 400:
                        error_traceback = traceback.format_exc()
                        error_response = response_data
                        last_error_response = error_response
                        
                        error_code = error_response.get("data", {}).get("code")
                        error_message = error_response.get("data", {}).get("message", "Unknown")
                        missing_fields = error_response.get("data", {}).get("missingFields", [])
                        
                        logger.error(f"HTTP 400 Error: code={error_code}")
                        logger.error(f"   Mensaje: {error_message}")
                        await error_notify(method_name, client_id, f"Error 400 en API Kuenta, error completo: {error_response}, code={error_code}, message={error_message}")
                        #insertar log del error en la base de datos
                        await insertar_log(
                            method_name=method_name,
                            client_id=client_id,
                            error_message=f"Error 400 en API Kuenta, error completo: {error_response}, code={error_code}, message={error_message}",
                            http_code=400,
                            tipo="error",
                            traceback_str=error_traceback
                        )
                        # ===== SUB-CASO: PERFIL INCOMPLETO =====
                        if error_code == "IncompleteProfile":
                            logger.error(f"PERFIL INCOMPLETO DETECTADO")
                            
                            # Extraer información legible
                            missing_info = extract_missing_fields_info(missing_fields)
                            
                            logger.error(f"   Total campos faltantes: {missing_info['total']}")
                            logger.error(f"   Obligatorios: {missing_info['required_count']}")
                            logger.error(f"   Opcionales: {missing_info['optional_count']}")
                            logger.error(f"   Campos: {', '.join(missing_info['required_labels'])}")
                            
                            # Notificar
                            error_msg = f"Perfil incompleto. Faltan {missing_info['required_count']} campos obligatorios: {', '.join(missing_info['required_labels'])} error_completo: {error_response}"
                            await error_notify(method_name, client_id, error_msg)
                            #insertar log del error en la base de datos
                            await insertar_log(
                                method_name=method_name,
                                client_id=client_id,
                                error_message=f"Perfil incompleto en API Kuenta, error completo: {error_response}, code={error_code}, message={error_message}",
                                http_code=400,
                                tipo="error"
                            )

                            # NO reintentar - es un problema del cliente
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "error": "IncompleteProfile",
                                    "message": "El perfil del cliente está incompleto en Kuenta",
                                    "required_fields_count": missing_info['required_count'],
                                    "required_fields": missing_info['required_labels'],
                                    "optional_fields": missing_info['optional_labels'],
                                    "by_category": missing_info['by_category'],
                                    "instruction": "Complete los datos faltantes en la plataforma Kuenta"
                                }
                            )
                        
                        # ===== SUB-CASO: OTRO ERROR 400 =====
                    elif status_code == 400:
                        error_response = response_data
                        last_error_response = error_response
                        
                        error_code = error_response.get("data", {}).get("code")
                        error_message = error_response.get("data", {}).get("message", "Unknown")
                        missing_fields = error_response.get("data", {}).get("missingFields", [])
                        # Notificar si es estatus 400 no manejado
                        await error_notify(method_name, client_id, 
                            f"Error 400 en API Kuenta, error completo: {error_response}, code={error_code}, message={error_message} error completo: {last_error_response}")
                    
                    # ===== CASO 2B: ERROR 403 (FORBIDDEN - TOKEN INVÁLIDO) =====
                    elif status_code == 403:
                        error_traceback = traceback.format_exc()
                        error_response = response_data
                        last_error_response = error_response
                        
                        logger.error(f"HTTP 403 Forbidden: Token inválido o expirado")
                        logger.error(f"   Respuesta: {error_response}")
                        await error_notify(method_name, client_id, 
                            f"Error 403 en API Kuenta: Token inválido o sin autorización. Error completo: {error_response}")
                        
                        # Insertar log del error en la base de datos
                        await insertar_log(
                            method_name=method_name,
                            client_id=client_id,
                            error_message=f"Error 403 en API Kuenta: Token inválido o sin autorización. Respuesta: {error_response}",
                            http_code=403,
                            tipo="error",
                            traceback_str=error_traceback
                        )
                        
                        # Intentar obtener un nuevo token en el siguiente intento
                        if attempt < max_retries - 1:
                            logger.info(f"Obteniendo nuevo token e intentando nuevamente...")
                            try:
                                token = await obtener_token(client)
                                headers["Authorization"] = token
                                logger.info("Token renovado exitosamente")
                            except Exception as token_error:
                                logger.error(f"Error al renovar token: {str(token_error)}")
                                await error_notify(method_name, client_id, 
                                    f"Error al renovar token de autorización: {str(token_error)}")
                            
                            wait_time = 2 ** attempt
                            logger.info(f"Reintentando en {wait_time}s con nuevo token...")
                            await asyncio.sleep(wait_time)
                        else:
                            # Si falló en el último intento, retornar error de autorización
                            logger.error(f"Fallo de autorización tras {max_retries} intentos")
                            raise HTTPException(
                                status_code=401,  # Unauthorized - para que el cliente sepa que hay problema de credenciales
                                detail={
                                    "error": "UnauthorizedToken",
                                    "message": "No se pudo obtener autorización para acceder a la API",
                                    "instruction": "Por favor contacta con soporte técnico para verificar las credenciales de la API",
                                    "last_error": error_response
                                }
                            )
                        continue
                    
                    # ===== CASO 3: OTROS ERRORES (500, 503, etc) =====
                    else:
                        logger.error(f"HTTP {status_code}")
                        error_text = str(response_data) if ext_client_post else response.text
                        logger.error(f"   Respuesta: {error_text[:200]}")
                        
                        await error_notify(method_name, client_id, 
                            f"Error HTTP {status_code} en API Kuenta, error completo: {error_text[:500]},error completo: {last_error_response}")
                        
                        # Reintentar
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt
                            logger.info(f"Reintentando en {wait_time}s...")
                            await asyncio.sleep(wait_time)
                        continue
                
                except httpx.TimeoutException as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"Intento {attempt+1}:TIMEOUT ({str(e)})")
                    await error_notify(method_name, client_id, "Timeout en API Kuenta, excepción: " + str(e))
                    # insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=client_id,
                        error_message=f"Timeout en API Kuenta, excepción: {str(e)}",
                        http_code=504,
                        tipo="error",
                        traceback_str=error_traceback
                    )
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        
                except httpx.HTTPStatusError as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"Intento {attempt+1}: Error HTTP {e.response.status_code}")
                    logger.error(f"   {e.response.text[:200]}")
                    await error_notify(method_name, client_id,f"intento {attempt+1}: Error HTTP en API Kuenta: " + e.response.text[:200]+"excepción: " + str(e))
                    # insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=client_id,
                        error_message=f"Error HTTP {e.response.status_code} en API Kuenta, excepción: {str(e)}",
                        http_code=e.response.status_code,
                        tipo="error",
                        traceback_str=error_traceback
                    )
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        
                except httpx.RequestError as e:
                    logger.error(f"Intento {attempt+1}: 🔌 Error de conexión ({str(e)})")
                    await error_notify(method_name, client_id, "Error de conexión en API Kuenta, excepción: " + str(e))
                    # insertar log del error en la base de datos
                    await insertar_log(
                        method_name=method_name,
                        client_id=client_id,
                        error_message=f"Error de conexión en API Kuenta, excepción: {str(e)}",
                        http_code=503,
                        tipo="error"
                    )
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.info(f"Reintentando en {wait_time}s...")
                        await asyncio.sleep(wait_time)
            
            # ===== VALIDAR ÉXITO DEL POST =====
            if response_credit_id is None:
                logger.error(f"FALLO: No se creó payable tras {max_retries} intentos")
                await error_notify(method_name, client_id, 
                    f"No se pudo crear payable tras {max_retries} intentos, error_completo: {last_error_response}")
                # insertar log del error en la base de datos
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=f"No se pudo crear payable tras {max_retries} intentos, error_completo: {last_error_response}",
                    http_code=502,
                    tipo="error"
                )
                
                # Retornar 502 SOLO si fue error de conexión
                raise HTTPException(
                    status_code=502,  # ← BAD GATEWAY (error del servidor)
                    detail={
                        "error": "ServiceUnavailable",
                        "message": f"No se pudo crear el payable tras {max_retries} intentos",
                        "last_error": last_error_response
                    }
                )
                
            #realizar consulta inmediata para obtener la simulación del crédito creado
            logger.info(f"Payable creado con ID: {response_credit_id}, procediendo a obtener simulación...") 
            
            # ===== LOOP DE REINTENTOS PARA GET SIMULACIÓN (MÁXIMO 2 INTENTOS) =====
            max_retries_simulacion = 2
            response_get_simulacion = None
            simulacion_data = {}
            status_code_simulacion = None
            last_error_simulacion = None
            ext_client_get = None
            
            for attempt_get in range(max_retries_simulacion):
                try:
                    # Intentar cargar configuración del servicio externo
                    try:
                        ext_client_get = await ExternalClient.from_code("KUENTA_PAYABLE_GET")
                        logger.info(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Configuración de servicio externo para GET simulación cargada desde BD")
                        
                        # Agregar el ID del crédito al path
                        ext_client_get.set_path(f"/{response_credit_id}")
                        ext_client_get.set_headers(headers)
                        
                        if not ext_client_get.url:
                            logger.error("La configuración del servicio externo para GET simulación no tiene URL definida")
                            raise ValueError("URL no definida en configuración")
                        
                        # Ejecutar request con servicio externo
                        response_get_simulacion = await ext_client_get.run()
                        
                        if not isinstance(response_get_simulacion, dict):
                            raise Exception("Respuesta invalida del servicio externo")
                        
                        status_code_simulacion = response_get_simulacion.get("status")
                        simulacion_data = response_get_simulacion.get("data") or "no se encontro la data de la peticion"
                        
                    except (ValueError, Exception) as e:
                        # Si falla el servicio externo, usar cliente HTTP directo
                        logger.warning(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Usando cliente HTTP directo para GET simulación")
                        response_get_simulacion = await client.get(
                            f"https://api.kuenta.co/v1/payables/{response_credit_id}", 
                            headers=headers
                        )
                        status_code_simulacion = response_get_simulacion.status_code
                        simulacion_data = response_get_simulacion.json()
                    
                    logger.info(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Status code de la simulación obtenido: {status_code_simulacion}")
                    
                    # Si el status es exitoso, salir del loop
                    if status_code_simulacion in [200, 201]:
                        logger.info(f"GET simulación exitoso en intento {attempt_get + 1}")
                        break
                    else:
                        # Si no es exitoso, guardar el error para reintentar
                        last_error_simulacion = simulacion_data
                        logger.warning(f"GET simulación falló con status {status_code_simulacion}, reintentando...")
                        
                        # Esperar 5 segundos antes de reintentar (solo si no es el último intento)
                        if attempt_get < max_retries_simulacion - 1:
                            logger.info(f"Esperando 5 segundos antes de reintentar GET simulación...")
                            await asyncio.sleep(5)
                        continue
                
                except httpx.HTTPStatusError as e:
                    last_error_simulacion = str(e.response.text)
                    logger.error(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Error HTTP {e.response.status_code} en GET simulación")
                    
                    if attempt_get < max_retries_simulacion - 1:
                        logger.info(f"Esperando 5 segundos antes de reintentar GET simulación...")
                        await asyncio.sleep(5)
                    continue
                
                except httpx.RequestError as e:
                    last_error_simulacion = str(e)
                    logger.error(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Error de conexión en GET simulación: {str(e)}")
                    
                    if attempt_get < max_retries_simulacion - 1:
                        logger.info(f"Esperando 5 segundos antes de reintentar GET simulación...")
                        await asyncio.sleep(5)
                    continue
                
                except Exception as e:
                    last_error_simulacion = str(e)
                    logger.error(f"Intento {attempt_get + 1}/{max_retries_simulacion}: Error general en GET simulación: {str(e)}")
                    
                    if attempt_get < max_retries_simulacion - 1:
                        logger.info(f"Esperando 5 segundos antes de reintentar GET simulación...")
                        await asyncio.sleep(5)
                    continue
            
            # Validar si la consulta GET fue exitosa
            if status_code_simulacion not in [200, 201] or not simulacion_data:
                logger.error(f"FALLO: No se obtuvo la simulación tras {max_retries_simulacion} intentos")
                await error_notify(method_name, client_id, 
                    f"Error al consultar simulación tras {max_retries_simulacion} intentos: {last_error_simulacion}")
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=f"Error al consultar simulación tras {max_retries_simulacion} intentos: {last_error_simulacion}",
                    http_code=status_code_simulacion or 502,
                    tipo="error"
                )
                raise HTTPException(
                    status_code=status_code_simulacion or 502,
                    detail={
                        "error": "SimulationRetrievalFailed",
                        "message": f"No se pudo obtener la simulación del crédito tras {max_retries_simulacion} intentos",
                        "last_error": last_error_simulacion
                    }
                )
            
            logger.info(f"Status code de la simulación: {status_code_simulacion} \n")
            
            # ===== PROCESAR RESPUESTA EXITOSA DE SIMULACIÓN =====
            try:
                # Extraer datos de crédito de la respuesta
                credits_data = simulacion_data.get("credit")
                
                #tipo de dato puede ser lista o diccionario
                logger.info(f"Tipo de dato de credits en la simulación: {type(credits_data)}")
                
                if isinstance(credits_data, list):
                    if not credits_data:
                        raise ValueError("Empty credits list in simulation response")
                    credit_data = credits_data[0]
                elif isinstance(credits_data, dict):
                    credit_data = credits_data
                else:
                    logger.error(f"Formato inesperado de credits: {type(credits_data)}")
                    raise ValueError("Unexpected credits format in simulation response")
                
                logger.info(f"Datos del crédito obtenidos: {credit_data} \n")

                # Los installments están dentro de cada crédito
                installments = credit_data.get("installments", [])
                
                logger.info(f"Installments obtenidas: {installments} \n")
                cuota_inicial = credit_data.get("initialFee")
                ID_credito = credit_data.get("ID")
                logger.info(f"ID del crédito obtenido: {ID_credito} \n")
                referencia_credito = credit_data.get("reference")
                id_cliente = credit_data.get("debtorID")

                if not installments:
                    logger.error("No se encontraron installments en la respuesta")
                    await error_notify(method_name, client_id, 
                        "No se encontraron cuotas en la simulación")
                    raise HTTPException(
                        status_code=404,
                        detail="No se encontraron cuotas en la simulación"
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
                    "cuota_inicial_formatted": f"${cuota_inicial_rounded:,}"
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
                        "taxes": taxes
                    },
                    "valores_formateados": formatted_values
                }

                logger.info("Valores extraidos y formateados exitosamente")
                logger.info(f"Valores formateados: {formatted_values}")
                # Cacheamos las cuotas simuladas para servirlas rapido en /detalle_cuota_vencida
                if id_cliente and installments:
                    cuotas_cache[id_cliente] = {
                        "cuotas": installments,
                        "timestamp": datetime.now(timezone.utc)
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
                await info_notify(method_name, client_id, info_message, entity_id=str(id_cliente))
            
                return response_data
            
            except (KeyError, AttributeError, ValueError, IndexError) as e:
                logger.error(f"Error extrayendo datos de simulación: {str(e)}")
                await error_notify(method_name, client_id, 
                    f"Error procesando respuesta de simulación: {str(e)}")
                await insertar_log(
                    method_name=method_name,
                    client_id=client_id,
                    error_message=f"Error procesando respuesta de simulación: {str(e)}",
                    http_code=500,
                    tipo="error"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error procesando datos de simulación: {str(e)}"
                )
    
    except ValueError as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de conversión de datos: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error de conversión de datos: {str(e)}",
            http_code=400,
            tipo="error",
            traceback_str=error_traceback
        )
        await error_notify(method_name, client_id, f"Error de conversión: {str(e)}")
        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_datos"],
                "detalles_usuario": "Recuerda ingresar solo números en los campos de monto y cuota inicial."
            }
        )
        
    except httpx.RequestError as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error de conexión: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error de conexión: {str(e)}",
            http_code=502,
            tipo="error",
            traceback_str=error_traceback
        )
        await error_notify(method_name, client_id, f"Error de conexión: {str(e)}")
        return JSONResponse(
            status_code=502,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_conexion"],
                "detalles_usuario": "Nuestro servicio está experimentando problemas de conexión temporales."
            }
        )
        
    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error interno: {str(e)}", exc_info=True)
        await insertar_log(
            method_name=method_name,
            client_id=client_id,
            error_message=f"Error interno: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback
        )
        await error_notify(method_name, client_id, f"Error interno: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_general"],
                "detalles_usuario": "Nuestro equipo técnico ha sido notificado y está trabajando en solucionarlo."
            }
        )


#manejar diferentes casos de entrada para el valor "principal" y extraer solo los números
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
        'cop', 'pesos', 'valor', 'seria', 'de', 'quiero', 
        'financiar', 'necesito', 'el', 'aproximadamente',
        'como', 'cerca', 'millones', 'mil'
    ]
    
    for palabra in palabras_a_eliminar:
        valor = valor.replace(palabra, '')

    # Eliminar símbolos monetarios y caracteres especiales
    valor = re.sub(r'[$ \'"]', '', valor)

    # Convertir puntos y comas usados como separadores de miles
    valor = valor.replace('.', '')
    valor = valor.replace(',', '')

    # Extraer solo dígitos
    numeros = re.findall(r'\d+', valor)
    
    if not numeros:
        raise ValueError(f"No se pudo extraer un valor numérico de: {raw_principal}")
    # Unir todos los números encontrados
    valor_limpio = ''.join(numeros)
    
    try:
        return float(valor_limpio)
    except ValueError as e:
        await error_notify("limpiar_valor_principal", "N/A", f"Error al convertir a numero: {valor_limpio}")
        raise ValueError(f"No se pudo convertir a numero: {valor_limpio}") from e

# Ejemplo de uso:

#calcular financiamineto version 2
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
            await error_notify(method_name, linea_producto_notify_error, "Falta 'linea_producto' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'linea_producto' en el payload")
        
        # --- PROCESAR SEMESTRE, el dato entra como una palabra ejemplo "segundo semestre" y debe devolver el numero del semestre ---
        semestre_keys = [
            "semestre_renovacion_menu",
            "semestre_renovación_menu",
            "semestre",
        ]
        semestre_texto_raw = next((payload.get(k) for k in semestre_keys if payload.get(k) is not None), "")
        semestre_texto = unicodedata.normalize("NFKD", str(semestre_texto_raw)).encode("ascii", "ignore").decode("ascii").strip().lower()
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
            "decimo semestre": 10
        }
        
        if not semestre_texto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'semestre' en el payload")
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message="Falta 'semestre' en el payload",
                http_code=400,
                tipo="error"
            )
            
            raise HTTPException(status_code=400, detail="Debe incluir 'semestre' en el payload")
        
        numero_semestre = semestres_map.get(semestre_texto)
        if numero_semestre is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de semestre '{semestre_texto}' no reconocido")
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message=f"Valor de semestre '{semestre_texto}' no reconocido",
                http_code=400,
                tipo="error"
            )
            raise HTTPException(status_code=400, detail=f"El semestre '{semestre_texto}' no es válido. Use: primer semestre, segundo semestre, etc.")

        # --- PROCESAR PLAZO_VALOR_PAGAR, el dato entra en string y debe devolverse como un numero ---
        plazo_texto_raw = payload.get("plazo_valor_pagar", "")
        logging.info(f"plazo_valor_pagar recibido: {plazo_texto_raw} \n")
        plazo_texto = unicodedata.normalize("NFKD", str(plazo_texto_raw)).encode("ascii", "ignore").decode("ascii").strip().lower()
        plazo_map = {
            "1 mes": 1,
            "2 meses": 2,
            "3 meses": 3,
            "4 meses": 4,
            "5 meses": 5,
            "6 meses": 6
        }

        if not plazo_texto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'plazo_valor_pagar' en el payload")
            #insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message="Falta 'plazo_valor_pagar' en el payload",
                http_code=400,
                tipo="error"
            )
            raise HTTPException(status_code=400, detail="Debe incluir 'plazo_valor_pagar' en el payload")

        plazo_valor = plazo_map.get(plazo_texto)
        if plazo_valor is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de plazo '{plazo_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El plazo '{plazo_texto}' no es valido. Use: a un mes, a dos meses, etc.")
        
        logger.info(f"plazo_valor procesado: {plazo_valor} \n")
        logger.info (f"numero de semestre procesado: {numero_semestre} \n")

        try:
            raw_principal = str(payload.get("principal", "0"))
            principal = await limpiar_valor_principal(raw_principal)
        except ValueError as e:
            await error_notify(method_name, linea_producto_notify_error, f"Error en el valor principal: {str(e)}")
            #insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message=f"Error en el valor principal: {str(e)}",
                http_code=400,
                tipo="error"
            )
            raise HTTPException(status_code=400, detail=f"Error en el valor principal: {str(e)}")

        # Porcentaje de cuota (sin si­mbolo %)
        porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        #plazo_escogido = int(payload.get("plazo_escogido", 0))
        #el plazo procesado de tipo string y transformado a numero es plazo_valor y se le asigna a plazo_escogido para los calculos
        plazo_escogido = plazo_valor
        payment_frequency = int(payload.get("paymentFrequency", 30))
        
        #logger.info (f"plazo escogido para realizar los calculos: {plazo_escogido} \n")
        

        # --- CALCULOS INICIALES ---
        valor_cuota_inicial = principal * porcentaje_cuota
        dias_totales = plazo_escogido * payment_frequency
        
        # --- VALIDAR RANGO DE DIAS (API requiere entre 30 y 150 dias) ---
        #if dias_totales < 30 or dias_totales > 150:
            #logger.error(f"Días fuera de rango: {dias_totales}. Debe estar entre 30 y 150.")
            #await error_notify(method_name, linea_producto_notify_error, f"Días fuera de rango: {dias_totales}")
            #raise HTTPException(
                #status_code=400,
                #detail=f"El plazo debe resultar en un número de días entre 30 y 150. Plazo actual: {dias_totales} días"
            #)

        # --- CONSULTA A API KUENTA ---
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await obtener_token(client)
            if not token:
                await error_notify(method_name, linea_producto_notify_error, "No se pudo obtener token de autenticacion")
                raise HTTPException(status_code=401, detail="No se pudo obtener token de autenticacion")

            ext_client_product = None  # cliente externo opcional para productos
            try:  # intenta cargar configuracion desde BD
                ext_client_product = await ExternalClient.from_code("KUENTA_PRODUCT_GET")  # codigo de servicio en BD
                
                logger.info(f"Cliente externo KUENTA_PRODUCT_GET cargado desde BD: {ext_client_product.__dict__}\n")
            except ValueError:  # si no existe en BD
                ext_client_product = None  # fallback a settings
            product_base_url = ext_client_product.url if ext_client_product and ext_client_product.url else API_URL  # base desde BD o settings
            product_base_url = product_base_url or "https://api.kuenta.co/v1"  # fallback por seguridad
            product_base_url = product_base_url.rstrip("/")  # limpia slash final
            if "{linea_producto}" in product_base_url:  # si ya viene plantilla desde BD
                product_url_template = product_base_url  # usa plantilla directa
                
            else:  # arma plantilla desde base
                if product_base_url.endswith("product-lines"):  # si viene API_URL de product-lines
                    product_base_url = product_base_url.rsplit("/", 1)[0]  # elimina el segmento final
                product_url_template = f"{product_base_url}/products/{{linea_producto}}"  # construye plantilla
                
            KUENTA_URL = product_url_template.format(  # construye URL final con format
                linea_producto=linea_producto,  # parametro linea_producto
            )  # URL final de consulta
            
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": token
            }

            try:
                if ext_client_product:  # usa cliente externo si existe
                    ext_client_product.set_headers(headers)  # headers para auth
                    ext_client_product.set_url(KUENTA_URL)  # URL final de producto
                    resp = await ext_client_product.run()  # ejecuta request externa
                    if not isinstance(resp, dict):  # valida tipo de respuesta
                        raise Exception("Respuesta invalida del servicio externo")  # error si no es dict
                    status_code = resp.get("status", 500)  # status HTTP simulado
                    if status_code >= 400:  # maneja error HTTP en respuesta
                        await error_notify(  # notifica error de API
                            method_name,  # metodo
                            linea_producto_notify_error,  # id de contexto
                            f"Error de respuesta de Kuenta: {resp.get('data')}"  # detalle del error
                        )
                        await insertar_log(  # registra error en BD
                            method_name=method_name,  # metodo
                            client_id=linea_producto_notify_error,  # id de contexto
                            error_message=f"Error de respuesta de Kuenta: {resp.get('data')}",  # mensaje
                            http_code=status_code,  # status simulado
                            tipo="error"  # tipo log
                        )
                        raise HTTPException(status_code=status_code, detail=f"Error de respuesta de Kuenta: {resp.get('data')}")  # corta flujo
                    product_data = (resp.get("data") or {}).get("product", {})  # data de producto
                else:  # fallback a httpx directo
                    resp = await client.get(KUENTA_URL, headers=headers)  # request GET
                    resp.raise_for_status()  # valida status HTTP
                    product_data = resp.json().get("data", {}).get("product", {})  # extrae data
            except httpx.RequestError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de conexion con la API de Kuenta: {e}")
                # insertar log del error en la base de datos
                await insertar_log(
                    method_name=method_name,
                    client_id=linea_producto_notify_error,
                    error_message=f"Error de conexion con la API de Kuenta: {e}",
                    http_code=502,
                    tipo="error"
                )
                raise HTTPException(status_code=502, detail=f"Error de conexion con la API de Kuenta: {e}")
            except httpx.HTTPStatusError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de respuesta de Kuenta: {e.response.text}")
                # insertar log del error en la base de datos
                await insertar_log(
                    method_name=method_name,
                    client_id=linea_producto_notify_error,
                    error_message=f"Error de respuesta de Kuenta: {e.response.text}",
                    http_code=e.response.status_code,
                    tipo="error"
                )
                raise HTTPException(status_code=e.response.status_code, detail=f"Error de respuesta de Kuenta: {e.response.text}")

        # --- VALIDAR RESPUESTA ---
        if product_data.get("ID") != linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "El ID del producto no coincide")
            #insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=linea_producto_notify_error,
                error_message="El ID del producto no coincide",
                http_code=404,
                tipo="error"
            )
            raise HTTPException(status_code=404, detail="El ID del producto no coincide")
        
        logger.info(f"ID del producto obtenido: {product_data.get('ID')}\n")

        aval_porcentaje = next(
            (float(str(c.get("percentage", 0))) for c in product_data.get("costs", []) if c.get("label") == "Aval"),
            None
        )
        if aval_porcentaje is None:
            await error_notify(method_name, linea_producto_notify_error, "No se encontro porcentaje de Aval en el producto")
            raise HTTPException(status_code=404, detail="No se encontro porcentaje de Aval en el producto")
        logger.info(f"Porcentaje de Aval obtenido de la linea: {aval_porcentaje}% \n")
        # --- CALCULOS FINALES ---
        valor_desembolsar = principal - valor_cuota_inicial
        if (1 - aval_porcentaje) == 0:
            await error_notify(method_name, linea_producto_notify_error, "El porcentaje de aval no puede ser 100%")
            raise ValueError("El porcentaje de aval no puede ser 100%.")

        valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        deducciones_anticipadas = valor_solicitar * aval_porcentaje
        
        # --- FORMATEO PARA DEMOSTRACION ---
        demostracion_valor_producto = f"${principal:,.0f}"
        demostracion_cuota_inicial = f"${valor_cuota_inicial:,.0f}"
        demostracion_valor_desembolsar = f"${valor_desembolsar:,.0f}"
        demostracion_deducciones = f"${deducciones_anticipadas:,.0f}"
        demostracion_valor_solicitar = f"${valor_solicitar:,.0f}"
        
        logger.info (f"numero de semestre procesado: {numero_semestre} semestre \n")
        logger.info(f"plazo_valor_pagar procesado: {plazo_valor} meses \n")
        
        logger.info(f"----- Resumen de calculos realizados ----- \n")
        logger.info(f"Valor del producto (principal): {demostracion_valor_producto} \n")
        logger.info(f"Cuota inicial (valor_cuota_inicial): {demostracion_cuota_inicial} \n")
        logger.info(f"Valor a desembolsar (valor_desembolsar): {demostracion_valor_desembolsar} \n")
        logger.info(f"Deducciones anticipadas (deducciones_anticipadas): {demostracion_deducciones} \n")
        logger.info(f"Valor a solicitar (valor_solicitar): {demostracion_valor_solicitar} \n")
        logger.info(f"Aval aplicado porcentaje (aval_porcentaje): {aval_porcentaje} \n")
        logger.info(f"Plazo en dias (plazo_dias): {dias_totales} \n")
        logger.info(f"Porcentaje escogido (porcentaje_str): {porcentaje_str}% \n")
        
        logger.info("Calculo completado correctamente. \n")
        logger.info("-------------fin de la ejecucion------------------ \n")
        
        #notificacion informativa
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
            "valor_solicitado_demostracion": demostracion_valor_solicitar
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
            traceback_str=error_traceback
        )
        await error_notify(method_name, linea_producto_notify_error, f"Error de datos: {e}")
        return {
                "estado": "error",
                "mensaje": MENSAJES_USUARIO["valor_invalido"],
                "detalles_usuario": "Por favor verifica el valor del monto a financiar."
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
            traceback_str=error_traceback
        )
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        return {
            "estado": "error",
            "mensaje": mensaje_usuario,
            "detalles_usuario": "Si el problema persiste, por favor comuni­cate con nuestro servicio al cliente."
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
            traceback_str=error_traceback
        )
        await error_notify(method_name, linea_producto_notify_error, f"Error interno: {e}")
        return {
            "estado": "error", 
            "mensaje": MENSAJES_USUARIO["error_conexion"],
            "detalles_usuario": "Nuestro equipo tecnico ha sido notificado del inconveniente."
        }


# Nuevo endpoint para consultar el estado de un pago usando creditid, installmentid y orderid
@app.post("/obtener-estado/{debtor_id}")
async def obtener_estado(debtor_id:str,request: Request):
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
        debtor_id_notify_error = f"debtor_id_cliente =  {debtor_id} y creditid = {creditid}"
        logger.info(f"+++++ Parametros recibidos en el body: creditid= ++++++++, \n {creditid} \n")
        logger.info(f"+++++ Parametros recibidos en el body: installmentid= ++++++++, \n {installmentid} \n")
        logger.info(f"+++++ Parametros recibidos en el body: orderid= ++++++++, \n {orderid} \n")
        

        if not creditid or not installmentid or not orderid:
            raise HTTPException(status_code=400, detail="Faltan parametros obligatorios: creditid, installmentid, orderid")

        ext_client_order = None  # cliente externo opcional para estado de orden
        try:  # intenta cargar configuracion desde BD
            ext_client_order = await ExternalClient.from_code("KUENTA_ORDER_STATUS")  # codigo de servicio en BD
            
            logger.info(f"Cliente externo KUENTA_ORDER_STATUS cargado desde BD: {ext_client_order.__dict__}\n")
        except ValueError:  # si no existe en BD
            ext_client_order = None  # fallback a settings
            logger.info("Cliente externo KUENTA_ORDER_STATUS no encontrado en BD, usando configuración por defecto.")
        order_base_url = ext_client_order.url if ext_client_order and ext_client_order.url else GET_PAYABLE_URL  # base desde BD o settings
        order_base_url = order_base_url or "https://api.kuenta.co/v1/payables"  # fallback por seguridad
        order_base_url = order_base_url.rstrip("/") + "/"  # asegura slash final
        order_url_template = f"{order_base_url}{{creditid}}/installments/0/orders/list/{{orderid}}"  # plantilla con placeholders
        url = order_url_template.format(  # construye URL final
            creditid=creditid,  # parametro creditid
            orderid=orderid,  # parametro orderid
        )  # URL final de consulta
        
        intentos = 3
        intervalo_segundos = 10
        intento = 0

        logger.info(f"Parametros recibidos: creditid={creditid}, installmentid={installmentid}, orderid={orderid}")

        async with httpx.AsyncClient() as client:
            # Obtener token una sola vez

            access_token = await obtener_token(client)
            logger.info(f"Token obtenido: {access_token}")

            if not access_token:
                raise HTTPException(status_code=401, detail="No se pudo obtener el token de acceso")

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": debtor_id,
                "Authorization": access_token
            }
            
            while intento < intentos:
                intento += 1
                try:
                    
                    if ext_client_order:  # usa servicio externo si existe
                        ext_client_order.set_headers(headers)  # headers para auth
                        ext_client_order.set_url(url)  # URL final de consulta
                        response = await ext_client_order.run()  # ejecuta request externa
                        if not isinstance(response, dict):  # valida tipo de respuesta
                            raise Exception("Respuesta invalida del servicio externo")  # error si no es dict
                        status_code = response.get("status", 500)  # status HTTP simulado
                        data = response.get("data") or {}  # data de respuesta
                        if not isinstance(data, dict):  # valida data
                            data = {}  # fallback a dict vacio
                        if status_code >= 400:  # maneja error HTTP
                            raise Exception(f"Error HTTP {status_code}")  # fuerza captura en except
                    else:  # fallback a httpx directo
                        response = await client.get(url, headers=headers)  # request httpx original
                        response.raise_for_status()  # valida status HTTP
                        data = response.json()  # parse JSON
                    status = data.get("status")
                    logger.info(f"Intento {intento}: status del pago = {status}")
                    
                    if status != "pending":
                        logger.info(f"Estado final obtenido: {status} en el intento {intento} \n")
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
                        traceback_str=error_traceback
                    )
                    await error_notify(method_name, debtor_id_notify_error, f"Error en intento: {intento} {str(e)}")
                    
                if intento < intentos:
                    await asyncio.sleep(intervalo_segundos)
        return {"mensaje": "No se obtuvo un estado diferente a 'pending' tras 3 intentos"}
    
    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Error en el proceso: {str(e)}")
        await insertar_log(
            method_name=method_name,
            client_id=debtor_id_notify_error,
            error_message=f"Error en el proceso: {str(e)}",
            http_code=500,
            tipo="error",
            traceback_str=error_traceback
        )
        await error_notify(method_name, debtor_id_notify_error, f"Error en el proceso: {str(e)}")
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
            "cedula_cliente": payload.cedula_cliente
        }
        
        data_credito = payload.datos_credito.model_dump()

        # Inserción en BD
        resultado = await insertar_registro_completo(data_cliente, data_credito)
        
        # Verificar si la inserción falló por duplicado o fue actualización
        if isinstance(resultado, dict):
            if resultado["status"] == "success":
                logger.info(f"Renovación registrada exitosamente: {resultado}")
                await info_notify(method_name, client_id, f"Renovación registrada. en base de datos ID: {resultado['id_renovacion']}")
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "Renovación registrada correctamente",
                    "data": resultado
                }
            )
        
        elif resultado["status"] == "updated":
            logger.info(f"Registro actualizado exitosamente: {resultado}")
            await info_notify(method_name, client_id, f"Registro actualizado ID: {resultado['id_renovacion']}")
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": resultado["message"],  # "Registro actualizado exitosamente..."
                    "data": resultado
                }
            )
        
        elif resultado["status"] == "error":
            logger.warning(f"Intento de registro duplicado rechazado: {resultado}")
            await error_notify(method_name, client_id, f"No se puede registrar la renovación: {resultado['message']}")
            # insertar log del error en la base de datos
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message=f"No se puede registrar la renovación: {resultado['message']}",
                http_code=409,
                tipo="error"
            )
            return JSONResponse(
                status_code=409,  # Conflict
                content={
                    "status": "error",
                    "message": "No se puede registrar la renovación",
                    "error": resultado["message"],
                    "details": resultado.get("details", {})
                }
            )
        
        else:
            # Caso inesperado
            logger.error(f"Estado desconocido en resultadoado: {resultado}")
            await error_notify(method_name, client_id, "Estado de respuesta desconocido al registrar renovación")
            await insertar_log(
                method_name=method_name,
                client_id=client_id,
                error_message="Estado de respuesta desconocido al registrar renovación",
                http_code=500,
                tipo="error"
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Error interno del servidor",
                    "details": "Estado de respuesta desconocido"
                }
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
            traceback_str=error_traceback
        )
        await error_notify(method_name, client_id, f"Error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Error interno del servidor", "detail": str(e)}
        )

# consultar los creditos realizados en el flujo de renovaciones
@app.post("/consultar-creditos", tags=["Consultas"], summary="Búsqueda avanzada de créditos")
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
            fecha_fin=filtros.fecha_fin
        )
        
        if not resultados:
            return JSONResponse(status_code=404, content={"status": "info", "message": "No se encontraron registros con esos filtros"})
        
        
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
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "total_resultados": len(resultados),
                "data": jsonable_encoder(resultados)
            }
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
            traceback_str=error_traceback
        )
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

#### endpoints para pruebas de notificaciones ###

# Endpoint que llama a error_notify (envía email + telegram)
@app.post("/test-notify")
async def test_notify(payload: TestNotifyRequest = Body(...)):
    try:
        result = await error_notify(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-notify")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# Endpoint para probar solo envi­o por email
@app.post("/test-email")
async def test_email(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_email(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-email")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# Endpoint para probar solo envío a Telegram
@app.post("/test-telegram")
async def test_telegram(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_telegram(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-telegram")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


# Endpoint para enviar correo de renovacion de credito con validaciones
@app.post("/Correo_post_llamada", summary="Receptor de variables despues de la llamada",description="Recibe el payload con las variables de entrada y extraídas.",tags=["Correo_post_llamada"])
async def handle_webhook(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Endpoint principal que recibe el payload del webhook.

    1.  Valida automaticamente el payload contra el modelo `WebhookPayload`.
    2.  Llama al servicio `procesar_webhook` para manejar toda la logica.
    3.  Retorna una respuesta JSON.
    """
    logger.info(f"Inicio del procesamiento del webhook, payload {payload} \n")
    try:
        
        logger.info(f"Payload completo recibido: {payload.model_dump_json(indent=2)} \n")
        
        # logging.info(f"Objetivo extraido: {payload.extracted_variables.objetivo} \n")
        
        # Logica de enrutamiento de el envio de los correos basada en el objetivo de la llamada de cada agente IA
        objetivo = payload.extracted_variables.objetivo
        logging.info(f"Objetivo a procesar: {objetivo}")
        
        if objetivo == "webinar":
            logging.info("El objetivo es 'webinar'. Llamando a procesar_webhook_webinar.")
            
            #empieza aqui
            resultado = await procesar_webhook_webinar(payload)
            
            logging.info(f"Procesamiento completado para webinar: {payload.input_variables.NOMBRE_TITULAR}")
            
            #Validar explicitamente el resultado
            if resultado.get("status") == "error":
                
                logging.info(f"error en la procesamiento del webhook webinar: {resultado.get('message')}")
                await error_notify(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}"
                )
                return JSONResponse(status_code=500,
                                    content={
                                        "status": "error",
                                        "message": resultado.get("message", "Error desconocido"),
                                        "correo_enviado": resultado.get("correo_enviado", False),
                                        "intentos": resultado.get("intentos_correo", 0),
                                        "data": resultado
                                    }
                )
            elif resultado.get("status") == "success":   
                
                logging.info(f"Webhook webinar EXITOSO: Correo enviado")
                await info_notify(
                    method_name="webhook_webinar",
                    client_id=objetivo,
                    info_message=f"Webhook de webinar completado exitosamente. Correo enviado en  para {payload.input_variables.NOMBRE_TITULAR}"
                )
                return JSONResponse(status_code=200,
                                    content={
                                        "status": "success",
                                        "message": "Webhook de renovacion procesado",
                                        "correo_enviado": True,
                                        "intentos": resultado.get("intentos_correo"),
                                        "data": resultado,
                                    })
            else:
                logging.warning(f"Webhook webinar con problemas: {resultado}")
                await error_notify(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}"
                )
                await insertar_log(
                    method_name="handle_webhook_webinar",
                    client_id=objetivo,
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}",
                )
                return {
                    "status": "error" if resultado.get("status") == "error" else "partial",
                    "message": resultado.get("message", "Error desconocido"),
                    "correo_enviado": resultado.get("correo_enviado", False),
                    "intentos": resultado.get("intentos_correo", 0),
                    "data": resultado
                }
        
        elif objetivo == "renovacion":
            logging.info("El objetivo es 'renovacion'. Llamando a procesar_webhook_renovacion.")
            
            
            resultado = await procesar_webhook_renovacion(payload)
            
            
            logging.info(f"Procesamiento completado para renovacion: {payload.input_variables.NOMBRE_TITULAR}")
            
            #Validar explicitamente el resultado
            if resultado.get("status") == "error":
                logging.info(f"error en la procesamiento del webhook renovacion: {resultado.get('message')}")
                await error_notify(method_name="handle_webhook_renovacion",
                                client_id=objetivo,
                                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}"
                                    )
                return JSONResponse(status_code=500,
                                    content={"status": "error",
                                            "message": resultado.get("message", "Error desconocido")
                                            ,
                                            "correo_enviado": resultado.get("correo_enviado", False),
                                            "intentos": resultado.get("intentos_correo", 0),
                                            "data": resultado
                                            }
                                    )
            elif resultado.get("status") == "success":
                
                logging.info(f"Webhook renovacion EXITOSO: Correo enviado")
                await info_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    info_message=f"Webhook de renovacion completado exitosamente. Correo enviado  para {payload.input_variables.NOMBRE_TITULAR}"
                )
                return  JSONResponse( status_code=200,
                content={
                    "status": "success",
                    "message": "Webhook de renovacion procesado exitosamente",
                    "correo_enviado": True,
                    "intentos": resultado.get("intentos_correo"),
                    "data": resultado
                })
            else:
                logging.warning(f"no se encontro el objetivo de la llamada: {resultado}")
                await error_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}"
                )
                await insertar_log(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    error_message=f"Webhook renovacion con problemas: {resultado.get('message')}"
                )
                return {
                    "status": "error" if resultado.get("status") == "error" else "partial",
                    "message": resultado.get("message", "Error desconocido"),
                    "correo_enviado": resultado.get("correo_enviado", False),
                    "intentos": resultado.get("intentos_correo", 0),
                    "data": resultado
                }
                
        elif objetivo == "renovacion y refinanciacion":
            logging.info("El objetivo es 'renovacion y refinanciacion'. Llamando a procesar_llamada_renovacion_Y_refinanciamiento.")
            logging.info(f"payload completo recibido: {payload.model_dump_json(indent=2)} \n")
            
            try:
                resultado = await procesar_llamada_renovacion_Y_refinanciamiento(payload)
                
                logging.info(f"Procesamiento completado para renovacion y refinanciacion: {payload.input_variables.NOMBRE_TITULAR}")
                
                # Validar explicitamente el resultado
                if resultado.get("status") == "error":
                    logging.error(f"Error en el procesamiento: {resultado.get('message')}")
                    await error_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        error_message=f"Error en procesamiento de renovacion y refinanciacion: {resultado.get('message')}"
                    )
                    return JSONResponse(
                        status_code=430,
                        content={
                            "status": "error",
                            "message": resultado.get("message", "Error desconocido"),
                            "acciones_ejecutadas": resultado.get("acciones", []),
                            "errores": resultado.get("errores", []),
                            "data": resultado
                        }
                    )
                elif resultado.get("status") == "success":
                    logging.info(f"Procesamiento EXITOSO para renovacion y refinanciacion")
                    await info_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        info_message=f"Procesamiento de renovacion y refinanciacion completado exitosamente para {payload.input_variables.NOMBRE_TITULAR}. Acciones: {', '.join(resultado.get('acciones_ejecutadas', []))}"
                    )
                    return JSONResponse(
                        status_code=200,
                        content={
                            "status": "success",
                            "message": "Procesamiento de renovacion y refinanciacion completado exitosamente",
                            "cliente": resultado.get("cliente"),
                            "correo": resultado.get("correo"),
                            "acciones_ejecutadas": resultado.get("acciones_ejecutadas", []),
                            "data": resultado
                        }
                    )
                else:
                    # status == "partial" o "warning"
                    logging.warning(f"⚠️ Procesamiento parcial: {resultado}")
                    await error_notify(
                        method_name="handle_webhook_renovacion_refinanciacion",
                        client_id=objetivo,
                        error_message=f"Procesamiento parcial de renovacion y refinanciacion: {resultado.get('message')}"
                    )
                    return JSONResponse(
                        status_code=430,
                        content={
                            "status": resultado.get("status", "partial"),
                            "message": resultado.get("message", "Procesamiento parcial"),
                            "acciones_ejecutadas": resultado.get("acciones_ejecutadas", []),
                            "errores": resultado.get("errores", []),
                            "data": resultado
                        }
                    )
            except Exception as e:
                logging.error(f"Excepción en procesar_llamada_renovacion_Y_refinanciamiento: {e}", exc_info=True)
                await error_notify(
                    method_name="handle_webhook_renovacion_refinanciacion",
                    client_id=objetivo,
                    error_message=f"Excepción en renovacion y refinanciacion: {str(e)}"
                )
                return JSONResponse(
                    status_code=430,
                    content={
                        "status": "error",
                        "message": f"Error en el procesamiento de renovacion y refinanciacion",
                        "detail": str(e),
                        "cliente": payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown"
                    }
                )
        else:
            # Objetivo no reconocido
            logging.warning(f"Objetivo no reconocido: {objetivo}")
            await error_notify(
                method_name="handle_webhook",
                client_id=objetivo,
                error_message=f"Objetivo no reconocido en webhook: {objetivo}"
            )
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Objetivo no reconocido: {objetivo}",
                    "objetivos_validos": ["webinar", "renovacion", "renovacion y refinanciacion"],
                    "cliente": payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown"
                }
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
                client_id=(payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown"),
                error_message=f"Error en el endpoint /webhook: {str(e)}",
                http_code=500,
                tipo="error",
                traceback_str=error_traceback
            )
        except Exception as log_error:
            logging.error(f"Error al registrar el log: {str(log_error)}")
        
        await error_notify(
            method_name="handle_webhook",
            client_id=(payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown"),
            error_message=f"Error en el endpoint /webhook: {str(e)}"
        )
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "message": "Error al procesar el webhook",
                "detail": str(e),
                "cliente": (payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown")
            }
        )


# Endpoint para registrar renovaciones en la base de datos
@app.post("/renovaciones", tags=["Renovaciones"], summary="Registrar renovación de cliente")
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
            host=db_host,
            user=db_user,
            password=db_pass,
            db=db_name
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
                        payload.nombre_cliente
                    )
                )
                
                # Confirmar la transacción
                await connection.commit()
                
                # Obtener el ID de la renovación insertada
                insertado_id = cursor.lastrowid
                
                logger.info(f"Renovación registrada exitosamente con ID: {insertado_id}")
                
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
                    entity_id=str(insertado_id)
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
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
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
            traceback_str=error_traceback
        )
        await error_notify(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error al insertar en BD: {str(db_error)}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error al conectar con la base de datos",
                "detail": "No se pudo registrar la renovación"
            }
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
            traceback_str=error_traceback
        )
        await error_notify(
            method_name=method_name,
            client_id=payload.nombre_cliente,
            error_message=f"Error en registrar_renovacion: {str(e)}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
                "detail": str(e)
            }
        )

# endpoints para gestion de servicios externos ###

@app.post("/servicios-externos", tags=["Servicios Externos"], summary="Crear servicio externo")
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


@app.put("/servicios-externos/{codigo}", tags=["Servicios Externos"], summary="Actualizar servicio externo")
async def actualizar_servicio_externo_endpoint(codigo: str, payload: ServicioExternoUpdateRequest):
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


@app.patch("/servicios-externos/{codigo}/desactivar", tags=["Servicios Externos"], summary="Desactivar servicio externo")
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


@app.get("/servicios-externos/{codigo}", tags=["Servicios Externos"], summary="Obtener servicio externo por codigo")
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
    
    
    #consultar logs de errores desde la base de datos con filtros
    
@app.post("/consultar-logs", tags=["Logs"], summary="Consultar todos los logs del sistema")
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
                if not (1 <= dia_int <= 31) or not (1 <= mes_int <= 12) or año_int < 2000:
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
                    "ejemplo": "12-02-2025"
                }
            )
        
        if filtros.fecha_inicio and not validar_fecha_dmy(filtros.fecha_inicio, "fecha_inicio"):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Formato de fecha_inicio inválido",
                    "parametro": "fecha_inicio",
                    "formato_esperado": "D-M-Y",
                    "ejemplo": "01-01-2025"
                }
            )
        
        if filtros.fecha_fin and not validar_fecha_dmy(filtros.fecha_fin, "fecha_fin"):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Formato de fecha_fin inválido",
                    "parametro": "fecha_fin",
                    "formato_esperado": "D-M-Y",
                    "ejemplo": "31-12-2025"
                }
            )
        
        # Validar que tipo sea "error" o "info"
        if filtros.tipo and filtros.tipo.lower() not in ["error", "info"]:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "El parámetro 'tipo' debe ser 'error' o 'info'",
                    "parametro": "tipo",
                    "valores_validos": ["error", "info"]
                }
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
            offset=filtros.offset
        )
        
        # Verificar si hubo error en la consulta
        if "error" in resultado:
            logger.error(f"Error en consulta de logs: {resultado['error']}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Error al consultar los logs",
                    "detail": resultado['error']
                }
            )
        
        # Respuesta exitosa
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Logs consultados exitosamente",
                "total": resultado['total'],
                "registros_retornados": len(resultado['registros']),
                "limite": resultado['limite'],
                "offset": resultado['offset'],
                "hay_mas": (resultado['offset'] + resultado['limite']) < resultado['total'],
                "datos": resultado['registros']
            }
        )
    
    except Exception as e:
        logger.error(f"Error en endpoint consultar_logs: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Error interno del servidor",
                "detail": str(e)
            }
        )
