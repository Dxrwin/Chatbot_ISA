import logging
from typing import Dict, Any
import time
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
            "status": "warning",
            "message": "No se pudo enviar correo por falta de destinatario.",
        }

    # 2. VALIDACIONES (sin notificaciones duplicadas)
    enviar_correo = True
    razon_rechazo = None

    if extracted_vars.interes_renovar is not None:
        interes_str = str(extracted_vars.interes_renovar).strip()
        if interes_str.lower() == "no" or interes_str == "":
            enviar_correo = False
            razon_rechazo = f"Cliente no interesado en renovar"
            logging.info("Validacion: 'interes_renovar' es 'No' o vacio.")

    if extracted_vars.contesto_llamada is False and extracted_vars.estado is False:
        enviar_correo = False
        razon_rechazo = f"Cliente no contesto llamada y estado es False"
        logging.info("Validacion: 'contesto_llamada' y 'estado' son False.")

    # Si no pasa validaciones, notificar UNA sola vez y retornar
    if not enviar_correo:
        logging.info(f"No se cumplieron las validaciones: {razon_rechazo}")
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=f"{razon_rechazo}. Cliente: {input_vars.NOMBRE_TITULAR}, Correo: {destinatario}",
        )
        return {
            "status": "success",
            "message": "Validaciones no cumplidas, no se envio correo.",
        }

    # 3. ENVIAR CORREO (si pasó todas las validaciones)
    logging.info(f"Validacion superada. Intentando enviar correo a: {input_vars.NOMBRE_TITULAR} ({destinatario})")

    numero_telefono_input = getattr(input_vars, "Celular", None)
    linea_universitaria = getattr(input_vars, "LINEA_CREDITO", None)
    primer_name = extracted_vars.primer_name

    if not numero_telefono_input:
        logging.warning("No se recibio numero_telefono en el payload.")
    if not linea_universitaria:
        logging.warning("No se recibio linea_universitaria en el payload.")

    try:
        link_whatsapp_asesor = "https://wa.me/573182856386"

        confirmacion_response = await enviar_correo_renovacion(
            destinatario=destinatario,
            nombre=primer_name or "Cliente Onetwocredit",
            link_renovacion=input_vars.LINK,
            semestre=str(input_vars.SEMESTRE),
            link_asesor=link_whatsapp_asesor,
        )
        logging.info(confirmacion_response)
        
        await info_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            info_message=f"{confirmacion_response} hacia {destinatario} para {input_vars.NOMBRE_TITULAR}",
        )

        flujo_id = None

        if numero_telefono_input and linea_universitaria:
            try:
                flujo_id = await insertar_flujo_correo_post_agente(
                    nombre_cliente=input_vars.NOMBRE_TITULAR,
                    correo_enviado=destinatario,
                    numero_telefono=numero_telefono_input,
                    linea_universitaria=linea_universitaria,
                )
                logging.info(f"Flujo registrado en BD con ID: {flujo_id}")
            except Exception as e:
                logging.error(f"Error al registrar flujo en BD: {e}", exc_info=True)

        return {
            "status": "success",
            "enviado_a": input_vars.NOMBRE_TITULAR,
            "correo_destinatario": destinatario,
            "link_renovacion": input_vars.LINK,
            "status_correo": "Enviado",
            "numero_telefono": numero_telefono_input,
            "linea_universitaria": linea_universitaria,
            "id_flujo_bd": flujo_id,
        }

    except Exception as e:
        logging.error(f"Error al enviar correo: {e}", exc_info=True)
        # Notificar el error pero no lanzar excepción para evitar que el caller
        # (webhook sender) reintente la petición y provoque envíos duplicados.
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=f"Error en el servicio de envio de correo: {e}",
        )
        return {
            "status": "error",
            "message": f"Error en el servicio de envio de correo: {e}",
        }

#validaciones par
async def procesar_webhook_webinar(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la logica de negocio del webhook.
    """
    #logging.info(f"payloads recibidos: {payload}")
    
    variables_entrada = payload.input_variables
    variables_extraidas = payload.extracted_variables

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
        confirmacion_response = await enviar_correo_webinar(
            destinatario=destinatario,
            nombre=primer_name or "Cliente Onetwocredit",
        )
        logging.info(confirmacion_response)
        await info_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            info_message=f"{confirmacion_response} hacia {destinatario} para {variables_entrada.Nombre}",
        )
        
        flujo_id = None

        if numero_telefono_input:
            try:
                flujo_id = await insertar_flujo_correo_post_agente(
                    nombre_cliente=variables_entrada.Nombre,
                    correo_enviado=destinatario,
                    numero_telefono=numero_telefono_input,
                    linea_universitaria=variables_extraidas.objetivo,
                )
                logging.info(f"Flujo registrado en BD con ID: {flujo_id}")
            except Exception as e:
                logging.error(f"Error al registrar flujo en BD: {e}", exc_info=True)
        
        return {
            "status": "success",
            "enviado_a": variables_entrada.Nombre,
            "correo_destinatario": destinatario,
            "status_correo": "Enviado",
            "numero_telefono": numero_telefono_input,
        }
    
    except Exception as e:
        logging.error(f"Error al enviar correo: {e}", exc_info=True)
        # Notificar el error y devolver un resultado de error en lugar de lanzar.
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=(
                "problemas con el servidor de envio de correos "
                "(reintentos agotados, requiere reintento manual)"
            ),
        )
        mensaje_error = "problemas con el servidor de envio de correos"
        return {
            "status": "error",
            "message": mensaje_error,
        }
        
    
    
