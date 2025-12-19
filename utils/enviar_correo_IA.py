import logging
from typing import Dict, Any
import time
import httpx
from models.models import WebhookPayload
from utils.database import insertar_flujo_correo_post_agente
from utils.email_service import enviar_correo_renovacion, enviar_correo_webinar
from utils.notify_error import info_notify, error_notify
from utils.config import settings

# Cache liviano en memoria para evitar reintentos inmediatos de envios webinar
WEBINAR_CACHE_TTL = 300  # segundos
webinar_request_cache: Dict[str, float] = {}

# Esta funcion marca y detecta reenvios recientes para no duplicar correos en el mismo proceso
def _marcar_y_verificar_reenvio_webinar(llave: str) -> bool:
    ahora = time.time()
    ultimo = webinar_request_cache.get(llave)
    if ultimo and (ahora - ultimo) < WEBINAR_CACHE_TTL:
        return True
    webinar_request_cache[llave] = ahora
    return False


# Función auxiliar para integración con Bitrix24
async def integracion_bitrix(celular: str, tipo_proceso: str = "renovacion", timeout: int = 10) -> Dict[str, Any]:
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
                "renovacion": "C24:NEW",  # IA CALL - RENOVACIÓN
                "refinanciacion": "C24:UC_OU9VHP"  # IA CALL - REFINANCIACIÓN
            }
            
            stage_id = stage_id_map.get(tipo_proceso.lower(), "C24:NEW")
            logging.info(f"Tipo de proceso: {tipo_proceso} -> STAGE_ID: {stage_id}")
            
            url_deal = "https://horizontesas-fontumi.bitrix24.es/rest/6/untkqcnft2vadt5d/crm.deal.add"
            
            payload_deal = {
                "fields": {
                    "TITLE": f"IA CALL - {tipo_proceso.upper()}",
                    "CATEGORY_ID": 24,
                    "STAGE_ID": stage_id,
                    "OPPORTUNITY": 2500000,
                    "CURRENCY_ID": "COP",
                    "CONTACT_IDS": [id_contacto],
                    "ASSIGNED_BY_ID": 6,
                    "COMMENTS": f"Deal creado vía API para {tipo_proceso}",
                    "ADDITIONAL_INFO": f"Creado automáticamente por sistema IA para {tipo_proceso}"
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
        

#validaciones para el envio de correo para la rennovacion
async def procesar_webhook_renovacion(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la logica de negocio del webhook.
    """
    input_vars = payload.input_variables
    extracted_vars = payload.extracted_variables

    logging.info(f"payloads recibidos: input_vars={input_vars},\n extracted_vars={extracted_vars}")

    # 1. DECIDIR DESTINATARIO PRIMERO (antes de validaciones)
    destinatario = None
    
    if extracted_vars.desicion_correo is True:
        destinatario = input_vars.CORREO
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif extracted_vars.desicion_correo is False:
        # Si el cliente proporciono correo, usarlo; si no, usar el guardado
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"Usando correo proporcionado por cliente (desicion_correo=False): {destinatario}")
        else:
            destinatario = input_vars.CORREO
            logging.info(f"Correo cliente vacio, usando correo guardado por defecto: {destinatario}")
    else:
        # Si desicion_correo no se proporciona, intentar correo_cliente; si no, usar guardado
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"desicion_correo no definido, usando correo_cliente: {destinatario}")
        else:
            destinatario = input_vars.CORREO
            logging.info(f"desicion_correo no definido y correo_cliente vacio, usando correo guardado: {destinatario}")

    if not destinatario:
        logging.warning("No hay destinatario disponible.")
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=f"No se pudo enviar correo por falta de destinatario.",
        )
        return {
            "status": "error",
            "message": "No se pudo enviar correo por falta de destinatario.",
        }

    # 2. VALIDACIONES (lógica simplificada para envío de correo)
    enviar_correo = False
    mensaje_error = None
    
    # Obtener valores booleanos
    envio_correo_value = extracted_vars.envio_correo
    intrsrenovarbool_value = extracted_vars.intrsrenovarbool
    ambiguedad_value = extracted_vars.ambiguedad
    interes_renovar_value = extracted_vars.interes_renovar
    
    # Convertir interes_renovar a string para comparación
    if interes_renovar_value is not None:
        interes_renovar_str = str(interes_renovar_value).strip().lower()
    else:
        interes_renovar_str = None
    
    # Lógica de envío: 
    # Si ambiguedad es True Y (envio_correo es True O intrsrenovarbool es True) → enviar
    # O si envio_correo es True → enviar
    # O si intrsrenovarbool es True → enviar
    
    if ambiguedad_value is True and (envio_correo_value is True or intrsrenovarbool_value is True):
        enviar_correo = True
        logging.info(f"✅ Enviando correo: ambiguedad=True y (envio_correo=True O intrsrenovarbool=True)")
    
    elif envio_correo_value is True:
        enviar_correo = True
        logging.info(f"✅ Enviando correo: envio_correo=True")
    
    elif intrsrenovarbool_value is True:
        enviar_correo = True
        logging.info(f"✅ Enviando correo: intrsrenovarbool=True")
    
    else:
        # No se cumplen las condiciones para enviar correo
        if not destinatario:
            mensaje_error = "No se pudo enviar correo por falta de destinatario."
            logging.warning(f"❌ {mensaje_error}")
        elif ambiguedad_value is False and envio_correo_value is False and intrsrenovarbool_value is False:
            mensaje_error = "El cliente no tiene interés en renovar (ambiguedad=False, envio_correo=False, intrsrenovarbool=False)."
            logging.info(f"❌ {mensaje_error}")
        elif ambiguedad_value is True and envio_correo_value is False and intrsrenovarbool_value is False:
            mensaje_error = "Existe ambigüedad pero el cliente no autorizó el envío (envio_correo=False, intrsrenovarbool=False)."
            logging.info(f"❌ {mensaje_error}")
        else:
            mensaje_error = "No se cumplen las condiciones para enviar correo."
            logging.info(f"❌ {mensaje_error}")
        
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=f"{mensaje_error} Cliente: {input_vars.NOMBRE_TITULAR}",
        )
        return {
            "status": "error",
            "message": mensaje_error,
        }

    # Verificar que enviar_correo sea True antes de continuar
    if not enviar_correo:
        logging.warning("⚠️ Variable enviar_correo es False, no se procede con el envío.")
        return {
            "status": "error",
            "message": "No se cumplen las condiciones para enviar correo.",
        }

    # Si llega aquí, enviar_correo es True → proceder con el envío

    # 3. ENVIAR CORREO (si pasó todas las validaciones)
    logging.info(f"Validacion superada. Intentando enviar correo a: {input_vars.NOMBRE_TITULAR} ({destinatario})")

    numero_telefono_input = getattr(input_vars, "Celular", None)
    linea_universitaria = getattr(input_vars, "LINEA_CREDITO", None)
    primer_name = extracted_vars.primer_name

    if not numero_telefono_input:
        logging.warning("No se recibio numero_telefono en el payload.")
        return {
            "status": "error",
            "message": "No se pudo enviar correo por falta de numero_telefono."
        }
    if not linea_universitaria:
        logging.warning("No se recibio linea_universitaria en el payload.")
        return {
            "status": "error",
            "message": "No se pudo enviar correo por falta de linea_universitaria."
        }

    try:
        link_whatsapp_asesor = "https://wa.me/573182856386"

        confirmacion_response = await enviar_correo_renovacion(
            destinatario=destinatario,
            nombre=primer_name or "Cliente One2credit",
            semestre=str(input_vars.SEMESTRE),
            link_asesor=link_whatsapp_asesor,
        )
        
        #Validar explícitamente la respuesta del envío de correo
        logging.info(f"Respuesta del envío de correo: {confirmacion_response}")
        
        # Si el correo NO se envió exitosamente
        if confirmacion_response.get("status") != "success":
            logging.error(f"❌ Error en envio de correo de renovacion a {destinatario}: {confirmacion_response.get('message')}")
            logging.warning(f"El correo no fue enviado: {confirmacion_response.get('message')} Detalles: {confirmacion_response}")
            
            # Notificar el error pero no reintentemos
            await error_notify(
                method_name="procesar_webhook_renovacion",
                client_id=input_vars.NOMBRE_TITULAR,
                error_message=f"Correo no enviado para renovación. Destinatario: {destinatario}. Error: {confirmacion_response.get('message')}. Detalles: {confirmacion_response}",
            )
            
            return {
                "status": "error",
                "message": confirmacion_response.get("message", "Error desconocido al enviar correo"),
                "correo_enviado": False,
                "destinatario": destinatario,
                "intentos_correo": confirmacion_response.get("intentos", 0),
            }
        
        #Si el correo se envió exitosamente, proceder con BD
        logging.info(f"✅ Correo enviado exitosamente a: {destinatario} en intento {confirmacion_response.get('intentos')}")
        
        await info_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            info_message=f"Correo de renovación enviado exitosamente a {destinatario} en intento {confirmacion_response.get('intentos')} para {input_vars.NOMBRE_TITULAR}",
        )

        flujo_id = None
        #Mejorado manejo de registro en BD
        if numero_telefono_input and linea_universitaria:
            try:
                flujo_id = await insertar_flujo_correo_post_agente(
                    nombre_cliente=input_vars.NOMBRE_TITULAR,
                    correo_enviado=destinatario,
                    numero_telefono=numero_telefono_input,
                    linea_universitaria=linea_universitaria,
                )
                logging.info(f"Flujo registrado en BD con ID: {flujo_id}")
                
                await info_notify(
                    method_name="procesar_webhook_renovacion",
                    client_id=input_vars.NOMBRE_TITULAR,
                    info_message=f"Flujo de renovación registrado en BD. ID: {flujo_id}",
                )
            except Exception as e:
                logging.error(f"Error al registrar flujo en BD: {e}", exc_info=True)
                await error_notify(
                    method_name="procesar_webhook_renovacion",
                    client_id=input_vars.NOMBRE_TITULAR,
                    error_message=f"Correo enviado pero error al registrar flujo en BD: {e}",
                )
        else:
            if not numero_telefono_input:
                logging.warning(f"No se registró flujo: número telefónico faltante para {input_vars.NOMBRE_TITULAR}")
            if not linea_universitaria:
                logging.warning(f"No se registró flujo: línea universitaria faltante para {input_vars.NOMBRE_TITULAR}")

        #Respuesta mejorada con flags explícitos
        return {
            "status": "success",
            "correo_enviado": True,
            "enviado_a": input_vars.NOMBRE_TITULAR,
            "correo_destinatario": destinatario,
            "intentos_correo": confirmacion_response.get("intentos"),
            "numero_telefono": numero_telefono_input,
            "linea_universitaria": linea_universitaria,
            "id_flujo_bd": flujo_id,
        }

    except Exception as e:
        logging.error(f"Excepción en procesar_webhook_renovacion: {e}", exc_info=True)
        # Notificar el error pero no lanzar excepción para evitar que el caller
        # (webhook sender) reintente la petición y provoque envíos duplicados.
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=f"Excepción en servicio de correo de renovación: {e}",
        )
        return {
            "status": "error",
            "message": f"Error en el servicio de envio de correo: {e}",
            "correo_enviado": False,
        }

#validaciones par
async def procesar_webhook_webinar(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la logica de negocio del webhook.
    """
    #logging.info(f"payloads recibidos: {payload}")
    
    variables_entrada = payload.input_variables
    variables_extraidas = payload.extracted_variables
    
    # todas las variables extraidas:
    
    logging.info(f"variables extraidas : resumen{variables_extraidas.resumen} \n comentario_libre: {variables_extraidas.comentario_libre} \n contesto_llamada: {variables_extraidas.contesto_llamada} \n estado: {variables_extraidas.estado} \n desicion_correo: {variables_extraidas.desicion_correo} \n correo_cliente: {getattr(variables_extraidas, 'correo_cliente', None)} \n interes_corre {variables_extraidas.interes_correo} \n objetivo: {variables_extraidas.objetivo} \n primer_name: {variables_extraidas.primer_name} ")

    # 1. DECIDIR DESTINATARIO PRIMERO
    destinatario = None
    
    if variables_extraidas.desicion_correo is True:
        destinatario = variables_entrada.EMAIL
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif variables_extraidas.desicion_correo is False:
        # Si el cliente proporciono correo, usarlo; si no, usar el guardado
        correo_cliente = getattr(variables_extraidas, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"Usando correo proporcionado por cliente (desicion_correo=False): {destinatario}")
        else:
            destinatario = variables_entrada.EMAIL
            logging.info(f"Correo cliente vacio, usando correo guardado por defecto: {destinatario}")
    else:
        # Si desicion_correo no se proporciona, intentar correo_cliente; si no, usar guardado
        correo_cliente = getattr(variables_extraidas, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"desicion_correo no definido, usando correo_cliente: {destinatario}")
        else:
            destinatario = variables_entrada.EMAIL
            logging.info(f"desicion_correo no definido y correo_cliente vacio, usando correo guardado: {destinatario}")

    if not destinatario:
        logging.warning("No hay destinatario disponible.")
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"No se pudo enviar correo por falta de destinatario.",
        )
        return {
            "status": "warning",
            "message": "No se pudo enviar correo por falta de destinatario.",
        }

    # 1.1 Idempotencia: evita reintentos inmediatos del mismo correo en el mismo proceso
    dedup_key = f"{(destinatario or '').lower()}|{(variables_entrada.Nombre or '').lower()}"
    if _marcar_y_verificar_reenvio_webinar(dedup_key):
        logging.info("Solicitud de webinar ya procesada recientemente; se omite reenvio para evitar duplicados.")
        return {
            "status": "success",
            "message": "Solicitud ya procesada recientemente; no se reintenta el envio.",
        }

    # 2. VALIDACIONES
    enviar_correo = True
    razon_rechazo = None

    if variables_extraidas.interessolicitud is not None:
        interes_str = str(variables_extraidas.interessolicitud).strip()
        if interes_str.lower() == "no" or interes_str == "":
            enviar_correo = False
            razon_rechazo = "Cliente no interesado en el webinar"
            logging.info("Validacion: 'interessolicitud' es 'No' o vacio.")
    
    if variables_extraidas.contesto_llamada is False and variables_extraidas.estado is False:
        enviar_correo = False
        razon_rechazo = "Cliente no contesto llamada y estado es False"
        logging.info("Validacion: 'contesto_llamada' y 'estado' son False.")

    if not enviar_correo:
        logging.info(f"No se cumplieron las validaciones: {razon_rechazo}")
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"{razon_rechazo}. Cliente: {variables_entrada.Nombre}, Correo: {destinatario}",
        )
        return {
            "status": "success",
            "message": "Validaciones no cumplidas, no se envio correo.",
        }

    # 3. ENVIAR CORREO
    logging.info(f"Validacion superada. Intentando enviar correo a: {variables_entrada.Nombre} ({destinatario})")

    numero_telefono_input = getattr(variables_entrada, "Contacto", None)
    if not numero_telefono_input:
        logging.warning("No se recibio numero_telefono en el payload.")

    await info_notify(
        method_name="procesar_webhook_webinar",
        client_id=variables_entrada.Nombre,
        info_message=f"Intentando enviar correo a: {variables_entrada.Nombre} ({destinatario})",
    )
    
    primer_name = variables_extraidas.primer_name
    
    
    try:
        #luego de las validaciones envia el correo
        confirmacion_response = await enviar_correo_webinar(
            destinatario=destinatario,
            nombre=primer_name or "Cliente Onetwocredit",
        )
        
        #Validar explícitamente la respuesta del envío de correo
        logging.info(f"Respuesta del envío de correo (webinar): {confirmacion_response}")
        
        # Si el correo NO se envió exitosamente
        if confirmacion_response.get("status") != "success":
            logging.warning(f"El correo webinar no fue enviado: {confirmacion_response.get('message')}")
            logging.warning(f"Detalles: {confirmacion_response}")
            
            # Notificar el error
            await error_notify(
                method_name="procesar_webhook_webinar",
                client_id=variables_entrada.Nombre,
                error_message=f"Correo webinar no enviado. Destinatario: {destinatario}. Error: {confirmacion_response.get('message')}. Detalles: {confirmacion_response}",
            )
            
            return {
                "status": "error",
                "message": confirmacion_response.get("message", "Error desconocido al enviar correo"),
                "correo_enviado": False,
                "destinatario": destinatario,
                "intentos_correo": confirmacion_response.get("intentos", 0),
            }
        
        #Si el correo se envió exitosamente, proceder con BD
        logging.info(f"Correo webinar enviado exitosamente a: {destinatario} en intento {confirmacion_response.get('intentos')}")
        
        await info_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            info_message=f"Correo de invitación a webinar enviado exitosamente a {destinatario} en intento {confirmacion_response.get('intentos')} para {variables_entrada.Nombre}",
        )
        
        flujo_id = None
        #Mejorado manejo de registro en BD
        if numero_telefono_input:
            try:
                flujo_id = await insertar_flujo_correo_post_agente(
                    nombre_cliente=variables_entrada.Nombre,
                    correo_enviado=destinatario,
                    numero_telefono=numero_telefono_input,
                    linea_universitaria=variables_extraidas.objetivo,
                )
                logging.info(f"Flujo de webinar registrado en BD con ID: {flujo_id}")
                
                await info_notify(
                    method_name="procesar_webhook_webinar",
                    client_id=variables_entrada.Nombre,
                    info_message=f"Flujo de webinar registrado en BD. ID: {flujo_id}",
                )
            except Exception as e:
                logging.error(f"Error al registrar flujo de webinar en BD: {e}", exc_info=True)
                await error_notify(
                    method_name="procesar_webhook_webinar",
                    client_id=variables_entrada.Nombre,
                    error_message=f"Correo webinar enviado pero error al registrar flujo en BD: {e}",
                )
        else:
            logging.warning(f"No se registró flujo de webinar: número telefónico faltante para {variables_entrada.Nombre}")
        
        
        return {
            "status": "success",
            "enviado_a": variables_entrada.Nombre,
            "correo_destinatario": destinatario,
            "numero_telefono": numero_telefono_input
        }
    
    except Exception as e:
        logging.error(f"Excepción en procesar_webhook_webinar: {e}", exc_info=True)
        # Notificar el error y devolver un resultado de error en lugar de lanzar.
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"Excepción en servicio de correo de webinar: {e}",
        )
        return {
            "status": "error",
            "message": f"Error en el servicio de envio de correo: {e}",
            "correo_enviado": False,
        }
        
