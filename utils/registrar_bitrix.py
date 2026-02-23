import logging
from typing import Dict, Any
import time
import httpx
from models.models import WebhookPayload
from utils.notify_error import info_notify, error_notify
from utils.config import settings
import requests
import json

# Cache liviano en memoria para evitar reintentos inmediatos de envios webinar
WEBINAR_CACHE_TTL = 300  # segundos
webinar_request_cache: Dict[str, float] = {}



# Función auxiliar para integración con Bitrix24
async def registrar_en_bitrix(celular: str, tipo_proceso: str = "Campaña llamadas IA financiación posgrados IUB", timeout: int = 10) -> Dict[str, Any]:
    
    logging.info(f"Iniciando integración con Bitrix24 para teléfono: {celular} y proceso: {tipo_proceso}")
    """
    Realiza integración completa con Bitrix24 para buscar cliente por teléfono y crear deal.
    
    Args:
        celular: Número de teléfono (ej: "+573002613153" o "3002613153")
        tipo_proceso: "renovacion" o "refinanciacion" para determinar el STAGE_ID
        timeout: Tiempo máximo de espera en segundos
    
    Returns:
        Diccionario con:
        {
            "status": "success" | "error",
            "message": "descripción",
            "id_contacto": "id_del_contacto",
            "id_deal": "id_del_deal"
        }
    """
    try:
        # Validar entrada
        if not celular or not str(celular).strip():
            logging.warning("❌ Número de teléfono vacío para búsqueda en Bitrix24")
            return {
                "status": "error",
                "message": "Número de teléfono vacío"
            }
        
        # Normalizar teléfono
        telefono = str(celular).strip()
        if not telefono.startswith("+"):
            telefono = f"+57{telefono}"
        
        logging.info(f"Buscando cliente en Bitrix24 con teléfono: {telefono}")
        
        # ========== PETICIÓN 1: Buscar contacto por teléfono ==========
        url_contact = "https://horizontesas-fontumi.bitrix24.es/rest/6/untkqcnft2vadt5d/crm.contact.list"
        
        payload_contact = {
            "filter": {
                "PHONE": telefono
            },
            "select": ["ID"]
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Cookie': 'qmb=0.'
        }
        
        logging.info(f"Enviando petición de búsqueda con payload: {payload_contact}")
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            
            response = await client.post(url_contact, json=payload_contact, headers=headers)
            
            logging.info(f"Respuesta de búsqueda - Status: {response.status_code}")
            logging.debug(f"Respuesta completa de búsqueda: {response.text}")
            
            if response.status_code != 200:
                logging.error(f"❌ Error al obtener el contacto")
                logging.error(f"Status: {response.status_code}")
                logging.error(f"Response: {response.text}")
                await error_notify(
                    method_name="integracion_bitrix",
                    client_id=telefono,
                    error_message=f"Error al buscar contacto en Bitrix24: {response.status_code}"
                )
                return {
                    "status": "error",
                    "message": f"Error al obtener el contacto: {response.status_code}"
                }
            
            result = response.json().get("result", [])
            
            if not result:
                logging.warning(f"❌ No se encontró contacto con teléfono: {telefono}")
                await error_notify(
                    method_name="integracion_bitrix",
                    client_id=telefono,
                    error_message=f"No se encontró contacto en Bitrix24 con teléfono {telefono}"
                )
                return {
                    "status": "error",
                    "message": f"No se encontró contacto con teléfono {telefono}"
                }
            
            id_contacto = result[0]["ID"]
            logging.info(f"✅ Contacto encontrado - ID: {id_contacto}")
            
            # ========== PETICIÓN 2: Crear deal con el ID del contacto ==========
            
            # Determinar STAGE_ID según tipo_proceso
            stage_id_map = {
                "renovacion": "cargue de campañas masivas",  # primera etapa del pipeline de cobranzas
                "Campaña llamadas IA financiación posgrados IUB": "C28:UC_MZ7219"  # etapa específica para esta campaña de refinanciación, si no se encuentra se asigna a "C28:NEW"
            }
            
            stage_id = stage_id_map.get(tipo_proceso.lower(), "Campaña llamadas IA financiación posgrados IUB")
            logging.info(f"Tipo de proceso: {tipo_proceso} -> STAGE_ID: {stage_id}")
            
            url_deal = "https://horizontesas-fontumi.bitrix24.es/rest/6/untkqcnft2vadt5d/crm.deal.add"
            
            payload_deal = {
                "fields": {
                    "TITLE": f"IA CALL - {tipo_proceso.upper()}",
                    "CATEGORY_ID": 38,
                    "STAGE_ID": stage_id,
                    "OPPORTUNITY": 2500000,
                    "CURRENCY_ID": "COP",
                    "CONTACT_IDS": [id_contacto],
                    "ASSIGNED_BY_ID": 6,
                    "COMMENTS": f"Deal creado vía API del scrip de darwin para {tipo_proceso}",
                    "ADDITIONAL_INFO": f"Creado automáticamente por integracion IA y britrix para {tipo_proceso}"
                }
            }
            
            logging.info(f"Enviando petición de creación de deal con payload: {payload_deal}")
            
            response_deal = await client.post(url_deal, json=payload_deal, headers=headers)
            
            logging.info(f"Respuesta de creación de deal - Status: {response_deal.status_code}")
            
            if response_deal.status_code != 200:
                logging.error(f"❌ Error al crear el deal")
                logging.error(f"Status: {response_deal.status_code}")
                logging.error(f"Response: {response_deal.text}")
                await error_notify(
                    method_name="integracion_bitrix",
                    client_id=id_contacto,
                    error_message=f"Error al crear deal en Bitrix24: {response_deal.status_code}"
                )
                return {
                    "status": "error",
                    "message": f"Error al crear el deal: {response_deal.status_code}",
                    "id_contacto": id_contacto
                }
            
            respuesta_deal = response_deal.json()
            id_deal = respuesta_deal.get("result")
            
            logging.info(f"✅ Deal creado correctamente - ID: {id_deal}")
            await info_notify(
                method_name="integracion_bitrix",
                client_id=id_contacto,
                info_message=f"Deal creado exitosamente en Bitrix24 - ID Deal: {id_deal}, Tipo: {tipo_proceso}"
            )
            
            return {
                "status": "success",
                "message": "Deal creado correctamente",
                "id_contacto": id_contacto,
                "id_deal": id_deal
            }
            
    except httpx.ConnectError as e:
        logging.error(f"❌ Error de conexión a Bitrix24: {e}")
        await error_notify(
            method_name="integracion_bitrix",
            client_id=celular,
            error_message=f"Error de conexión a Bitrix24: {e}"
        )
        return {
            "status": "error",
            "message": f"Error de conexión a Bitrix24: {e}"
        }
    except httpx.TimeoutException as e:
        logging.error(f"❌ Timeout en conexión a Bitrix24: {e}")
        await error_notify(
            method_name="integracion_bitrix",
            client_id=celular,
            error_message=f"Timeout en conexión a Bitrix24: {e}"
        )
        return {
            "status": "error",
            "message": f"Timeout en conexión a Bitrix24: {e}"
        }
    except Exception as e:
        logging.error(f"❌ Error inesperado en integracion_bitrix: {e}", exc_info=True)
        await error_notify(
            method_name="integracion_bitrix",
            client_id=celular,
            error_message=f"Error inesperado en integracion_bitrix: {e}"
        )
        return {
            "status": "error",
            "message": f"Error inesperado: {e}"
        }