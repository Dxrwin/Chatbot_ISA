from fastapi import FastAPI, HTTPException, Body,Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from utils.notify_error import error_notify, get_cached_logs
import httpx
import logging
import asyncio
from fastapi import Request
#import redis.asyncio as redis
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import os
import json
load_dotenv()

app = FastAPI()

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno desde .env
AUTH_PAYLOAD_PROD_STR = os.getenv("AUTH_PAYLOAD_PROD")
AUTH_PAYLOAD_DEMO_STR = os.getenv("AUTH_PAYLOAD_DEMO")

AUTH_PAYLOAD_PROD = json.loads(AUTH_PAYLOAD_PROD_STR)
AUTH_PAYLOAD_DEMO = json.loads(AUTH_PAYLOAD_DEMO_STR)

AUTH_URL = os.getenv("AUTH_URL")
API_URL = os.getenv("API_URL")
ORG_ID = os.getenv("ORG_ID")
PAYABLE_URL = os.getenv("PAYABLE_URL")

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
    

#print("variables de entorno cargadas: \n AUTH_PAYLOAD: {AUTH_PAYLOAD} \n AUTH_URL: {AUTH_URL} \n API_URL: {API_URL} \n ORG_ID: {ORG_ID} \n PAYABLE_URL: {PAYABLE_URL} \n")

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

