import logging
from typing import Dict, Any
import time
import httpx
from models.models import WebhookPayload
from utils.database import insertar_flujo_correo_post_agente
from utils.email_service import enviar_correo_renovacion, enviar_correo_webinar
from utils.notify_error import info_notify, error_notify
from utils.database import insertar_log
#from utils.config import settings
from utils.registrar_bitrix import registrar_en_bitrix
from utils.whatsapp_service import enviar_whatsapp_renovacion
from utils.linea_credito_links import obtener_link_por_linea_credito
import os
import re
import unicodedata

def normalizar_texto_correo(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return re.sub(r"[\s_-]+", " ", texto).strip()

def normalizar_objetivo_correo(valor: Any) -> str:
    texto = normalizar_texto_correo(valor)
    aliases = {
        "renovacion": "renovacion",
        "renovaciones": "renovacion",
        "renovacion credito": "renovacion",
        "renovacion de credito": "renovacion",
        "webinar": "webinar",
        "cobranza": "cobranzas",
        "cobranzas": "cobranzas",
    }
    return aliases.get(texto, texto)

def correo_valido_envio(valor: Any) -> bool:
    if valor is None:
        return False
    texto = str(valor).strip()
    if not texto:
        return False
    patron = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
    return re.fullmatch(patron, texto) is not None

def _datos_modelo(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if isinstance(obj, dict):
        return obj
    return {
        key: value
        for key, value in vars(obj).items()
        if not key.startswith("_") and value is not None
    }

def obtener_correo_desde_modelo(obj: Any, campos_preferidos: list[str]) -> tuple[str | None, str | None]:
    datos = _datos_modelo(obj)

    for campo in campos_preferidos:
        valor = datos.get(campo)
        if correo_valido_envio(valor):
            return str(valor).strip(), campo

    for campo, valor in datos.items():
        nombre = str(campo).lower()
        if ("correo" in nombre or "email" in nombre) and correo_valido_envio(valor):
            return str(valor).strip(), str(campo)

    return None, None

def resolver_destinatario_correo(input_vars: Any, extracted_vars: Any) -> tuple[str | None, str]:
    correo_cliente, fuente_cliente = obtener_correo_desde_modelo(
        extracted_vars,
        ["correo_cliente", "correoCliente", "correo", "email", "CORREO", "EMAIL"],
    )
    correo_guardado, fuente_guardado = obtener_correo_desde_modelo(
        input_vars,
        ["CORREO", "correo", "EMAIL", "email", "Email"],
    )

    if getattr(extracted_vars, "desicion_correo", None) is False:
        if correo_cliente:
            return correo_cliente, f"extracted_variables.{fuente_cliente}"
        if correo_guardado:
            return correo_guardado, f"input_variables.{fuente_guardado}"
        return None, "sin_correo_valido"

    if getattr(extracted_vars, "desicion_correo", None) is True:
        if correo_guardado:
            return correo_guardado, f"input_variables.{fuente_guardado}"
        if correo_cliente:
            return correo_cliente, f"extracted_variables.{fuente_cliente}"
        return None, "sin_correo_valido"

    if correo_cliente:
        return correo_cliente, f"extracted_variables.{fuente_cliente}"
    if correo_guardado:
        return correo_guardado, f"input_variables.{fuente_guardado}"
    return None, "sin_correo_valido"

def obtener_base_url_bitrix_correo() -> str:
    """
    Obtiene la URL base de Bitrix desde variables de entorno.

    No debe existir ningún webhook hardcodeado en el código.
    """

    base_url = (
        os.getenv("BITRIX_CORREO_BASE_URL")
        or os.getenv("BITRIX_BASE_URL")
        or ""
    ).rstrip("/")

    if not base_url:
        raise RuntimeError(
            "No está configurada BITRIX_CORREO_BASE_URL ni BITRIX_BASE_URL."
        )

    return base_url


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
        base_url_bitrix = obtener_base_url_bitrix_correo()

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
        url_contact = f"{base_url_bitrix}/crm.contact.list.json"
        
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
                logging.error(f"Error al obtener el contacto")
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
            
            url_deal = f"{base_url_bitrix}/crm.deal.add.json"
            
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
                logging.error(f"Error al crear el deal")
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
            
            logging.info(f"Deal creado correctamente - ID: {id_deal}")
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
        logging.error(f"Error de conexión a Bitrix24: {e}")
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
        logging.error(f"Error inesperado en integracion_bitrix: {e}", exc_info=True)
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
    destinatario, fuente_destinatario = resolver_destinatario_correo(input_vars, extracted_vars)
    interes_renovar_str = None
    logging.info(
        "Destinatario resuelto para renovacion | correo=%s | fuente=%s | desicion_correo=%s",
        destinatario,
        fuente_destinatario,
        extracted_vars.desicion_correo,
    )

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
    objetivo_value = normalizar_objetivo_correo(
        getattr(extracted_vars, "objetivo", None)
        or getattr(input_vars, "SEGMENTO_CAMPANA", None)
        or ""
    )
    requseguimiento_value = normalizar_texto_correo(getattr(extracted_vars, "requseguimiento", ""))
    aceptainfocorreo_value = getattr(extracted_vars, "aceptainfocorreo", None)
    seguimiento_positivo = requseguimiento_value in {"si", "true", "1", "yes", "requiere", "requerido"}
    seguimiento_negativo = requseguimiento_value in {"no", "false", "0", "not", "no requiere", "no requerido"}
    no_autorizo_correo = aceptainfocorreo_value is False
    
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
        logging.info(f"Enviando correo: ambiguedad=True y (envio_correo=True O intrsrenovarbool=True)")
    
    elif envio_correo_value is True:
        enviar_correo = True
        logging.info(f"Enviando correo: envio_correo=True")
    
    elif intrsrenovarbool_value is True:
        enviar_correo = True
        logging.info(f"Enviando correo: intrsrenovarbool=True")

    elif objetivo_value == "renovacion" and seguimiento_positivo:
        enviar_correo = True
        logging.info("Enviando correo: objetivo=renovacion y requseguimiento positivo")
    
    else:
        # No se cumplen las condiciones para enviar correo
        if no_autorizo_correo or seguimiento_negativo:
            motivo_no_envio = (
                "El cliente no autorizo envio de informacion por correo."
                if no_autorizo_correo
                else "La variable requseguimiento indica que no se requiere envio de correo."
            )
            logging.info(
                "%s Cliente: %s, destinatario disponible: %s",
                motivo_no_envio,
                input_vars.NOMBRE_TITULAR,
                bool(destinatario),
            )
            return {
                "status": "success",
                "message": motivo_no_envio,
                "correo_enviado": False,
                "destinatario": destinatario,
                "intentos_correo": 0,
                "motivo_no_envio": motivo_no_envio,
            }

        if not destinatario:
            mensaje_error = "No se pudo enviar correo por falta de destinatario."
            logging.warning(f" {mensaje_error}")
        elif ambiguedad_value is False and envio_correo_value is False and intrsrenovarbool_value is False:
            mensaje_error = "El cliente no tiene interés en renovar (ambiguedad=False, envio_correo=False, intrsrenovarbool=False)."
            logging.info(f" {mensaje_error}")
        elif ambiguedad_value is True and envio_correo_value is False and intrsrenovarbool_value is False:
            mensaje_error = "Existe ambigüedad pero el cliente no autorizó el envío (envio_correo=False, intrsrenovarbool=False)."
            logging.info(f"{mensaje_error}")
        else:
            mensaje_error = "No se cumplen las condiciones para enviar correo."
            logging.info(f" {mensaje_error}")
        
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
        logging.warning(" Variable enviar_correo es False, no se procede con el envío.")
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
            logging.error(f"Error en envio de correo de renovacion a {destinatario}: {confirmacion_response.get('message')}")
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
        logging.info(f"Correo enviado exitosamente a: {destinatario} en intento {confirmacion_response.get('intentos')}")
        
        await info_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            info_message=f"Correo de renovación enviado exitosamente a {destinatario} en intento {confirmacion_response.get('intentos')} para {input_vars.NOMBRE_TITULAR}",
        )
        
                # ===========================
        # NUEVO: envío de WhatsApp
        # ===========================
        try:
            # 1) Obtener link por la LINEA_CREDITO
            link_renovacion = obtener_link_por_linea_credito(linea_universitaria)

            if not link_renovacion:
                logging.warning(
                    "No se envía WhatsApp porque no hay link mapeado para LINEA_CREDITO='%s'",
                    linea_universitaria,
                )
            else:
                # 2) Número en formato internacional (asegúrate de esto)
                telefono_cliente = str(numero_telefono_input)
                # Si en BD viene como '3006803158', probablemente tienes que anteponer '57'
                if telefono_cliente.startswith("3"):
                    telefono_cliente = f"57{telefono_cliente}"

                whatsapp_resp = await enviar_whatsapp_renovacion(
                    telefono=telefono_cliente,
                    nombre_cliente=primer_name or input_vars.NOMBRE_TITULAR or "Cliente One2credit",
                    link_redirect=link_renovacion,
                )

                if whatsapp_resp.get("status") != "success":
                    logging.error(
                        "❌ Error enviando WhatsApp de renovación a %s: %s",
                        telefono_cliente,
                        whatsapp_resp,
                    )
                    await error_notify(
                        method_name="procesar_webhook_renovacion",
                        client_id=input_vars.NOMBRE_TITULAR,
                        error_message=(
                            f"WhatsApp no enviado para renovación. "
                            f"Teléfono: {telefono_cliente}. Detalle: {whatsapp_resp}"
                        ),
                    )
                else:
                    logging.info(
                        "✅ WhatsApp de renovación enviado a %s: %s",
                        telefono_cliente,
                        whatsapp_resp,
                    )
                    await info_notify(
                        method_name="procesar_webhook_renovacion",
                        client_id=input_vars.NOMBRE_TITULAR,
                        info_message=(
                            f"WhatsApp de renovación enviado a {telefono_cliente} "
                            f"para {input_vars.NOMBRE_TITULAR}"
                        ),
                    )

        except Exception as e:
            logging.error(
                "Excepción enviando WhatsApp de renovación: %s",
                e,
                exc_info=True,
            )
            await error_notify(
                method_name="procesar_webhook_renovacion",
                client_id=input_vars.NOMBRE_TITULAR,
                error_message=f"Excepción enviando WhatsApp: {e}",
            )
        # ===========================
        # FIN NUEVO BLOQUE
        # ===========================


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
    destinatario, fuente_destinatario = resolver_destinatario_correo(
        variables_entrada,
        variables_extraidas,
    )
    logging.info(
        "Destinatario resuelto para webinar | correo=%s | fuente=%s | desicion_correo=%s",
        destinatario,
        fuente_destinatario,
        variables_extraidas.desicion_correo,
    )

    if not destinatario:
        logging.warning("No hay destinatario disponible.")
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"No se pudo enviar correo por falta de destinatario.",
        )
        await insertar_log(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"No se pudo enviar correo por falta de destinatario.",
            http_code=400,
            tipo="error"
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
                await insertar_log(
                    method_name="procesar_webhook_webinar",
                    client_id=variables_entrada.Nombre,
                    error_message=f"Correo webinar enviado pero error al registrar flujo en BD: {e}",
                    http_code=500,
                    tipo="error"
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
        #insertar en la base de datos el error
        await insertar_log(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=f"Excepción en servicio de correo de webinar: {e}",
            http_code=500,
            tipo="error"
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
    destinatario, fuente_destinatario = resolver_destinatario_correo(input_vars, extracted_vars)
    numero_telefono = getattr(input_vars, "Celular", None) or getattr(input_vars, "Contacto", None)
    #linea_universitaria = getattr(input_vars, "LINEA_CREDITO", None) or getattr(extracted_vars, "objetivo", None)
    
    # Decidir destinatario
    if extracted_vars.desicion_correo is True:
        destinatario = destinatario
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif extracted_vars.desicion_correo is False:
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_valido_envio(correo_cliente):
            destinatario = correo_cliente
            logging.info(f"Usando correo cliente (desicion_correo=False): {destinatario}")
        else:
            logging.info(f"Correo cliente vacío, usando guardado por defecto")
    else:
        correo_cliente = getattr(extracted_vars, "correo_cliente", None)
        if correo_valido_envio(correo_cliente):
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
    
async def procesar_webhook_cobranzas(payload: WebhookPayload) -> Dict[str, Any]:
    logging.info("Iniciando procesamiento de webhook de cobranzas")
    """
    Servicio de Cobranzas:
    Evalúa si el cliente está interesado para mandarlo a Bitrix, 
    de lo contrario, le dispara un correo de seguimiento.
    """
    var_in = payload.input_variables
    var_ex = payload.extracted_variables
    nombre_cliente = var_in.Nombre or var_in.NOMBRE_TITULAR or "Cliente No Identificado"
    
    # [LOG] Trazamos el estado general para saber con qué estamos lidiando
    logging.info(f"[COBRANZAS] Iniciando análisis para {nombre_cliente}. Objetivo: {var_ex.objetivo}")

    # =======================================================
    # PASO 1: EXTRACCIÓN Y LIMPIEZA DE VARIABLES CLAVE
    # =======================================================
    # Nota del Senior: La IA a veces manda 'Sí', 'Si', 'si', o None. Hay que normalizar eso.
    
    # Extraemos la variable "interes" (Asumimos que viene en 'interessolicitud' o 'tipo_interes')
    interes_raw = str(var_ex.interessolicitud or var_ex.tipo_interes or "").strip().lower()
    # Lo convertimos a un booleano confiable
    interes_positivo = interes_raw in ["sí", "si", "yes", "true"]
    
    confirm_interes = var_ex.confirmacion_interes is True
    estado_llamada = var_ex.estado is True
    objetivo_llamada = str(var_ex.objetivo or "").strip().lower()
    
    # Variables secundarias (por si nos sirven para los logs o Bitrix)
    mas_info = var_ex.mas_informacion is True
    autori_contacto = var_ex.autorizacion_contacto is True

    logging.info(f"[COBRANZAS] Variables limpias -> interes_positivo: {interes_positivo}, "
                f"confirm_interes: {confirm_interes}, estado_llamada: {estado_llamada}, "
                f"mas_info: {mas_info}, autori_contacto: {autori_contacto}")

    # =======================================================
    # PASO 2: RESOLVER EL DESTINATARIO (Igual que antes, blindado)
    # =======================================================
    destinatario, fuente_destinatario = resolver_destinatario_correo(var_in, var_ex)
    logging.info(
        "[COBRANZAS] Destinatario resuelto | correo=%s | fuente=%s | desicion_correo=%s",
        destinatario,
        fuente_destinatario,
        var_ex.desicion_correo,
    )

    if not destinatario:
        logging.error(f"[COBRANZAS] Sin destinatario para {nombre_cliente}. Abortando.")
        return {"status": "warning", "message": "Falta de destinatario."}

    # =======================================================
    # PASO 3: CONTROL DE SPAM / IDEMPOTENCIA
    # =======================================================
    # dedup_key = f"{destinatario.lower()}|{nombre_cliente.lower()}"
    # if _marcar_y_verificar_reenvio_cobranzas(dedup_key):
    #     logging.warning(f"[COBRANZAS] Cliente {nombre_cliente} ya procesado hoy. Ignorando.")
    #     return {"status": "success", "message": "Solicitud ya procesada.", "correo_enviado": False}

    # =======================================================
    # PASO 4: EL CEREBRO DE LA OPERACIÓN (Bitrix vs Correo)
    # =======================================================
    
    # [CONDICIÓN 1]: INTERÉS POSITIVO -> SE VA A BITRIX
    # Regla: interes == "Sí", confirm_interes == True, objetivo == "Cobranza", estado == True
    if interes_positivo and confirm_interes and objetivo_llamada in ["cobranza", "cobranzas"] and estado_llamada:
        logging.info(f"[COBRANZAS] ¡JACKPOT! El cliente {nombre_cliente} mostró interés sólido. Enviando a Bitrix...")
        
        # Preparamos el payload que le mandaremos al CRM
        datos_bitrix = {
            "nombre": nombre_cliente,
            "correo": destinatario,
            "telefono": var_in.Contacto,
            "tipo_interes": var_ex.tipo_interes,
            "objetivo": var_ex.objetivo,
            "razon": var_ex.razon_cliente,
            "autoriza_contacto": autori_contacto
        }
        logging.info(f"[COBRANZAS] Payload para Bitrix: {datos_bitrix}")
        
        try:
            # Invocamos el servicio de Bitrix
            bitrix_response = await registrar_en_bitrix(datos_bitrix["telefono"], datos_bitrix["objetivo"])
            
            logging.info(f"[COBRANZAS] Respuesta de Bitrix: {bitrix_response}")
            
            if bitrix_response.get("status") == "success":
                logging.info(f"[COBRANZAS] Cliente registrado en Bitrix con éxito. ID: {bitrix_response.get('lead_id')}")
                return {
                    "status": "success",
                    "message": "Cliente interesado, registrado en Bitrix.",
                    "accion": "registro_bitrix",
                    "data": bitrix_response
                }
            else:
                # Si Bitrix se cae, lo logueamos, pero no detenemos el mundo
                logging.error(f"[COBRANZAS] Falló el registro en Bitrix: {bitrix_response}")
                return {"status": "error", "message": "Falló Bitrix", "accion": "registro_bitrix"}
                
        except Exception as e:
            logging.error(f"[COBRANZAS] Excepción al contactar Bitrix: {e}", exc_info=True)
            return {"status": "error", "message": f"Error Bitrix: {e}"}


    # [CONDICIÓN 2]: INTERÉS NEGATIVO O VACÍO -> SE VA POR CORREO
    # Si llegó hasta aquí, significa que alguna de las variables falló (ej. no confirmó, o pidió más info sin confirmar).
    else:
        motivo_correo = "Falta de interés explícito o variables incompletas"
        if not interes_positivo: motivo_correo = "Cliente indicó No interés"
        elif not confirm_interes: motivo_correo = "No hubo confirmación de interés"
        elif not estado_llamada: motivo_correo = "La llamada no finalizó en estado positivo"
        
        logging.info(f"[COBRANZAS] El cliente no cumple criterios para Bitrix ({motivo_correo}). Disparando correo de retención/información a {destinatario}...")
        
        primer_name = var_ex.primer_name or nombre_cliente

        try:
            # Enviamos el correo
            if destinatario:
                primer_name = var_ex.primer_name or "Cliente One2credit"
                link_whatsapp = "https://wa.me/573182856386"
                
                try:
                    respuesta_correo = await enviar_correo_renovacion(
                        destinatario=destinatario,
                        nombre=primer_name,
                        semestre=getattr(var_in, "SEMESTRE", ""),
                        link_asesor=link_whatsapp
                    )
                    
                    if respuesta_correo.get("status") == "success":
                        logging.info(f"✅ Correo enviado a {destinatario}")
                        await info_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            info_message=f"Correo informativo enviado a {destinatario}"
                        )
                        
                        return {
                            "status": "success",
                            "message": "Correo informativo enviado exitosamente."
                        }
                        
                    else:
                        error_msg = f"No se envió correo: {respuesta_correo.get('message')}"
                        logging.error(f"❌ {error_msg}")
                        await error_notify(
                            method_name="procesar_llamada_renovacionYrefinanciamiento",
                            client_id=nombre_cliente,
                            error_message=error_msg
                        )
                        
                        return {
                            "status": "error",
                            "message": error_msg,
                        }
                        
                except Exception as e:
                    error_msg = f"Error al enviar correo: {e}"
                    logging.error(f"❌ {error_msg}", exc_info=True)
                    await error_notify(
                        method_name="procesar_llamada_renovacionYrefinanciamiento",
                        client_id=nombre_cliente,
                        error_message=error_msg
                    )
            
            if respuesta_correo.get("status") != "success":
                logging.error(f"[COBRANZAS] Fallo al enviar correo: {respuesta_correo}")
                return {"status": "error", "message": "Fallo en proveedor de correos"}
                
            logging.info(f"[COBRANZAS] Correo enviado exitosamente a {destinatario}.")
            
            # Guardamos en nuestra BD interna el registro del flujo
            numero_telefono_input = getattr(var_in, "Contacto", None)
            if numero_telefono_input:
                try:
                    flujo_id = await insertar_flujo_correo_post_agente(
                        nombre_cliente=nombre_cliente,
                        correo_enviado=destinatario,
                        numero_telefono=numero_telefono_input,
                        linea_universitaria=var_ex.objetivo or "COBRANZAS",
                    )
                    logging.info(f"[COBRANZAS] Registro en BD exitoso. Flujo ID: {flujo_id}")
                except Exception as bd_err:
                    logging.error(f"[COBRANZAS] Correo enviado, pero BD falló: {bd_err}")
            
            return {
                "status": "success",
                "message": f"Correo enviado. Razón: {motivo_correo}",
                "accion": "envio_correo",
                "correo_enviado": True,
                "destinatario": destinatario
            }
            
        except Exception as e:
            logging.error(f"[COBRANZAS] Excepción al enviar correo: {e}", exc_info=True)
            return {"status": "error", "message": f"Fallo interno al enviar correo: {e}"}