async def procesar_llamada_renovacion_Y_refinanciamiento(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio que orquesta la lógica de negocio para renovación y refinanciamiento.
    
    Valida variables extraídas y ejecuta:
    1. Envío de correos de renovación
    2. Peticiones POST a webhooks externos para refinanciamiento
    
    Casos de validación:
    1. renovacion=Si + (acpt_info_email=True OR aceptoinfocorreo=Si) → correo + webhook
    2. renovacion=No + (acpt_info_email=True OR aceptoinfocorreo=Si) → correo
    3. refinanciar_bool=True + refinanciar=Si → webhook
    4. refinanciar_bool=True + refinanciar=Si + agendo_asst_assr=Si → webhook
    5. refinanciar_bool=True + refinanciar=Si + agendo_asst_assr=Si + fecha_asst_assor → webhook
    6. refinanciar=Si + refinanciar_bool=True + asst_assr_bool=True → webhook
    7. aceptoinfocorreo=Si + refinanciar=No + refinanciar_bool=False + renovacion=No → correo
    """
    
    input_vars = payload.input_variables
    extracted_vars = payload.extracted_variables
    
    logging.info(f"Procesando webhooks de renovación y refinanciamiento")
    logging.info(f"Input variables: {input_vars}")
    logging.info(f"Extracted variables: {extracted_vars}")
    
    # Preparar variables necesarias
    nombre_cliente = getattr(input_vars, "NOMBRE_TITULAR", None) or getattr(input_vars, "Nombre", None) or "Cliente"
    destinatario = getattr(input_vars, "CORREO", None) or getattr(input_vars, "EMAIL", None)
    numero_telefono = getattr(input_vars, "Celular", None) or getattr(input_vars, "Contacto", None)
    #linea_universitaria = getattr(input_vars, "LINEA_CREDITO", None) or getattr(extracted_vars, "objetivo", None)
    
    # Decidir destinatario
    if extracted_vars.desicion_correo is True:
        destinatario = destinatario
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif extracted_vars.desicion_correo is False:
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"Usando correo cliente (desicion_correo=False): {destinatario}")
        else:
            logging.info(f"Correo cliente vacío, usando guardado por defecto")
    else:
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_cliente and str(correo_cliente).strip():
            destinatario = correo_cliente
            logging.info(f"Correo cliente: {destinatario}")
    
    # Variables de control
    acciones_ejecutadas = []
    errores = []
    
    try:
        # CASO 1: renovacion=Si + (acpt_info_email=True OR aceptoinfocorreo=Si)
        # → Enviar correo + Llamar webhook
        if (extracted_vars.renovacion == "Si" and 
            (extracted_vars.acpt_info_email is True or extracted_vars.aceptoinfocorreo == "Si")):
            
            logging.info("CASO 1: renovacion=Si + aceptación de correo → Envío de correo")
            
            # Enviar correo
            if destinatario:
                primer_name = extracted_vars.primer_name or ""
                link_whatsapp = "https://wa.me/573182856386"
                
                try:
                    respuesta_correo = await enviar_correo_renovacion(
                        destinatario=destinatario,
                        nombre=primer_name,
                        semestre=getattr(input_vars, "SEMESTRE", ""),
                        link_asesor=link_whatsapp,
                    )
                    
                    if respuesta_correo.get("status") == "success":
                        logging.info(f"✅ Correo de renovación enviado a {destinatario}")
                        acciones_ejecutadas.append("correo_renovacion")
                        
                        await info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Correo de renovación enviado a {destinatario}"
                            
                        )
                        
                        return {
                            "status": "success",
                            "message": "Correo de renovación enviado exitosamente.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                            
                    else:
                        error_msg = f"No se envió correo: {respuesta_correo.get('message')}"
                        logging.error(f"❌ {error_msg}")
                        errores.append(error_msg)
                        await error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=error_msg
                        )
                        return {
                            "status": "error",
                            "message": error_msg,
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                except Exception as e:
                    error_msg = f"Error al enviar correo de renovación: {e}"
                    logging.error(f"❌ {error_msg}", exc_info=True)
                    errores.append(error_msg)
                    await error_notify(
                        method_name="procesar_llamada_renovacionYrefinanciamiento",
                        client_id=nombre_cliente,
                        error_message=error_msg
                    )
            
            # Llamar Bitrix24 para buscar cliente
            id_bitrix = None
            if numero_telefono:
                try:
                    respuesta_bitrix = await integracion_bitrix(numero_telefono,"renovacion")
                    if respuesta_bitrix.get("status") == "success":
                        id_bitrix = respuesta_bitrix.get("id")
                        logging.info(f"✅ Cliente encontrado en Bitrix24 - ID: {id_bitrix}")
                        acciones_ejecutadas.append("busqueda_bitrix_renovacion")
                    else:
                        logging.warning(f"⚠️ No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}")
                        errores.append(f"Bitrix24: {respuesta_bitrix.get('message')}")
                except Exception as e:
                    logging.error(f"❌ Error en búsqueda Bitrix24: {e}")
                    errores.append(f"Error Bitrix24: {e}")
        
        # CASO 2: renovacion=No + (acpt_info_email=True OR aceptoinfocorreo=Si)
        # → Solo enviar correo
        elif (extracted_vars.renovacion == "No" and 
                (extracted_vars.acpt_info_email is True or extracted_vars.aceptoinfocorreo == "Si")):
            
            logging.info("CASO 2: renovacion=No + aceptación de correo → Solo envío de correo")
            
            if destinatario:
                primer_name = extracted_vars.primer_name or "Cliente One2credit"
                link_whatsapp = "https://wa.me/573182856386"
                
                try:
                    respuesta_correo = await enviar_correo_renovacion(
                        destinatario=destinatario,
                        nombre=primer_name,
                        semestre=getattr(input_vars, "SEMESTRE", ""),
                        link_asesor=link_whatsapp
                    )
                    
                    if respuesta_correo.get("status") == "success":
                        logging.info(f"✅ Correo enviado a {destinatario}")
                        acciones_ejecutadas.append("correo_informativo")
                        
                        await info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Correo informativo enviado a {destinatario}"
                        )
                        
                        return {
                            "status": "success",
                            "message": "Correo informativo enviado exitosamente.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                    else:
                        error_msg = f"No se envió correo: {respuesta_correo.get('message')}"
                        logging.error(f"❌ {error_msg}")
                        errores.append(error_msg)
                        await error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=error_msg
                        )
                        
                        return {
                            "status": "error",
                            "message": error_msg,
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                except Exception as e:
                    error_msg = f"Error al enviar correo: {e}"
                    logging.error(f"❌ {error_msg}", exc_info=True)
                    errores.append(error_msg)
                    await error_notify(
                        method_name="procesar_llamada_renovacionYrefinanciamiento",
                        client_id=nombre_cliente,
                        error_message=error_msg
                    )
        
        # CASOS 3-6: Validaciones de refinanciamiento
        # CASO 3: refinanciar_bool=True + refinanciar=Si
        if (extracted_vars.refinanciar_bool is True and extracted_vars.refinanciar == "Si"):
            logging.info("CASO 3: refinanciar_bool=True + refinanciar=Si → Webhook")
            
            # Buscar cliente en Bitrix24
            id_bitrix = None
            if numero_telefono:
                try:
                    respuesta_bitrix = await integracion_bitrix(numero_telefono,"refinanciacion")
                    
                    if respuesta_bitrix.get("status") == "success":
                        id_bitrix = respuesta_bitrix.get("id")
                        logging.info(f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}")
                        acciones_ejecutadas.append("busqueda_bitrix_caso3")
                        info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}"
                        )
                        return {
                            "status": "success",
                            "message": "Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                    else:
                        logging.warning(f"No se encontró cliente en Bitrix24 y no se registro en el embudo: {respuesta_bitrix.get('message')}")
                        errores.append(f"Bitrix24 CASO 3: {respuesta_bitrix.get('message')}")
                        error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=f"No se encontró cliente en Bitrix24 y no se registro en el embudo: {respuesta_bitrix.get('message')}"
                        )
                        
                except Exception as e:
                    logging.error(f"❌ Error en búsqueda Bitrix24: {e}")
                    errores.append(f"Error Bitrix24 CASO 3: {e}")
                    
            
        # CASO 4: refinanciar_bool=True + refinanciar=Si + agendo_asst_assr=Si
        if (extracted_vars.refinanciar_bool is True or 
            extracted_vars.refinanciar == "Si" and 
            extracted_vars.agendo_asst_assr == "Si"):
            
            logging.info("CASO 4: refinanciar_bool=True + refinanciar=Si + agendo_asst_assr=Si → Webhook")
            
            # Buscar cliente en Bitrix24
            id_bitrix = None
            if numero_telefono:
                try:
                    respuesta_bitrix = await integracion_bitrix(numero_telefono,"refinanciacion")
                    if respuesta_bitrix.get("status") == "success":
                        id_bitrix = respuesta_bitrix.get("id")
                        logging.info(f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}")
                        acciones_ejecutadas.append("busqueda_bitrix_caso4")
                        info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}"
                        )
                        return {
                            "status": "success",
                            "message": "Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                    else:
                        logging.warning(f"⚠️ No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}")
                        errores.append(f"Bitrix24 CASO 4: {respuesta_bitrix.get('message')}")
                        
                        error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=f"No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}"
                        )
                        
                except Exception as e:
                    logging.error(f"❌ Error en búsqueda Bitrix24: {e}")
                    errores.append(f"Error Bitrix24 CASO 4: {e}")
        
        # CASO 5: refinanciar_bool=True + refinanciar=Si + agendo_asst_assr=Si + fecha_asst_assor
        fecha_asesor = getattr(extracted_vars, "fecha_asst_assor", None)
        if (extracted_vars.refinanciar_bool is True or 
            extracted_vars.refinanciar == "Si" and 
            extracted_vars.agendo_asst_assr == "Si" or 
            fecha_asesor and str(fecha_asesor).strip()):
            
            logging.info(f"CASO 5: Con fecha de asesoría ({fecha_asesor}) → Webhook")
            
            # Buscar cliente en Bitrix24
            id_bitrix = None
            if numero_telefono:
                try:
                    respuesta_bitrix = await integracion_bitrix(numero_telefono,"refinanciacion")
                    if respuesta_bitrix.get("status") == "success":
                        id_bitrix = respuesta_bitrix.get("id")
                        logging.info(f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}")
                        acciones_ejecutadas.append("busqueda_bitrix_caso5")
                        
                        info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}"
                        )
                        return {
                            "status": "success",
                            "message": "Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                    else:
                        logging.warning(f"⚠️ No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}")
                        errores.append(f"Bitrix24 CASO 5: {respuesta_bitrix.get('message')}")
                        error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=f"No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}"
                        )
                        
                except Exception as e:
                    logging.error(f"❌ Error en búsqueda Bitrix24: {e}")
                    errores.append(f"Error Bitrix24 CASO 5: {e}")
            
        # CASO 6: refinanciar=Si + refinanciar_bool=True + asst_assr_bool=True
        if (extracted_vars.refinanciar == "Si" or 
            extracted_vars.refinanciar_bool is True or 
            extracted_vars.asst_assr_bool is True):
            
            logging.info("CASO 6: refinanciar=Si + refinanciar_bool=True + asst_assr_bool=True → Webhook")
            
            # Buscar cliente en Bitrix24
            id_bitrix = None
            if numero_telefono:
                try:
                    respuesta_bitrix = await integracion_bitrix(numero_telefono,"refinanciacion")
                    if respuesta_bitrix.get("status") == "success":
                        id_bitrix = respuesta_bitrix.get("id")
                        logging.info(f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}")
                        acciones_ejecutadas.append("busqueda_bitrix_caso6")
                        info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding: {id_bitrix}"
                        )
                        return {
                            "status": "success",
                            "message": "Cliente registrado en bitrix para refinanciar y se envio data al embudo Onboarding.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }

                    else:
                        logging.warning(f"⚠️ No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}")
                        errores.append(f"Bitrix24 CASO 6: {respuesta_bitrix.get('message')}")
                        error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=f"No se encontró cliente en Bitrix24: {respuesta_bitrix.get('message')}"
                        )
                        
                except Exception as e:
                    logging.error(f"❌ Error en búsqueda Bitrix24: {e}")
                    errores.append(f"Error Bitrix24 CASO 6: {e}")
            
            
        # CASO 7: aceptoinfocorreo=Si + refinanciar=No + refinanciar_bool=False + renovacion=No
        # → Solo enviar correo
        if (extracted_vars.aceptoinfocorreo == "Si" and 
            extracted_vars.refinanciar == "No" or 
            extracted_vars.refinanciar_bool is False and 
            extracted_vars.renovacion == "No"):
            
            logging.info("CASO 7: Sin renovación ni refinanciamiento, pero con aceptación → Correo")
            
            if destinatario:
                primer_name = extracted_vars.primer_name or "Cliente One2credit"
                link_whatsapp = "https://wa.me/573182856386"
                
                try:
                    respuesta_correo = await enviar_correo_renovacion(
                        destinatario=destinatario,
                        nombre=primer_name,
                        semestre=getattr(input_vars, "SEMESTRE", ""),
                        link_asesor=link_whatsapp
                    )
                    
                    if respuesta_correo.get("status") == "success":
                        logging.info(f"✅ Correo informativo enviado a {destinatario}")
                        acciones_ejecutadas.append("correo_caso7")
                        
                        await info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Correo informativo CASO 7 enviado a {destinatario}"
                        )
                        
                        return {
                            "status": "success",
                            "message": "Correo informativo CASO 7 enviado exitosamente.",
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                    else:
                        error_msg = f"No se envió correo CASO 7: {respuesta_correo.get('message')}"
                        logging.error(f"❌ {error_msg}")
                        errores.append(error_msg)
                        await error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=error_msg
                        )
                        
                        return {
                            "status": "error",
                            "message": error_msg,
                            "acciones_ejecutadas": acciones_ejecutadas,
                            "errores": errores
                        }
                        
                except Exception as e:
                    error_msg = f"Error al enviar correo CASO 7: {e}"
                    logging.error(f"❌ {error_msg}", exc_info=True)
                    errores.append(error_msg)
                    await error_notify(
                        method_name="procesar_llamada_renovacionYrefinanciamiento",
                        client_id=nombre_cliente,
                        error_message=error_msg
                    )
        
        # Validar que al menos una acción se haya ejecutado
        if not acciones_ejecutadas and not errores:
            logging.warning(f"⚠️ No se ejecutaron validaciones para el cliente {nombre_cliente}")
            await error_notify(
                method_name="procesar_llamada_renovacionYrefinanciamiento",
                client_id=nombre_cliente,
                error_message="No se cumplieron las validaciones especificadas para ejecutar acciones"
            )
            return {
                "status": "warning",
                "message": "No se ejecutó ninguna acción",
                "acciones": acciones_ejecutadas,
                "errores": errores
            }
        
        # Preparar respuesta final
        respuesta_final = {
            "status": "success" if not errores else "partial",
            "cliente": nombre_cliente,
            "correo": destinatario,
            "acciones_ejecutadas": acciones_ejecutadas,
            "errores": errores if errores else None
        }
        
        if acciones_ejecutadas:
            await info_notify(
                method_name="procesar_llamada_renovacionYrefinanciamiento",
                client_id=nombre_cliente,
                info_message=f"Procesamiento completado. Acciones: {', '.join(acciones_ejecutadas)}"
            )
        
        logging.info(f"✅ Procesamiento completado: {respuesta_final}")
        #return respuesta_final
        
    except Exception as e:
        logging.error(f"❌ Excepción en procesar_llamada_renovacionYrefinanciamiento: {e}", exc_info=True)
        await error_notify(
            method_name="procesar_llamada_renovacionYrefinanciamiento",
            client_id=nombre_cliente,
            error_message=f"Excepción crítica: {e}"
        )
        return {
            "status": "error",
            "message": f"Error en el procesamiento: {e}",
            "acciones": acciones_ejecutadas if 'acciones_ejecutadas' in locals() else [],
            "cliente": nombre_cliente
        }
    
