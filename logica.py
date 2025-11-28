from fastapi import FastAPI, HTTPException, Body,Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from utils.notify_error import error_notify, get_cached_logs,send_log_email, send_log_telegram,info_notify
from utils.enviar_correo_IA import procesar_webhook_renovacion, procesar_webhook_webinar
from models.models import WebhookPayload
import httpx
import logging
import asyncio
from fastapi import Request
import time
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from utils.config import settings, TOKEN_DATA
from dotenv import load_dotenv
import re
import aiomysql
        
# Obtener parámetros de configuración DE LA BASE DE DATOS
db_host = settings.DB_HOST
db_user = settings.DB_USER
db_pass = settings.DB_PASSWORD_RENOVACION
db_name = settings.DB_NAME_RENOVACION
#import os
# import json
# load_dotenv()

#app = FastAPI()
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
        # Código que se ejecuta al iniciar
        message = (
            "🟢 Servidor iniciado correctamente\n"
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Ambiente: Producción\n"
            "Estado: ONLINE"
        )
        await error_notify(
            method_name="startup_server",
            client_id="system",
            error_message=message
        )
        logger.info("Servidor iniciado y notificaciones enviadas correctamente")
        
        yield  # El servidor está ejecutándose
        
        # Código que se ejecuta al apagar
        message = (
            "🔴 Servidor detenido\n"
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

# Crear la aplicación FastAPI con el nuevo manejador lifespan
app = FastAPI(lifespan=lifespan)

# El middleware para detectar reinicios
@app.middleware("http")
async def check_server_restart(request: Request, call_next):
    """
    Middleware que detecta reinicios del servidor por cambios en el código
    """
    try:
        if not hasattr(app.state, "server_started"):
            app.state.server_started = True
            message = (
                "🔄 Servidor actualizado y reiniciado\n"
                f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"Ambiente: Producción\n"
                "Estado: RELOADED"
            )
            await error_notify(
                method_name="server_reload",
                client_id="system",
                error_message=message
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

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# Pydantic ya los parseó como diccionarios
AUTH_PAYLOAD_PROD = settings.AUTH_PAYLOAD_PROD 
AUTH_PAYLOAD_DEMO = settings.AUTH_PAYLOAD_DEMO

# Y las otras variables también
AUTH_URL = settings.AUTH_URL
API_URL = settings.API_URL
ORG_ID = settings.ORG_ID
PAYABLE_URL = settings.PAYABLE_URL
GET_PAYABLE_URL = settings.GET_PAYABLE_URL

#print("variables de entorno cargadas: \n AUTH_PAYLOAD: {AUTH_PAYLOAD} \n AUTH_URL: {AUTH_URL} \n API_URL: {API_URL} \n ORG_ID: {ORG_ID} \n PAYABLE_URL: {PAYABLE_URL} \n")


# Variables globales para el token y su expiración
TOKEN_DATA = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0
}

# --- Sistema de Caché en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

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
    message: str = "Mensaje de prueba para notificación"


# Función para obtener o refrescar el token
async def obtener_token(client: httpx.AsyncClient):
    """
    Obtiene el token de acceso, refresca si está expirado.
    """
    ahora = int(time.time())
    # Si el token existe y no ha expirado, lo retorna
    if TOKEN_DATA["access_token"] and TOKEN_DATA["expires_at"] > ahora:
        print("Token válido, no es necesario refrescar.")
        return TOKEN_DATA["access_token"]
    # Si hay refresh_token, intenta refrescar
    if TOKEN_DATA["refresh_token"]:
        refresh_payload = {
            "grant_type": "refresh_token",
            "refresh_token": TOKEN_DATA["refresh_token"],
            "client_id": AUTH_PAYLOAD_PROD["client_id"],
            "client_secret": AUTH_PAYLOAD_PROD["client_secret"]
        }
        response = await client.post(AUTH_URL, json=refresh_payload)
        response.raise_for_status()
        auth_data = response.json()
        TOKEN_DATA["access_token"] = auth_data.get("access_token")
        TOKEN_DATA["refresh_token"] = auth_data.get("refresh_token")
        
        print("Token refrescado exitosamente.")
        
        expires_in = auth_data.get("expires_in", 3600)
        TOKEN_DATA["expires_at"] = ahora + expires_in - 60  # margen de 1 min
        return TOKEN_DATA["access_token"]
    # Si no hay token, solicita uno nuevo
    print("Solicitando nuevo token de acceso.")
    print(f"Payload de autenticación: {AUTH_PAYLOAD_PROD}")
    
    response = await client.post(AUTH_URL, json=AUTH_PAYLOAD_PROD)
    response.raise_for_status()
    auth_data = response.json()
    
    TOKEN_DATA["access_token"] = auth_data.get("access_token")
    TOKEN_DATA["refresh_token"] = auth_data.get("refresh_token")
    
    expires_in = auth_data.get("expires_in", 3600)
    TOKEN_DATA["expires_at"] = ahora + expires_in - 60  # margen de 1 min
    print("Nuevo token obtenido exitosamente.")
    return TOKEN_DATA["access_token"]



# Endpoint para obtener detalles de una cuota específica desde la caché
@app.post("/detalle_cuota_vencida", response_class=JSONResponse)
async def obtener_detalle_cuota(request: DetalleCuotaRequest = Body(...)):
    """
    PASO 2: Consulta la caché para obtener los detalles de UNA SOLA cuota.
    Devuelve una respuesta estructurada que el bot puede interpretar.
    """
    id_credito = request.id_cliente
    numero_cuota = request.numero_cuota

    cache_entry = cuotas_cache.get(id_credito)
    
    if not cache_entry:
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "clave_error": "SESION_NO_ENCONTRADA",
                "mensaje": "No se encontraron cuotas para este cliente. El bot debería volver a ejecutar la consulta inicial."
            }
        )

    cache_time = cache_entry["timestamp"]
    if datetime.now(timezone.utc) - cache_time > timedelta(minutes=CACHE_TTL_MINUTES):
        del cuotas_cache[id_credito]
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "clave_error": "SESION_EXPIRADA",
                "mensaje": "La información ha expirado. El bot debe notificar al usuario y volver a consultar."
            }
        )

    lista_cuotas = cache_entry["cuotas"]

    if not (1 <= numero_cuota <= len(lista_cuotas)):
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "clave_error": "INDICE_INVALIDO",
                "mensaje": f"Número de cuota inválido. Por favor, elija un número entre 1 y {len(lista_cuotas)}."
            }
        )
    
    cuota_solicitada = lista_cuotas[numero_cuota - 1]
    
    return JSONResponse(
        status_code=200,
        content={
            "status": "exito",
            "cuota": cuota_solicitada
        }
    )


