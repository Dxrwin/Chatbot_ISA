from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from utils.notify_error import error_notify, get_cached_logs,send_log_email, send_log_telegram,info_notify
from utils.enviar_correo_IA import procesar_webhook_renovacion, procesar_webhook_webinar
from utils.auth import obtener_token
from models.models import WebhookPayload
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
    Middleware que detecta reinicios del servidor por cambios en el cÃƒÂ³digo
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
        logger.error(f"Error al enviar notificaciÃƒÂ³n de reinicio: {e}")
    
    return await call_next(request)

# Mensajes amigables para el cliente
MENSAJES_CLIENTE = {
    "error_conexion": "Lo sentimos, en este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
    "error_datos": "Los valores ingresados no son vÃƒÂ¡lidos. Por favor verifica que el monto y la cuota inicial sean nÃƒÂºmeros vÃƒÂ¡lidos.",
    "error_servicio": "En este momento nuestro servicio no estÃƒÂ¡ disponible. Por favor intenta mÃƒÂ¡s tarde.",
    "error_simulacion": "No pudimos completar la simulaciÃƒÂ³n de tu crÃƒÂ©dito. Por favor verifica los datos e intenta nuevamente.",
    "cuotas_no_encontradas": "No se pudo obtener el detalle de las cuotas para tu crÃƒÂ©dito. Por favor intenta nuevamente.",
    "error_general": "Hubo un problema al procesar tu solicitud. Por favor intenta nuevamente mÃƒÂ¡s tarde."
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

#pydantc es un validador de datos de entrada y salida
# Modelo Pydantic para el payload sin validaciones estrictas
class PayableRequest(BaseModel):
    creditLineID: str 
    principal: float 
    time: int 
    disbursementMethod: str 
    initialFee: float 
    paymentFrequency: int 

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
    message: str = "Mensaje de prueba para notificaciÃƒÂ³n"



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

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = await client.get(API_URL, headers=headers)
                    response.raise_for_status()
                    data = response.json()
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
                    if attempt == MAX_RETRIES:
                        return {
                            "estado": "error",
                            "mensaje": MENSAJES_CLIENTE["error_conexion"],
                            "detalles_usuario": "No se pudo conectar con el servicio externo. Por favor intenta nuevamente mas tarde."
                        }
                    await asyncio.sleep(RETRY_DELAY * attempt)

                except httpx.HTTPStatusError as e:
                    logger.error(f"Error HTTP {e.response.status_code} en API externa: {e.response.text}")
                    await error_notify(method_name, parent_id_notify_error, f"Error en API externa: {e.response.text}")
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
        logger.error(f"Error general en webhook_product_lines: {e}")
        await error_notify(method_name, parent_id_notify_error, f"Error general: {e}")
        return {
            "estado": "error",
            "mensaje": MENSAJES_CLIENTE["error_general"],
            "detalles_usuario": "Ocurrio un error inesperado. Por favor intenta nuevamente mas tarde."
        }


def format_currency(value: float) -> str:
    """Formatea un nÃƒÂºmero como moneda COP: sin decimales, con $ y separadores de miles."""
    return f"${value:,.0f}"

# Endpoint para crear un nuevo payable o credito despues de la simulacion
@app.post("/payable/{client_id}")
async def create_payable(client_id: str, payload: PayableRequest):
    """
    Endpoint para crear un nuevo payable:
    1. Recibe el ID del cliente como parÃƒÂ¡metro
    2. Transforma los campos principal y initialFee de str a int
    3. Extrae el token de autorizacion del payload
    4. Realiza la peticiÃƒÂ³n POST al endpoint de payable
    
    """
    method_name = "create_payable"
    try:

        async with httpx.AsyncClient() as client:
            
            logger.info(f"+++++ Parametros recibidos: client_id= ++++++++, \n {client_id} \n")
            
            logger.info(f"#####--- Payload entrante ----#### \n {payload} \n")
            
            principal = payload.principal
            initial_fee = payload.initialFee
            
            token = await obtener_token(client)
            
            new_payload = {
                "creditLineID": payload.creditLineID,
                "principal": principal,
                "time": payload.time,
                "disbursementMethod": payload.disbursementMethod,
                "initialFee": initial_fee,
                "paymentFrequency": payload.paymentFrequency
            }
            logger.info(f"Payload para saliente para el post a kuenta: \n {new_payload} \n")
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": client_id,
                #"Config-Organization-ID":"c269cfcc-0c9c-43e3-bef0-9e95d42ca309",
                #"Organization-ID":"c8c90e3e-f2a6-4caa-8254-a1403b4416d3",
                "Authorization": token
            }
            logger.info(f"Iniciando peticion POST a {PAYABLE_URL}")
            logger.info(f"Payload transformado para enviar a kuenta: {new_payload}")

            max_retries = 3
            for attempt in range(max_retries):
                
                try:
                    response = await client.post(
                        PAYABLE_URL,
                        json=new_payload,
                        headers=headers
                    )
                    status_code = response.status_code
                    logger.info(f"Intento {attempt+1}: status_code={status_code}")
                    if status_code == 201:
                            logger.info("Procesando respuesta de Kuenta")
                            response_data = response.json()
                            credit = response_data.get("data", {}).get("credit", {})
                            
                            # ID credito
                            response_credit_id = credit.get("ID")
                            logger.info(f"ID del crÃƒÂ©dito creado: {response_credit_id} \n")

                    try:

                            url_prod = f"https://api.kuenta.co/v1/payable/{response_credit_id}"
                            response_get_simulacion = await client.get(url_prod, headers=headers)
                            status_code_simulacion = response_get_simulacion.status_code
                            
                            logger.info(f"Status code de la simulacion: {status_code_simulacion}")
                            
                            
                            if status_code_simulacion == 200 or status_code_simulacion == 201:
                                simulacion_data = response_get_simulacion.json()
                                installments = simulacion_data.get("data", {}).get("credit", {}).get("installments", [])
                                cuota_inicial = simulacion_data.get("data", {}).get("credit", {}).get("initialFee")
                                ID_credito = simulacion_data.get("data", {}).get("credit", {}).get("ID")
                                referencia_credito = simulacion_data.get("data").get("credit").get("reference")
                                id_cliente = simulacion_data.get("data").get("credit").get("debtorID")

                                if installments:
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
                                    response_data.update({
                                        "ID del credito creado": response_credit_id,
                                        "valores_originales": {
                                            "payment": payment,
                                            "capital": capital,
                                            "interest": interest,
                                            "costs": costs,
                                            "taxes": taxes
                                        },
                                        "valores_formateados": {
                                            "payment_formatted": formatted_values["payment_formatted"],
                                            "capital_formatted": formatted_values["capital_formatted"],
                                            "interest_formatted": formatted_values["interest_formatted"],
                                            "costs_formatted": formatted_values["costs_formatted"],
                                            "taxes_formatted": formatted_values["taxes_formatted"],
                                            "cuota_inicial_formatted": formatted_values["cuota_inicial_formatted"]
                                        }
                                    })

                                    logger.info("Valores extraidos y formateados exitosamente")
                                    logger.info(f"Valores formateados: {formatted_values}")
                                    # Cacheamos las cuotas simuladas para servirlas rapido en /detalle_cuota_vencida
                                    if id_cliente and installments:
                                        cuotas_cache[id_cliente] = {
                                            "cuotas": installments,
                                            "timestamp": datetime.now(timezone.utc)
                                        }
                                    
                                    #notificacion informativa a telegram y email
                                    info_message = f"Credito creado y regsitrado en kuenta correctamente \n ID del crÃƒÂ©dito: {ID_credito} \n Referencia del credito :{referencia_credito}\n ID del cliente :{id_cliente} \n Valor total credito:{formatted_values['payment_formatted']}"
                                    
                                    # envia notificacion informativa (email + telegram) con id para seguimiento
                                    await info_notify(method_name, client_id, info_message, entity_id=str(id_cliente))
                
                                    return response_data
                                else:
                                    logger.error("No se encontraron installments en la respuesta")
                                    await error_notify(method_name, client_id, "No se encontraron cuotas en la simulacion")
                                    raise HTTPException(status_code=404, detail="No se encontraron cuotas en la simulacion")
                
                            else:
                                logger.error(f"Error en la consulta de simulaciÃƒÂ³n: {status_code_simulacion}")
                                await error_notify(method_name, client_id, f"Error al consultar la simulacion: {status_code_simulacion}")
                                raise HTTPException(status_code=status_code_simulacion, 
                                                detail="Error al consultar la simulacion")
                            
                    except httpx.HTTPStatusError as e:
                            await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {str(e)}")
                            logger.error(f"Intento {attempt+1}: Error en la respuesta de la API externa kuenta: {e.response.status_code}")
                    except Exception as e:
                        logger.warning(f"No se pudo enviar notificacion informativa: {e}")
                        
                except httpx.HTTPStatusError as e:
                        logger.error(f"Intento {attempt+1}: Error en API externa: {e.response.status_code}")
                        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # espera exponencial
            # Si no se logrÃƒÂ³ en los reintentos
            await error_notify(method_name, client_id, f"Error de conexion tras: {max_retries} intentos o respuesta no vÃƒÂ¡lida")
            raise HTTPException(status_code=502, detail=f"Error de conexion tras {max_retries} intentos o respuesta no vÃƒÂ¡lida")
        
    except ValueError as e:
        logger.error(f"Error de conversion de datos: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conversion de datos: {str(e)}")
        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_datos"],
                "detalles_usuario": "Recuerda ingresar solo numeros en los campos de monto y cuota inicial."
            }
        )
        
    except httpx.RequestError as e:
        logger.error(f"Error de conexiÃƒÂ³n: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conexion: {str(e)}")
        return JSONResponse(
            status_code=502,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_conexion"],
                "detalles_usuario": "Nuestro servicio estÃƒÂ¡ experimentando problemas de conexion temporales."
            }
        )
    
    except httpx.HTTPStatusError as e:
        logger.error(f"Error en API externa: {e.response}")
        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
        return JSONResponse(
            status_code=e.response.status_code,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_servicio"],
                "detalles_usuario": "Por favor intenta mas tarde o contacta a nuestro servicio al cliente."
            }
        )
    except Exception as e:
        logger.error(f"Error interno: {str(e)}")
        await error_notify(method_name, client_id, f"Error interno: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_general"],
                "detalles_usuario": "Nuestro equipo tecnico ha sido notificado y esta trabajando en solucionarlo."
            }
        )