# Endpoint para obtener información del crédito y calcular días en mora
@app.post("/info_credito_mora")
async def obtener_info_credito(request: ClienteRequest = Body(...)):
    """
    Obtiene información del crédito y calcula días en mora por cuota.
    """
    
    async with httpx.AsyncClient() as client:
        id_credito = request.id_cliente
        token = await obtener_token(client)
        
        url = f"https://api.kuenta.co/v1/receivable/{id_credito}"
        
        
        headers = {
            "Authorization": token,
            "Config-Organization-ID": ORG_ID,
            "Organization-ID": ORG_ID
        }
        
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            #logging.info(f"Respuesta de la API: {data.get("data").get("credit")} \n")
        except Exception as e:
            logger.error(f"Error al obtener crédito: {e}")
            raise HTTPException(status_code=500, detail="Error al obtener información del crédito")
    
    principal = data.get("data").get("credit").get("principal")
    logging.info(f"principal: {principal} \n")
    
    fecha_inicio_credito = data.get("data").get("credit").get("startedAt")
    
    reference = data.get("data").get("credit").get("reference")
    logging.info(f"reference: {reference} \n")
    
    installments = data.get("data").get("credit").get("installments")
    logging.info(f"installments: {installments} \n")
    
    summary = data.get("data").get("credit").get("summary")
    logging.info(f"summary: {summary} \n")
    
    cuotas_str = ""
    total_cuotas = 0
    
    cuotas_vencidas = []
    
    for cuota in installments:
        status = cuota.get("status")
        if status in [3]:  # 3 = pendiente, 4 = vencida
            total_cuotas += 1
            created_at_str = cuota.get("date")
            
            # Formatear fecha a dd/mm/yy
            fecha_formateada = ""
            if created_at_str:
                fecha_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                fecha_formateada = fecha_dt.strftime("%d/%m/%y")
                
            estado = "CUOTA VENCIDA" if status == 4 else "CUOTA PENDIENTE"
            
            # Formatear valores numéricos
            payment = cuota.get("payment")
            payment_str = f"{round(payment):,}".replace(",", ".") if payment is not None else "N/A"
            
            
            cuotas_vencidas.append({
            "id": cuota.get("id"),
            "createdAt": fecha_formateada,
            "estatus": estado,
            "payment": payment_str,
            "dias de intereses en mora":cuota.get("debtInterestDays")
            })
            cuotas_str += (
                
                f"   //== Cuota número: {total_cuotas}, Estado: {estado} , Valor a pagar: {payment_str}, Fecha de creación: {fecha_formateada} , Días de intereses en mora: {cuota.get('debtInterestDays')} "
                " ==//---------------   "
                "----------------------------------------------------------------------------------------------     "
            )
    if cuotas_vencidas:
        cuotas_cache[id_credito] = {
            "timestamp": datetime.now(timezone.utc),
            "cuotas": cuotas_vencidas
        }
            
    # Buscar la cuota más reciente por createdAt
    cuota_reciente = None
    if installments:
        cuota_reciente = max(
            installments,
            key=lambda c: datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00"))
        )
        
    # Formatear datos de la cuota más reciente
    cuota_reciente_id = cuota_reciente.get("id") if cuota_reciente else None
    cuota_reciente_created = cuota_reciente.get("date") if cuota_reciente else None 
    cuota_reciente_payment = cuota_reciente.get("payment") if cuota_reciente else None
    cuota_reciente_payment_str = f"{round(cuota_reciente_payment):,}".replace(",", ".") if cuota_reciente_payment is not None else "N/A"
    cuota_reciente_fecha = ""
    if cuota_reciente_created:
        fecha_dt = datetime.fromisoformat(cuota_reciente_created.replace("Z", "+00:00"))
        cuota_reciente_fecha = fecha_dt.strftime("%d/%m/%y")

    # Formatear valores principales
    principal_str = f"{round(principal):,}".replace(",", ".") if principal is not None else "N/A"
    valor_mora_str = f"{round(summary.get('debt')):,}".replace(",", ".") if summary.get("debt") is not None else "N/A"
    valor_balance_str = f"{round(summary.get('balance')):,}".replace(",", ".") if summary.get("balance") is not None else "N/A"
    valor_pagado_str = f"{round(summary.get('paid')):,}".replace(",", ".") if summary.get("paid") is not None else "N/A"
    
    
    return {
        "reference_credito": reference,
        "fwcha_inicio_credito": fecha_inicio_credito,
        "valor total del credito": principal_str,
        "total_cuotas_vencidas_pendientes": total_cuotas,
        "valor total de mora": valor_mora_str,
        "dias en mora": summary.get("debtDays"),
        "valor total por pagar": valor_balance_str,
        "valor total pagado": valor_pagado_str,
        "detalle_cuotas_vencidas": cuotas_str.strip(),
        "cuotas_vencidas_pendientes": cuotas_vencidas,
        "cuota_mas_reciente_por_pagar": {
            "id": cuota_reciente_id,
            "createdAt": cuota_reciente_fecha,
            "payment": cuota_reciente_payment_str,
        }
    }


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
            #Obtener token (una sola vez)
            access_token = await obtener_token(client)
            if not access_token:
                msg = "No se pudo obtener el token de acceso"
                await error_notify(method_name, parent_id_notify_error, msg)
                raise HTTPException(status_code=401, detail=msg)

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": f"{access_token}"
            }

            #Intentar la consulta a la API externa con reintentos
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = await client.get(API_URL, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    lines = data.get("data", {}).get("lines", [])

                    # Buscar la línea
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

                    # Si no hay coincidencia
                    msg = f"No se encontró la línea con parentId: {parent_id}"
                    await error_notify(method_name, parent_id_notify_error, msg)
                    raise HTTPException(status_code=404, detail=msg)

                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                    logger.warning(f"Intento {attempt}/{MAX_RETRIES} fallido por timeout o conexión: {e}")
                    if attempt == MAX_RETRIES:
                        raise
                    await asyncio.sleep(RETRY_DELAY * attempt)  # reintento exponencial

                except httpx.HTTPStatusError as e:
                    # Si la API devuelve error, registrar y romper
                    logger.error(f"Error HTTP {e.response.status_code} en API externa: {e.response.text}")
                    await error_notify(method_name, parent_id_notify_error, f"Error en API externa: {e.response.text}")
                    if 500 <= e.response.status_code < 600 and attempt < MAX_RETRIES:
                        # Reintentar en errores 5xx
                        await asyncio.sleep(RETRY_DELAY * attempt)
                        continue
                    raise

            # Si todos los intentos fallan
            msg = "Error persistente al consultar API externa"
            await error_notify(method_name, parent_id_notify_error, msg)
            raise HTTPException(status_code=502, detail="Error persistente al consultar API externa")

    except Exception as e:
        logger.error(f"Error general en webhook_product_lines: {e}")
        await error_notify(method_name, parent_id_notify_error, f"Error general: {e}")
        raise HTTPException(status_code=500, detail=f"Error general: {e}")


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
            
            logger.info(f"+++++ Parámetros recibidos: client_id={client_id} ++++++++,\n")
            
            logger.info(f"#####---Payload original ----#### \n {payload} \n")
            
            principal = payload.principal
            initial_fee = payload.initialFee
            
            token = await obtener_token(client)
            
            logger.info(f"Token de autorización extraído: {token} \n")
            
            new_payload = {
                "creditLineID": payload.creditLineID,
                "principal": principal,
                "time": payload.time,
                "disbursementMethod": payload.disbursementMethod,
                "initialFee": initial_fee,
                "paymentFrequency": payload.paymentFrequency
            }
            logger.info(f"Payload para el post a kuenta: {new_payload} \n")
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": client_id,
                "Authorization": token
            }
            logger.info(f"Iniciando petición POST a {PAYABLE_URL}")
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
                        logger.info("Petición completada exitosamente")
                        # Si hay contenido, retorna el JSON, si no retorna vacío
                        try:
                            return response.json()
                        
                        except httpx.HTTPStatusError as e:
                            await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {str(e)}")
                            logger.error(f"Intento {attempt+1}: Error en la respuesta de la API externa kuenta: {e.response.status_code}")
                    else:
                        logger.error(f"status error : {response.status_code}")
                        await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {response.status_code}")
                        
                except httpx.HTTPStatusError as e:
                        logger.error(f"Intento {attempt+1}: Error en API externa: {e.response.status_code}")
                        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # espera exponencial
            # Si no se logró en los reintentos
            await error_notify(method_name, client_id, f"Error de conexión tras: {max_retries} intentos o respuesta no válida ---- {str(e)}")
            raise HTTPException(status_code=502, detail=f"Error de conexión tras {max_retries} intentos o respuesta no válida")
        
    except ValueError as e:
        logger.error(f"Error de conversión de datos: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conversión de datos: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail="Error en la conversión de datos. Asegúrate que principal y initialFee sean números válidos"
        )
        
    except httpx.RequestError as e:
        logger.error(f"Error de conexión: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conexión: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Error de conexión: {str(e)}")
    
    except httpx.HTTPStatusError as e:
        logger.error(f"Error en API externa: {e.response}")
        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Error en API externa: {e.response.text}")
    except Exception as e:
        logger.error(f"Error interno: {str(e)}")
        await error_notify(method_name, client_id, f"Error interno: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

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
        logger.info(f"Payload recibido: {payload} \n")

        # --- VALIDACIONES DE ENTRADA ---
        linea_producto = payload.get("linea_producto")
        if not linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'linea_producto' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'linea_producto' en el payload")

        # Limpieza y conversión del valor principal
        raw_principal = str(payload.get("principal", "0")).replace(",", "").strip()
        if not raw_principal.isdigit():
            await error_notify(method_name, linea_producto_notify_error, f"El valor principal '{raw_principal}' no es numérico válido")
            raise ValueError(f"El valor principal '{raw_principal}' no es numérico válido")

        principal = float(raw_principal)

        # Porcentaje de cuota (sin símbolo %)
        porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        plazo_escogido = int(payload.get("plazo_escogido", 0))
        payment_frequency = int(payload.get("paymentFrequency", 30))

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

        aval_porcentaje = next(
            (float(str(c.get("percentage", 0))) for c in product_data.get("costs", []) if c.get("label") == "Aval"),
            None
        )
        if aval_porcentaje is None:
            await error_notify(method_name, linea_producto_notify_error, "No se encontró porcentaje de Aval en el producto")
            raise HTTPException(status_code=404, detail="No se encontró porcentaje de Aval en el producto")

        # --- CÁLCULOS FINALES ---
        valor_desembolsar = principal - valor_cuota_inicial
        if (1 - aval_porcentaje) == 0:
            await error_notify(method_name, linea_producto_notify_error, "El porcentaje de aval no puede ser 100%")
            raise ValueError("El porcentaje de aval no puede ser 100%.")

        valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        deducciones_anticipadas = valor_solicitar * aval_porcentaje

        logger.info("Cálculo completado correctamente.")

        return {
            "valor_producto": principal,
            "cuota_inicial": valor_cuota_inicial,
            "valor_desembolsar": valor_desembolsar,
            "deducciones_anticipadas": deducciones_anticipadas,
            "valor_solicitado": valor_solicitar,
            "aval_aplicado_porcentaje": aval_porcentaje,
            "plazo_dias": dias_totales,
            "porcentaje_escogido": porcentaje_str
        }

    except ValueError as e:
        logger.error(f"Error de datos: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error de datos: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except HTTPException as e:
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        raise

    except Exception as e:
        logger.error(f"Error interno inesperado: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error interno: {e}")
        raise HTTPException(status_code=500, detail="Error interno en el cálculo de financiamiento")



# # Endpoint para calcular financiamiento version 1
# @app.post("/calcular_financiamiento")
# async def calcular_financiamiento(payload: dict):
#     """
#     Calcula el financiamiento basado en:
#     1. Valor de cuota = principal * porcentaje_cuota
#     2. Días de plazo = plazo_escogido * paymentFrequency
#     3. Ajusta las cuotas al plazo en meses
#     """
    
#     method_name = "calcular_financiamiento"
#     linea_producto_notify_error = f"linea_producto para la busqueda del la linea =  {payload.get('linea_producto')}"
#     try:
#         logger.info(f"Payload recibido para cálculo: {payload} \n") 
#         # Procesar el valor principal con manejo de puntos y comas
#         valor_principal = str(payload.get("principal"))
#         # Manejar múltiples puntos, mantener solo el último como decimal
#         partes = valor_principal.split(".")
#         if len(partes) > 1:
#             entero = "".join(partes[:-1]).replace(",", "").strip()
#             decimal = partes[-1].replace(",", "").strip()
#             valor_principal = f"{entero}.{decimal}"
#         else:
#             valor_principal = valor_principal.replace(",", "").strip()
#         principal = float(valor_principal)
        
#         logger.info(f"Valor principal procesado: {principal} \n")
#         # Extraer y procesar el resto de valores de forma simple
#         porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
#         plazo_escogido = int(str(payload.get("plazo_escogido", "0")).strip())
#         payment_frequency = int(str(payload.get("paymentFrequency", "30")).strip())
        
#         linea_producto = str(payload.get("linea_producto"))
#         logger.info(f"linea_producto: {linea_producto} \n")
        
#         #porcentaje_cuota como decimal
#         porcentaje_cuota = float(porcentaje_str) / 100
#         logger.info(f"Porcentaje de cuota procesado: {porcentaje_cuota} \n")
#         # valor cuota inicial
#         valor_cuota_inicial = principal * porcentaje_cuota
#         logger.info(f"Valor cuota inicial calculado: {valor_cuota_inicial} \n")
#         # 2. Calcular días totales de plazo
#         dias_totales = plazo_escogido * payment_frequency
#         logger.info(f"Días totales de plazo calculados: {dias_totales} \n")
        
#         if not linea_producto:
#             raise HTTPException(status_code=400, detail="Falta la propiedad 'linea_producto' en el payload")
        
#         async with httpx.AsyncClient() as client:
#             # Extraer el token y crear nuevo payload
#             token = await obtener_token(client)
#             # Consultar API externa Kuenta
#             KUENTA_URL = f"https://api.kuenta.co/v1/products/{linea_producto}"
#             async with httpx.AsyncClient(timeout= 30.0) as client:
#                 # Obtener token una sola vez
#                 headers = {
#                 "Config-Organization-ID": ORG_ID,
#                 "Organization-ID": ORG_ID,
#                 "Authorization": token
#                 }
                
#                 response = await client.get(KUENTA_URL, headers=headers)
#                 response.raise_for_status()
#                 response = response.json()
                
#                 logger.info(f"Respuesta de la api Kuenta para línea de producto {linea_producto}: {response} \n")
            
#         # Validar el ID del producto
#         product = response.get("data").get("product")
#         logger.info(f"Producto obtenido de Kuenta: {product} \n")
#         if product.get("ID") != linea_producto:
#             raise HTTPException(status_code=404, detail="El ID de la línea de producto no coincide")
        
#         # Buscar el porcentaje de Aval
#         aval_porcentaje = None
#         for cost in product.get("costs", []):
#             if cost.get("label") == "Aval":
#                 aval_porcentaje = float(str(cost.get("percentage", "0")))
#                 break
            
#         if aval_porcentaje is None:
#             raise HTTPException(status_code=404, detail="No se encontró el porcentaje de Aval en la respuesta de Kuenta")
        
#         # --- 5. Aplicar fórmulas de la calculadora ---
        
#         # Valor a desembolsar = Valor del producto - Cuota inicial
#         valor_desembolsar = principal - valor_cuota_inicial
        
#         # Valor a solicitar = Valor a desembolsar / (1 - porcentaje de deducciones)
#         if (1 - aval_porcentaje) == 0:
#             raise ValueError("El porcentaje de aval no puede ser 100%.")
#         valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        
#         # Deducciones anticipadas = Valor a solicitar * porcentaje de deducciones
#         deducciones_anticipadas = valor_solicitar * aval_porcentaje
        
#         logger.info("------ CÁLCULO REALIZADO CON ÉXITO ------")
#         logger.info(f"Valor del Producto: {valor_principal}")
#         logger.info(f"Cuota Inicial: {valor_cuota_inicial}")
#         logger.info(f"Valor a Desembolsar (Neto): {valor_desembolsar}")
#         logger.info(f"Deducciones Anticipadas (Aval): {deducciones_anticipadas}")
#         logger.info(f"Valor a Solicitar (Monto del crédito): {valor_solicitar}")
        

#         return {
#             "valor_producto": principal,
#             "cuota_inicial": valor_cuota_inicial,
#             "valor_desembolsar": valor_desembolsar,
#             "deducciones_anticipadas": deducciones_anticipadas,
#             "valor_solicitado": valor_solicitar,
#             "aval_aplicado_porcentaje": aval_porcentaje,
#             "plazo_dias": dias_totales,
#             "porcentaje_escogido": porcentaje_str
#         }
        

#     except ValueError as e:
#         logger.error(f"Error en la conversión de datos: {str(e)}")
#         await error_notify(method_name, linea_producto_notify_error, f"Error en la respuesta de la API externa kuenta: {str(e)}")
#         raise HTTPException(
#             status_code=400,
#             detail="Error en los datos. Verifique que los valores numéricos sean válidos"
#         )
#     except Exception as e:
#         logger.error(f"Error en el cálculo: {str(e)}")
#         await error_notify(method_name, linea_producto_notify_error, f"Error en el cálculo: {str(e)}")
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error en el cálculo: {str(e)}"
#         )


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

        if not creditid or not installmentid or not orderid:
            raise HTTPException(status_code=400, detail="Faltan parámetros obligatorios: creditid, installmentid, orderid")

        url = f"https://api.kuenta.co/v1/payable/{creditid}/installment/0/order/list/{orderid}"
        intentos = 3
        intervalo_segundos = 15
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
                        logger.info(f"Estado final obtenido: {status} en el intento {intento}")
                        logger.info(f"Respuesta completa: {data}")
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
    

@app.get("/logs")
async def get_logs(limit: int = 20):
    """
    Devuelve los últimos logs enviados (correo + Telegram).
    Se puede consultar por Postman o navegador.
    """
    return {"count": len(get_cached_logs(limit)), "logs": get_cached_logs(limit)}

