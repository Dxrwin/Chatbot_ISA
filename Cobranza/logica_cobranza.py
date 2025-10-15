from fastapi import FastAPI, HTTPException, Body
import logging
import httpx
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import asyncio
from fastapi import Request
from datetime import datetime, timezone, timedelta
import time
from fastapi import Query

app = FastAPI()

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Endpoints
AUTH_URL = "https://demo-auth-api.kuenta.co/v1/oauth/token"


# Variables globales para el token y su expiración
TOKEN_DATA = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0
}

# --- Sistema de Caché en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

# Credenciales
AUTH_PAYLOAD = {
    "grant_type": "client_credentials",
    "client_id": "0468375b-0c02-4143-bf65-76744971046f",
    "client_secret": "06cc0c556b2116d4ce39616dcbeb65b941a717d3bd26288c8d01a62e846c88ca",
    "entity_id": "c269cfcc-0c9c-43e3-bef0-9e95d42ca309",
    "username": "RimsaGroup",
    "password": "2003",
    "mfa_token": "",
    "refresh_token": "",
    "totp": ""
}

ORG_ID = "c269cfcc-0c9c-43e3-bef0-9e95d42ca309"

class ClienteRequest(BaseModel):
    id_cliente: str
    
class DetalleCuotaRequest(BaseModel):
    id_cliente: str
    numero_cuota: int

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
            "client_id": AUTH_PAYLOAD["client_id"],
            "client_secret": AUTH_PAYLOAD["client_secret"]
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
    response = await client.post(AUTH_URL, json=AUTH_PAYLOAD)
    response.raise_for_status()
    auth_data = response.json()
    TOKEN_DATA["access_token"] = auth_data.get("access_token")
    TOKEN_DATA["refresh_token"] = auth_data.get("refresh_token")
    expires_in = auth_data.get("expires_in", 3600)
    TOKEN_DATA["expires_at"] = ahora + expires_in - 60  # margen de 1 min
    print("Nuevo token obtenido exitosamente.")
    return TOKEN_DATA["access_token"]



@app.post("/info_credito_mora")
async def obtener_info_credito(request: ClienteRequest = Body(...)):
    """
    Obtiene información del crédito y calcula días en mora por cuota.
    """
    async with httpx.AsyncClient() as client:
        id_credito = request.id_cliente
        token = await obtener_token(client)
        url = f"https://demo-api.kuenta.co/v1/receivable/{id_credito}"
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