# Endpoint para obtener líneas de producto basado en parentId
@app.get("/product-lines/{parent_id}")
async def webhook_product_lines(parent_id: str):
    """
    Webhook asíncrono:
    1. Solicita token de autorización al AUTH_URL
    2. Usa el token para consultar API_URL
    3. Retorna solo id, parentId y name
    """
    
    MAX_RETRIES = 3  # número máximo de intentos
    RETRY_DELAY = 5  # segundos entre intentos (base)
    TIMEOUT = 15     # timeout en segundos por solicitud
    
    method_name = "product-lines"
    parent_id_notify_error = f"parent_id para la busqueda del la linea={parent_id}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            access_token = await obtener_token(client)
            if not access_token:
                msg = "No se pudo obtener el token de acceso"
                await error_notify(method_name, parent_id_notify_error, msg)
                return {
                    "estado": "error",
                    "mensaje": MENSAJES_CLIENTE["error_conexion"],
                    "detalles_usuario": "No se pudo obtener el token de acceso. Por favor intenta nuevamente más tarde."
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

                    for line in lines:
                        if line.get("parentId") == parent_id:
                            logger.info(f"Línea encontrada: {line}")
                            return {
                                "id": line.get("id"),
                                "name": line.get("name"),
                                "parentId": line.get("parentId"),
                                "principalMin": line.get("principalMin"),
                                "principalMax": line.get("principalMax"),
                                "timeMin": line.get("timeMin"),
                                "timeMax": line.get("timeMax"),
                            }

                    msg = f"No se encontró la línea con parentId: {parent_id}"
                    await error_notify(method_name, parent_id_notify_error, msg)
                    return {
                        "estado": "error",
                        "mensaje": MENSAJES_CLIENTE["error_servicio"],
                        "detalles_usuario": "No se encontró la línea de producto solicitada. Por favor verifica el código o intenta más tarde."
                    }

                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                    logger.warning(f"Intento {attempt}/{MAX_RETRIES} fallido por timeout o conexión: {e}")
                    if attempt == MAX_RETRIES:
                        return {
                            "estado": "error",
                            "mensaje": MENSAJES_CLIENTE["error_conexion"],
                            "detalles_usuario": "No se pudo conectar con el servicio externo. Por favor intenta nuevamente más tarde."
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
                        "detalles_usuario": "El servicio externo no está disponible. Por favor intenta más tarde."
                    }

            msg = "Error persistente al consultar API externa"
            await error_notify(method_name, parent_id_notify_error, msg)
            return {
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_conexion"],
                "detalles_usuario": "No se pudo obtener respuesta del servicio externo. Por favor intenta más tarde."
            }

    except Exception as e:
        logger.error(f"Error general en webhook_product_lines: {e}")
        await error_notify(method_name, parent_id_notify_error, f"Error general: {e}")
        return {
            "estado": "error",
            "mensaje": MENSAJES_CLIENTE["error_general"],
            "detalles_usuario": "Ocurrió un error inesperado. Por favor intenta nuevamente más tarde."
        }

def format_currency(value: float) -> str:
    """Formatea un número como moneda COP: sin decimales, con $ y separadores de miles."""
    return f"${value:,.0f}"

# Endpoint para crear un nuevo payable o credito despues de la simulacion
@app.post("/payable/{client_id}")
async def create_payable(client_id: str, payload: PayableRequest):
    """
    Endpoint para crear un nuevo payable:
    1. Recibe el ID del cliente como parámetro
    2. Transforma los campos principal y initialFee de str a int
    3. Extrae el token de autorización del payload
    4. Realiza la petición POST al endpoint de payable
    
    """
    method_name = "create_payable"
    try:

        async with httpx.AsyncClient() as client:
            
            logger.info(f"+++++ Parámetros recibidos: client_id= ++++++++, \n {client_id} \n")
            
            logger.info(f"#####--- Payload entrante ----#### \n {payload} \n")
            
            principal = payload.principal
            initial_fee = payload.initialFee
            
            token = await obtener_token(client)
            #token = "v2.public.eyJjbGllbnQiOiIwNDY4Mzc1Yi0wYzAyLTQxNDMtYmY2NS03Njc0NDk3MTA0NmYiLCJjdHgiOiJ1c2VyIiwiZXhwIjoiMjAyNS0xMC0yOVQxNjo0MjowOS0wNTowMCIsImp0aSI6IjRhODExMmJlLWRmOWEtNDBmNC1iYTU1LTU1Y2ZiYzFmZGUzNyIsInNlc3Npb24iOiI3MDhjZGYzNi1kZmI5LTQ4M2MtODRiMy1iYTlhZTNiZGM2MDIiLCJ0eXBlIjoiYXV0aCIsInVzZXIiOiJlODI5YTY3NC04MDE3LTRhYWEtYTJlNy00MjI1YWEyOGFjZTMiLCJ2ZXJpZmllZCI6InRydWUifdxXSkJr8ChbO7Hoea_4eCj0OEuNMOvLj6Xqt2gfX92iqdwJsjhAK-cznEbl8i0jon-F1nLf8WNtGzBCQa6ucQQ.bnVsbA"
            
            logger.info(f"Token de autorización extraído: {token} \n")
            
            new_payload = {
                "creditLineID": payload.creditLineID,
                #"creditLineID": "97c6a459-a86c-4e01-bfa1-7b3e21acddf3",
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
            logger.info(f"Iniciando petición POST a {PAYABLE_URL}")
            logger.info(f"Payload transformado para enviar a kuenta: {new_payload}")

            max_retries = 3
            for attempt in range(max_retries):
                
                try:
                    response = await client.post(
                        PAYABLE_URL,
                        #f"https://demo-api.kuenta.co/v1/payable",
                        json=new_payload,
                        headers=headers
                    )
                    status_code = response.status_code
                    logger.info(f"Intento {attempt+1}: status_code={status_code}")
                    if status_code == 201:
                        #logger.info("Petición completada exitosamente")
                        # Si hay contenido, retorna el JSON, si no retorna vacío
                            logger.info("Procesando respuesta de Kuenta")
                            response_data = response.json()
                            credit = response_data.get("data", {}).get("credit", {})
                            
                            # ID credito
                            response_credit_id = credit.get("ID")
                            logger.info(f"ID del crédito creado: {response_credit_id} \n")
                            #return response_data
                        
                    try:
                            # url en demo
                            #url = GET_PAYABLE_URL + response_credit_id
                            #print("url de la simulacion es: ", url)
                            
                            #url en produccion
                            url_prod = f"https://api.kuenta.co/v1/payable/{response_credit_id}"
                            # Realizar la consulta GET
                            response_get_simulacion = await client.get(url_prod, headers=headers)
                            status_code_simulacion = response_get_simulacion.status_code
                            
                            logger.info(f"Status code de la simulación: {status_code_simulacion}")
                            
                            
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
                                        "ID del crédito creado": response_credit_id,
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

                                    logger.info("Valores extraídos y formateados exitosamente")
                                    logger.info(f"Valores formateados: {formatted_values}")
                                    
                                    #notificación informativa a telegram y email
                                    info_message = f"Crédito creado y regsitrado en kuenta correctamente \n ID del crédito: {ID_credito} \n Referencia del credito :{referencia_credito}\n ID del cliente :{id_cliente} \n Valor total credito:{formatted_values['payment_formatted']}"
                                    
                                    # envia notificación informativa (email + telegram) con id para seguimiento
                                    await info_notify(method_name, client_id, info_message, entity_id=str(id_cliente))
                
                                    return response_data
                                else:
                                    logger.error("No se encontraron installments en la respuesta")
                                    raise HTTPException(status_code=404, detail="No se encontraron cuotas en la simulación")
                
                            else:
                                logger.error(f"Error en la consulta de simulación: {status_code_simulacion}")
                                raise HTTPException(status_code=status_code_simulacion, 
                                                detail="Error al consultar la simulación")
                            
                    except httpx.HTTPStatusError as e:
                            await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {str(e)}")
                            logger.error(f"Intento {attempt+1}: Error en la respuesta de la API externa kuenta: {e.response.status_code}")
                    except Exception as e:
                        logger.warning(f"No se pudo enviar notificación informativa: {e}")
                        
                except httpx.HTTPStatusError as e:
                        logger.error(f"Intento {attempt+1}: Error en API externa: {e.response.status_code}")
                        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # espera exponencial
            # Si no se logró en los reintentos
            await error_notify(method_name, client_id, f"Error de conexión tras: {max_retries} intentos o respuesta no válida")
            raise HTTPException(status_code=502, detail=f"Error de conexión tras {max_retries} intentos o respuesta no válida")
        
    except ValueError as e:
        logger.error(f"Error de conversión de datos: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conversión de datos: {str(e)}")
        return JSONResponse(
            status_code=400,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_datos"],
                "detalles_usuario": "Recuerda ingresar solo números en los campos de monto y cuota inicial."
            }
        )
        
    except httpx.RequestError as e:
        logger.error(f"Error de conexión: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conexión: {str(e)}")
        return JSONResponse(
            status_code=502,
            content={
                "estado": "error",
                "mensaje": MENSAJES_CLIENTE["error_conexion"],
                "detalles_usuario": "Nuestro servicio está experimentando problemas de conexión temporales."
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
                "detalles_usuario": "Por favor intenta más tarde o contacta a nuestro servicio al cliente."
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
                "detalles_usuario": "Nuestro equipo técnico ha sido notificado y está trabajando en solucionarlo."
            }
        )


#manejar diferentes casos de entrada para el valor "principal" y extraer solo los números
def limpiar_valor_principal(raw_principal: str) -> float:
    """
    Limpia y extrae el valor numérico de una cadena que contiene un monto.
    
    Args:
        raw_principal (str): Cadena con el valor principal en diferentes formatos
        
    Returns:
        float: Valor numérico extraído
        
    Raises:
        ValueError: Si no se puede extraer un valor numérico válido
    """
    if not raw_principal:
        raise ValueError("El valor principal no puede estar vacío")

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
        raise ValueError(f"No se pudo convertir a número: {valor_limpio}") from e

# Ejemplo de uso:

#calcular financiamineto version 2
@app.post("/calcular_financiamiento")
async def calcular_financiamiento(payload: dict):
    """
    Calcula el financiamiento basado en:
    1. Cuota inicial = principal * porcentaje_cuota
    2. Plazo en días = plazo_escogido * paymentFrequency
    3. Consulta a la API Kuenta para obtener el porcentaje de Aval
    4. Calcula desembolso, deducciones y valor a solicitar
    """
    method_name = "calcular_financiamiento"
    linea_producto_notify_error = f"línea_producto={payload.get('linea_producto')}"

    try:
        logger.info(f"###--- Payload recibido: ###---  \n {payload} \n")

        # --- VALIDACIONES DE ENTRADA ---
        linea_producto = payload.get("linea_producto")
        logger.info(f"linea_producto recibido: {linea_producto} \n")
        if not linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'linea_producto' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'linea_producto' en el payload")
        
        # --- PROCESAR SEMESTRE, el dato entra como una palabra ejemplo "segundo semestre" y debe devolver el numero del semestre ---
        semestre_texto = str(payload.get("semestre_renovación_menu", "")).strip().lower()
        semestres_map = {
            "primer semestre": 1,
            "segundo semestre": 2,
            "tercer semestre": 3,
            "cuarto semestre": 4,
            "quinto semestre": 5,
            "sexto semestre": 6,
            "séptimo semestre": 7,
            #"septimo semestre": 7,   # variante sin tilde
            "octavo semestre": 8,
            "noveno semestre": 9,
            "décimo semestre": 10
            #"decimo semestre": 10    # variante sin tilde
        }
        
        if not semestre_texto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'semestre' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'semestre' en el payload")
        
        numero_semestre = semestres_map.get(semestre_texto)
        if numero_semestre is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de semestre '{semestre_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El semestre '{semestre_texto}' no es válido. Use: primer semestre, segundo semestre, etc.")

        # --- PROCESAR PLAZO_VALOR_PAGAR, el dato entra en string y debe devolverse como un numero ---
        plazo_texto = str(payload.get("plazo_valor_pagar")).strip().lower()
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
            raise HTTPException(status_code=400, detail=f"El plazo '{plazo_texto}' no es válido. Use: a un mes, a dos meses, etc.")
        
        logger.info(f"plazo_valor procesado: {plazo_valor} \n")
        logger.info (f"numero de semestre procesado: {numero_semestre} \n")

        try:
            raw_principal = str(payload.get("principal", "0"))
            principal = limpiar_valor_principal(raw_principal)
        except ValueError as e:
            await error_notify(method_name, linea_producto_notify_error, f"Error en el valor principal: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Error en el valor principal: {str(e)}")

        # Porcentaje de cuota (sin símbolo %)
        porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        #plazo_escogido = int(payload.get("plazo_escogido", 0))
        #el plazo procesado de tipo string y transformado a numero es plazo_valor y se le asigna a plazo_escogido para los calculos
        plazo_escogido = plazo_valor
        payment_frequency = int(payload.get("paymentFrequency", 30))
        
        #logger.info (f"plazo escogido para realizar los calculos: {plazo_escogido} \n")
        

        # --- CÁLCULOS INICIALES ---
        valor_cuota_inicial = principal * porcentaje_cuota
        dias_totales = plazo_escogido * payment_frequency

        # --- CONSULTA A API KUENTA ---
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await obtener_token(client)
            if not token:
                await error_notify(method_name, linea_producto_notify_error, "No se pudo obtener token de autenticación")
                raise HTTPException(status_code=401, detail="No se pudo obtener token de autenticación")

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
                await error_notify(method_name, linea_producto_notify_error, f"Error de conexión con la API de Kuenta: {e}")
                raise HTTPException(status_code=502, detail=f"Error de conexión con la API de Kuenta: {e}")
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
            await error_notify(method_name, linea_producto_notify_error, "No se encontró porcentaje de Aval en el producto")
            raise HTTPException(status_code=404, detail="No se encontró porcentaje de Aval en el producto")
        logger.info(f"Porcentaje de Aval obtenido de la linea: {aval_porcentaje}% \n")
        # --- CÁLCULOS FINALES ---
        valor_desembolsar = principal - valor_cuota_inicial
        if (1 - aval_porcentaje) == 0:
            await error_notify(method_name, linea_producto_notify_error, "El porcentaje de aval no puede ser 100%")
            raise ValueError("El porcentaje de aval no puede ser 100%.")

        valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        deducciones_anticipadas = valor_solicitar * aval_porcentaje
        
        # --- FORMATEO PARA DEMOSTRACIÓN ---
        demostracion_valor_producto = f"${principal:,.0f}"
        demostracion_cuota_inicial = f"${valor_cuota_inicial:,.0f}"
        demostracion_valor_desembolsar = f"${valor_desembolsar:,.0f}"
        demostracion_deducciones = f"${deducciones_anticipadas:,.0f}"
        demostracion_valor_solicitar = f"${valor_solicitar:,.0f}"
        
        logger.info (f"numero de semestre procesado: {numero_semestre} semestre \n")
        logger.info(f"plazo_valor_pagar procesado: {plazo_valor} meses \n")
        
        logger.info(f"----- Resumen de cálculos realizados ----- \n")
        logger.info(f"Valor del producto (principal): {demostracion_valor_producto} \n")
        logger.info(f"Cuota inicial (valor_cuota_inicial): {demostracion_cuota_inicial} \n")
        logger.info(f"Valor a desembolsar (valor_desembolsar): {demostracion_valor_desembolsar} \n")
        logger.info(f"Deducciones anticipadas (deducciones_anticipadas): {demostracion_deducciones} \n")
        logger.info(f"Valor a solicitar (valor_solicitar): {demostracion_valor_solicitar} \n")
        logger.info(f"Aval aplicado porcentaje (aval_porcentaje): {aval_porcentaje} \n")
        logger.info(f"Plazo en días (plazo_dias): {dias_totales} \n")
        logger.info(f"Porcentaje escogido (porcentaje_str): {porcentaje_str}% \n")
        
        logger.info("Cálculo completado correctamente. \n")
        logger.info("-------------fin de la ejecución------------------ \n")
        
        
        MENSAJES_USUARIO = {
            "valor_invalido": "El monto ingresado no es válido. Por favor ingresa un valor numérico, por ejemplo: 2500000 o $2.500.000",
            "linea_no_existe": "Lo sentimos, el producto financiero seleccionado no está disponible en este momento. Por favor intenta nuevamente más tarde.",
            "semestre_invalido": "El semestre ingresado no es válido. Por favor selecciona una opción entre 'primer semestre' y 'décimo semestre'.",
            "plazo_invalido": "El plazo seleccionado no es válido. Por favor escoge entre 1 y 6 meses.",
            "error_conexion": "En este momento no podemos procesar tu solicitud. Por favor intenta nuevamente en unos minutos.",
            "error_calculo": "Hubo un problema al calcular tu financiamiento. Por favor verifica los valores ingresados e intenta nuevamente.",
            "datos_faltantes": "Por favor completa todos los campos requeridos para calcular tu financiamiento."
        }
        
        #notificacion informativa
        info_message = f"Cálculo de financiamiento realizado correctamente en etapa de simulacion \n ID linea de producto: {linea_producto}"
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
            
            # Agregar valores formateados para demostración
            "valor_producto_demostracion": demostracion_valor_producto,
            "cuota_inicial_demostracion": demostracion_cuota_inicial,
            "valor_desembolsar_demostracion": demostracion_valor_desembolsar,
            "deducciones_anticipadas_demostracion": demostracion_deducciones,
            "valor_solicitado_demostracion": demostracion_valor_solicitar
        }

    except ValueError as e:
        logger.error(f"Error de datos: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error de datos: {e}")
        #raise HTTPException(status_code=400, detail=str(e))
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
        elif "línea_producto" in str(e.detail):
            mensaje_usuario = MENSAJES_USUARIO["linea_no_existe"]
            
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        return {
            "estado": "error",
            "mensaje": mensaje_usuario,
            "detalles_usuario": "Si el problema persiste, por favor comunícate con nuestro servicio al cliente."
        }

    except Exception as e:
        logger.error(f"Error interno inesperado: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error interno: {e}")
        return {
            "estado": "error", 
            "mensaje": MENSAJES_USUARIO["error_conexion"],
            "detalles_usuario": "Nuestro equipo técnico ha sido notificado del inconveniente."
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
        logger.info(f"+++++ Parámetros recibidos en el body: creditid= ++++++++, \n {creditid} \n")
        logger.info(f"+++++ Parámetros recibidos en el body: installmentid= ++++++++, \n {installmentid} \n")
        logger.info(f"+++++ Parámetros recibidos en el body: orderid= ++++++++, \n {orderid} \n")
        

        if not creditid or not installmentid or not orderid:
            raise HTTPException(status_code=400, detail="Faltan parámetros obligatorios: creditid, installmentid, orderid")

        url = f"https://api.kuenta.co/v1/payable/{creditid}/installment/0/order/list/{orderid}"
        intentos = 3
        intervalo_segundos = 10
        intento = 0

        logger.info(f"Parámetros recibidos: creditid={creditid}, installmentid={installmentid}, orderid={orderid}")

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

# Endpoint que llama a error_notify (envía email + telegram)
@app.post("/test-notify")
async def test_notify(payload: TestNotifyRequest = Body(...)):
    try:
        result = await error_notify(payload.method_name, payload.client_id, payload.message)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except Exception as e:
        logger.exception("Error en /test-notify")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# Endpoint para probar solo envío por email
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


# Endpoint para enviar correo de renovación de crédito con validaciones
@app.post("/Correo_post_llamada", summary="Receptor de variables despues de la llamada",description="Recibe el payload con las variables de entrada y extraídas.",tags=["Correo_post_llamada"])
async def handle_webhook(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Endpoint principal que recibe el payload del webhook.

    1.  Valida automáticamente el payload contra el modelo `WebhookPayload`.
    2.  Llama al servicio `procesar_webhook` para manejar toda la lógica.
    3.  Retorna una respuesta JSON.
    """
    try:
        logging.info(f"Webhook recibido. Procesando para: {payload.input_variables.NOMBRE_TITULAR} \n")
        
        logging.debug(f"Payload completo recibido: {payload.model_dump_json(indent=2)} \n")
        
        # Lógica de enrutamiento de el envio de los correos basada en el objetivo de la llamada de cada agente IA
        if payload.extracted_variables.objetivo == "webinar":
            logging.info("El objetivo es 'webinar'. Llamando a procesar_webhook_webinar.")
            resultado = await procesar_webhook_webinar(payload)
            logging.info(f"Procesamiento completado para webinar: {payload.input_variables.NOMBRE_TITULAR}")
            return {"status": "success", "message": "Webhook de webinar procesado", "data": resultado}
        
        elif payload.extracted_variables.objetivo == "renovacion":
            logging.info("El objetivo es 'renovacion'. Llamando a procesar_webhook_renovacion.")
            # Delega toda la lógica al servicio
            resultado = await procesar_webhook_renovacion(payload)
        
        logging.info(f"Procesamiento completado para: {payload.input_variables.NOMBRE_TITULAR}")
        return {"status": "success", "message": "Webhook procesado", "data": resultado}

    except Exception as e:
        logging.error(f"Error en el endpoint /webhook: {str(e)}", exc_info=True)
        # Lanza una excepción HTTP que FastAPI convertirá en una respuesta de error
        raise HTTPException(
            status_code=500, 
            detail=f"Error interno del servidor: {str(e)}"
        )


# Endpoint para registrar renovaciones en la base de datos
@app.post("/renovaciones", tags=["Renovaciones"], summary="Registrar renovación de cliente")
async def registrar_renovacion(payload: RenovacionPayload):
    """
    Endpoint para registrar una renovación de crédito en la base de datos.
    
    Recibe:
    - estado_final_renovacion: Estado final de la renovación
    - estado_pago_payvalida: Estado del pago en PayValida
    - nombre_cliente: Nombre del cliente
    
    Retorna:
    - Confirmación de inserción y mensaje de éxito
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
                "detail": "No se pudo registrar la renovación"
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