#manejar diferentes casos de entrada para el valor "principal" y extraer solo los nÃƒÂºmeros
async def limpiar_valor_principal(raw_principal: str) -> float:
    """
    Limpia y extrae el valor numÃƒÂ©rico de una cadena que contiene un monto.
    
    Args:
        raw_principal (str): Cadena con el valor principal en diferentes formatos
        
    Returns:
        float: Valor numerico extraido
        
    Raises:
        ValueError: Si no se puede extraer un valor numÃƒÂ©rico vÃƒÂ¡lido
    """
    if not raw_principal:
        raise ValueError("El valor principal no puede estar vacÃƒÂ­o")

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

    # Eliminar caracteres especiales y texto comÃƒÂºn
    palabras_a_eliminar = [
        'cop', 'pesos', 'valor', 'seria', 'de', 'quiero', 
        'financiar', 'necesito', 'el', 'aproximadamente',
        'como', 'cerca', 'millones', 'mil'
    ]
    
    for palabra in palabras_a_eliminar:
        valor = valor.replace(palabra, '')

    # Eliminar sÃƒÂ­mbolos monetarios y caracteres especiales
    valor = re.sub(r'[$ \'"]', '', valor)

    # Convertir puntos y comas usados como separadores de miles
    valor = valor.replace('.', '')
    valor = valor.replace(',', '')

    # Extraer solo dÃƒÂ­gitos
    numeros = re.findall(r'\d+', valor)
    
    if not numeros:
        raise ValueError(f"No se pudo extraer un valor numÃƒÂ©rico de: {raw_principal}")

    # Unir todos los nÃƒÂºmeros encontrados
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
    2. Plazo en dÃƒÂ­as = plazo_escogido * paymentFrequency
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
            raise HTTPException(status_code=400, detail="Debe incluir 'semestre' en el payload")
        
        numero_semestre = semestres_map.get(semestre_texto)
        if numero_semestre is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de semestre '{semestre_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El semestre '{semestre_texto}' no es válido. Use: primer semestre, segundo semestre, etc.")

        # --- PROCESAR PLAZO_VALOR_PAGAR, el dato entra en string y debe devolverse como un numero ---
        plazo_texto_raw = payload.get("plazo_valor_pagar", "")
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
            raise HTTPException(status_code=400, detail="Debe incluir 'plazo_valor_pagar' en el payload")

        plazo_valor = plazo_map.get(plazo_texto)
        if plazo_valor is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de plazo '{plazo_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El plazo '{plazo_texto}' no es valido. Use: a un mes, a dos meses, etc.")
        
        logger.info(f"plazo_valor procesado: {plazo_valor} \n")
        logger.info (f"numero de semestre procesado: {numero_semestre} \n")

        # Definir MENSAJES_USUARIO antes del try para que sea accesible en los bloques except
        MENSAJES_USUARIO = {
            "valor_invalido": "El monto ingresado no es vÃƒÂ¡lido. Por favor ingresa un valor numerico, por ejemplo: 2500000 o $2.500.000",
            "linea_no_existe": "Lo sentimos, el producto financiero seleccionado no estÃƒÂ¡ disponible en este momento. Por favor intenta nuevamente mÃƒÂ¡s tarde.",
            "semestre_invalido": "El semestre ingresado no es valido. Por favor selecciona una opcion entre 'primer semestre' y 'dÃƒÂ©cimo semestre'.",
            "plazo_invalido": "El plazo seleccionado no es valido. Por favor escoge entre 1 y 6 meses.",
            "error_conexion": "En este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
            "error_calculo": "Hubo un problema al calcular tu financiamiento. Por favor verifica los valores ingresados e intenta nuevamente.",
            "datos_faltantes": "Por favor completa todos los campos requeridos para calcular tu financiamiento."
        }

        try:
            raw_principal = str(payload.get("principal", "0"))
            principal = await limpiar_valor_principal(raw_principal)
        except ValueError as e:
            await error_notify(method_name, linea_producto_notify_error, f"Error en el valor principal: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Error en el valor principal: {str(e)}")

        # Porcentaje de cuota (sin sÃƒÂ­mbolo %)
        porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        #plazo_escogido = int(payload.get("plazo_escogido", 0))
        #el plazo procesado de tipo string y transformado a numero es plazo_valor y se le asigna a plazo_escogido para los calculos
        plazo_escogido = plazo_valor
        payment_frequency = int(payload.get("paymentFrequency", 30))
        
        #logger.info (f"plazo escogido para realizar los calculos: {plazo_escogido} \n")
        

        # --- CÃƒLCULOS INICIALES ---
        valor_cuota_inicial = principal * porcentaje_cuota
        dias_totales = plazo_escogido * payment_frequency

        # --- CONSULTA A API KUENTA ---
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await obtener_token(client)
            if not token:
                await error_notify(method_name, linea_producto_notify_error, "No se pudo obtener token de autenticacion")
                raise HTTPException(status_code=401, detail="No se pudo obtener token de autenticacion")

            KUENTA_URL = f"https://api.kuenta.co/v1/products/{linea_producto}"
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": token
            }

            try:
                resp = await client.get(KUENTA_URL, headers=headers)
                resp.raise_for_status()
                product_data = resp.json().get("data", {}).get("product", {})
            except httpx.RequestError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de conexion con la API de Kuenta: {e}")
                raise HTTPException(status_code=502, detail=f"Error de conexion con la API de Kuenta: {e}")
            except httpx.HTTPStatusError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de respuesta de Kuenta: {e.response.text}")
                raise HTTPException(status_code=e.response.status_code, detail=f"Error de respuesta de Kuenta: {e.response.text}")

        # --- VALIDAR RESPUESTA ---
        if product_data.get("ID") != linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "El ID del producto no coincide")
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
        
        logger.info("CAlculo completado correctamente. \n")
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
        logger.error(f"Error de datos: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error de datos: {e}")
        return {
                "estado": "error",
                "mensaje": MENSAJES_USUARIO["valor_invalido"],
                "detalles_usuario": "Por favor verifica el valor del monto a financiar."
            }

    except HTTPException as e:
        mensaje_usuario = MENSAJES_USUARIO["datos_faltantes"]
        if "semestre" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["semestre_invalido"]
        elif "plazo" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["plazo_invalido"]
        elif "lÃƒÂ­nea_producto" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["linea_no_existe"]
            
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        return {
            "estado": "error",
            "mensaje": mensaje_usuario,
            "detalles_usuario": "Si el problema persiste, por favor comuni­cate con nuestro servicio al cliente."
        }

    except Exception as e:
        logger.error(f"Error interno inesperado: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error interno: {e}")
        return {
            "estado": "error", 
            "mensaje": MENSAJES_USUARIO["error_conexion"],
            "detalles_usuario": "Nuestro equipo tÃƒÂ©cnico ha sido notificado del inconveniente."
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
        logger.info(f"+++++ ParÃƒÂ¡metros recibidos en el body: creditid= ++++++++, \n {creditid} \n")
        logger.info(f"+++++ ParÃƒÂ¡metros recibidos en el body: installmentid= ++++++++, \n {installmentid} \n")
        logger.info(f"+++++ ParÃƒÂ¡metros recibidos en el body: orderid= ++++++++, \n {orderid} \n")
        

        if not creditid or not installmentid or not orderid:
            raise HTTPException(status_code=400, detail="Faltan parÃƒÂ¡metros obligatorios: creditid, installmentid, orderid")

        url = f"https://api.kuenta.co/v1/payable/{creditid}/installment/0/order/list/{orderid}"
        intentos = 3
        intervalo_segundos = 10
        intento = 0

        logger.info(f"ParÃƒÂ¡metros recibidos: creditid={creditid}, installmentid={installmentid}, orderid={orderid}")

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
                    
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    status = data.get("status")
                    logger.info(f"Intento {intento}: status del pago = {status}")
                    
                    if status != "pending":
                        logger.info(f"Estado final obtenido: {status} en el intento {intento} \n")
                        logger.info(f"Respuesta completa: {data} \n")
                        return data
                    
                except Exception as e:
                    logger.error(f"Error en intento {intento}: {str(e)}")
                    await error_notify(method_name, debtor_id_notify_error, f"Error en intento: {intento} {str(e)}")
                    
                if intento < intentos:
                    await asyncio.sleep(intervalo_segundos)
        return {"mensaje": "No se obtuvo un estado diferente a 'pending' tras 3 intentos"}
    
    except Exception as e:
        logger.error(f"Error en el proceso: {str(e)}")
        await error_notify(method_name, debtor_id_notify_error, f"Error en el proceso: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el proceso: {str(e)}")
    

#### endpoints para pruebas de notificaciones ###

# Endpoint que llama a error_notify (envÃƒÂ­a email + telegram)
@app.post("/test-notify")
async def test_notify(payload: TestNotifyRequest = Body(...)):
    try:
        result = await error_notify(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-notify")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# Endpoint para probar solo envÃƒÂ­o por email
@app.post("/test-email")
async def test_email(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_email(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-email")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# Endpoint para probar solo envÃƒÂ­o a Telegram
@app.post("/test-telegram")
async def test_telegram(payload: TestNotifyRequest = Body(...)):
    try:
        result = await send_log_telegram(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-telegram")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


# Endpoint para enviar correo de renovaciÃƒÂ³n de crÃƒÂ©dito con validaciones
@app.post("/Correo_post_llamada", summary="Receptor de variables despues de la llamada",description="Recibe el payload con las variables de entrada y extraÃƒÂ­das.",tags=["Correo_post_llamada"])
async def handle_webhook(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Endpoint principal que recibe el payload del webhook.

    1.  Valida automaticamente el payload contra el modelo `WebhookPayload`.
    2.  Llama al servicio `procesar_webhook` para manejar toda la logica.
    3.  Retorna una respuesta JSON.
    """
    try:
        logging.info(f"Webhook recibido. Procesando para: {payload.input_variables.NOMBRE_TITULAR} \n")
        
        logging.debug(f"Payload completo recibido: {payload.model_dump_json(indent=2)} \n")
        
        logging.info(f"Objetivo extraido: {payload.extracted_variables.objetivo} \n")
        
        # Logica de enrutamiento de el envio de los correos basada en el objetivo de la llamada de cada agente IA
        objetivo = payload.extracted_variables.objetivo
        logging.info(f"Objetivo a procesar: {objetivo}")
        
        if objetivo == "webinar":
            logging.info("El objetivo es 'webinar'. Llamando a procesar_webhook_webinar.")
            resultado = await procesar_webhook_webinar(payload)
            logging.info(f"Procesamiento completado para webinar: {payload.input_variables.NOMBRE_TITULAR}")
            
            #Validar explicitamente el resultado
            if resultado.get("status") == "success" and resultado.get("correo_enviado"):
                logging.info(f"Webhook webinar EXITOSO: Correo enviado en {resultado.get('intentos_correo')} intento(s)")
                await info_notify(
                    method_name="webhook_webinar",
                    client_id=objetivo,
                    info_message=f"Webhook de webinar completado exitosamente. Correo enviado en {resultado.get('intentos_correo')} intento(s) para {payload.input_variables.NOMBRE_TITULAR}"
                )
                return JSONResponse(status_code=200,
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
                    error_message=f"Webhook webinar con problemas: {resultado.get('message')}"
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
            if resultado.get("status") == "success" and resultado.get("correo_enviado"):
                logging.info(f"Webhook renovacion EXITOSO: Correo enviado en {resultado.get('intentos_correo')} intento(s)")
                await info_notify(
                    method_name="handle_webhook_renovacion",
                    client_id=objetivo,
                    info_message=f"Webhook de renovacion completado exitosamente. Correo enviado en {resultado.get('intentos_correo')} intento(s) para {payload.input_variables.NOMBRE_TITULAR}"
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
                logging.warning(f"⚠️ Webhook renovacion con problemas: {resultado}")
                await error_notify(
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
        
        else:
            logging.error(f"❌ Objetivo desconocido: {objetivo}")
            await error_notify(
                method_name="handle_webhook",
                client_id="unknown",
                error_message=f"Objetivo desconocido en webhook: {objetivo}"
            )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "error",
                    "message": f"Objetivo '{objetivo}' no reconocido. Valores validos: 'webinar', 'renovacion'",
                    "correo_enviado": False,
                }
            )

    except Exception as e:
        # No devolver 500 para evitar que el proveedor del webhook reintente
        # y provoque envíos duplicados. Registramos y notificamos, y
        # respondemos 200 con detalle del error interno.
        logging.error(f"Error en el endpoint /webhook: {str(e)}", exc_info=True)
        try:
            await error_notify(
                method_name="handle_webhook",
                client_id=(payload.input_variables.NOMBRE_TITULAR if payload and payload.input_variables else "unknown"),
                error_message=f"Error en el endpoint /webhook: {str(e)}",
            )
        except Exception:
            logging.exception("Fallo al enviar notificacion de error")
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "message": "Error interno (registrado). No se reintentará desde el servidor.",
                "detail": str(e),
            },
        )


# Endpoint para registrar renovaciones en la base de datos
@app.post("/renovaciones", tags=["Renovaciones"], summary="Registrar renovaciÃƒÂ³n de cliente")
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
        
        logger.info(f"Intentando registrar renovaciÃƒÂ³n para: {payload.nombre_cliente}")
        
        # Crear conexiÃƒÂ³n asincrÃƒÂ³nica a la base de datos
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
                
                # Ejecutar la inserciÃƒÂ³n
                await cursor.execute(
                    query,
                    (
                        payload.estado_final_renovacion,
                        payload.estado_pago_payvalida,
                        payload.nombre_cliente
                    )
                )
                
                # Confirmar la transacciÃƒÂ³n
                await connection.commit()
                
                # Obtener el ID de la renovaciÃƒÂ³n insertada
                insertado_id = cursor.lastrowid
                
                logger.info(f"RenovaciÃƒÂ³n registrada exitosamente con ID: {insertado_id}")
                
                # Enviar notificaciÃƒÂ³n informativa
                info_message = (
                    f"RenovaciÃƒÂ³n registrada exitosamente en la base de datos\n"
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
                        "message": "RenovaciÃƒÂ³n registrada exitosamente",
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
        logger.error(f"Error de base de datos: {str(db_error)}")
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
                "detail": "No se pudo registrar la renovaciÃƒÂ³n"
            }
        )
    
    except Exception as e:
        logger.error(f"Error en registrar_renovacion: {str(e)}")
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


@app.get("/logs")
async def get_logs(limit: int = 20):
    """
    Devuelve los ÃƒÂºltimos logs enviados (correo + Telegram).
    Se puede consultar por Postman o navegador.
    Manejo seguro cuando la cachÃƒÂ© estÃƒÂ© vacÃƒÂ­a o ocurra un error.
    """
    try:
        logs = await get_cached_logs(limit)
        if not logs:
            return {"count": 0, "logs": []}
        return {"count": len(logs), "logs": logs}
    except Exception as e:
        logger.exception("Error al obtener logs desde la cachÃƒÂ©")
        # No lanzar excepciÃƒÂ³n para no interrumpir el servidor; devolver estructura vacÃƒÂ­a
        return {"count": 0, "logs": []}
