import logging
from typing import Dict, Any
from models.models import WebhookPayload
from utils.database import insertar_flujo_correo_post_agente
from utils.email_service import enviar_correo_renovacion, enviar_correo_webinar
from utils.notify_error import info_notify, error_notify
from utils.config import settings

#validaciones para el envio de correo para la rennovacion
async def procesar_webhook_renovacion(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la logica de negocio del webhook.
    """
    input_vars = payload.input_variables
    extracted_vars = payload.extracted_variables

    # 1. Validaciones para decidir si se envia correo
    enviar_correo = True
    
    logging.info(f"payloads recibidos: input_vars={input_vars},\n extracted_vars={extracted_vars}")

    if extracted_vars.interes_renovar is not None:
        interes_str = str(extracted_vars.interes_renovar).strip()
        if interes_str.lower() == "no" or interes_str == "":
            enviar_correo = False
            logging.info("Validacion: 'interes_renovar' es 'No' o vacio. No se enviara correo.")
            msg = (
                f"El cliente {input_vars.NOMBRE_TITULAR} con correo {input_vars.CORREO} "
                "no esta interesado en renovar."
            )
            # Notificamos que el cliente no quiere renovar para auditar la decisión
            await error_notify(
                method_name="procesar_webhook_renovacion",
                client_id=input_vars.NOMBRE_TITULAR,
                error_message=msg,
            )

    if extracted_vars.contesto_llamada is False and extracted_vars.estado is False:
        enviar_correo = False
        # Registramos que no atendió la llamada para no disparar correos
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=(
                f"El cliente {input_vars.NOMBRE_TITULAR} con correo {input_vars.CORREO} "
                "no contesto llamada y estado es False."
            ),
        )
        logging.info("Validacion: 'contesto_llamada' y 'estado' son False. No se enviara correo.")

    if not enviar_correo:
        logging.info("No se cumplieron las validaciones para enviar el correo.")
        msg = (
            f"El cliente {input_vars.NOMBRE_TITULAR} con correo {input_vars.CORREO} "
            "no cumple condiciones para envio de correo."
        )
        # Confirmamos por correo/telegram que el flujo no envió email
        await info_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            info_message=msg,
        )
        return {
            "status": "success",
            "message": "Validaciones no cumplidas, no se envio correo.",
        }

    # 3. Enviar correo si la validacion pasa

    primer_name = extracted_vars.primer_name

    # --- Validación de decisión de correo ---
    # Si desicion_correo es True, usar CORREO de input_vars
    # Si desicion_correo es False, usar correo_cliente de extracted_vars
    if extracted_vars.desicion_correo is True:
        destinatario = input_vars.CORREO
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif extracted_vars.desicion_correo is False:
        destinatario = extracted_vars.correo_cliente
        logging.info(f"Usando correo proporcionado por cliente (desicion_correo=False): {destinatario}")
    else:
        # Si no se proporciona desicion_correo, usar correo_cliente por defecto
        destinatario = extracted_vars.correo_cliente
        logging.info(f"desicion_correo no definido, usando correo_cliente por defecto: {destinatario}")

    if not destinatario:
        logging.warning("No se puede enviar correo, no hay destinatario (ni CORREO ni correo_cliente).")
        # Persistimos la falta de destinatario para rastreo sin romper el flujo
        await error_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            error_message=(
                f"No se pudo enviar correo por falta de destinatario. "
                f"al correo {input_vars.CORREO} y {extracted_vars.correo_cliente}"
            ),
        )
        return {
            "status": "warning",
            "message": "No se pudo enviar correo por falta de destinatario.",
        }
        
        
    logging.info(f"Validacion superada. Intentando enviar correo a: {input_vars.NOMBRE_TITULAR} con correo : {destinatario}")

    numero_telefono_input = getattr(input_vars, "Celular", None)
    linea_universitaria = getattr(input_vars, "LINEA_CREDITO", None)
    
    if not numero_telefono_input:
        logging.warning("No se recibio numero_telefono en el payload. No se registrara flujo en BD.")
    if not linea_universitaria:
        logging.warning("No se recibio linea_universitaria en el payload.")
    
    # if input_vars.CORREO:
    #     destinatario = input_vars.CORREO
    #     logging.info(f"Usando correo extraido con variables entrantes: {destinatario}")
    # else:
    #     destinatario = extracted_vars.correo_cliente
    #     logging.info(f"Usando correo proporcionado por cliente: {destinatario}")

    try:
        link_whatsapp_asesor = "https://wa.me/573182856386"  # Link de asesor

        confirmacion_response = await enviar_correo_renovacion(
            destinatario=destinatario,
            nombre=primer_name or "Cliente Onetwocredit",
            link_renovacion=input_vars.LINK,
            semestre=str(input_vars.SEMESTRE),
            link_asesor=link_whatsapp_asesor,
        )
        logging.info(confirmacion_response)
        # Guardamos evidencia del correo enviado (email + telegram)
        await info_notify(
            method_name="procesar_webhook_renovacion",
            client_id=input_vars.NOMBRE_TITULAR,
            info_message=(
                f"{confirmacion_response} hacia {destinatario} "
                f"para el cliente {input_vars.NOMBRE_TITULAR}."
                f"con correo {destinatario}"
            ),
        )

        flujo_id = None

        # 4. Solo intentamos guardar en BD si tenemos telefono y linea
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
                # El correo ya fue enviado; no lanzamos error para no romper el webhook.
        else:
            logging.warning("No se registrara en BD por faltar numero_telefono o linea_universitaria.")

        payload_respuesta = {
            "status": "success",
            "enviado_a": input_vars.NOMBRE_TITULAR,
            "correo_destinatario": destinatario,
            "link_renovacion": input_vars.LINK,
            "status_correo": "Enviado",
            "numero_telefono": numero_telefono_input,
            "linea_universitaria": linea_universitaria,
            "id_flujo_bd": flujo_id,
        }
        return payload_respuesta

    except Exception as e:
        logging.error(f"Error al enviar correo: {e}", exc_info=True)
        raise Exception(f"Error en el servicio de envio de correo: {e}")
    
    

#validaciones par
async def procesar_webhook_webinar(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la logica de negocio del webhook.
    """
    logging.info(f"payloads recibidos: {payload}")
    
    enviar_correo = True
    variables_entrada = payload.input_variables
    variables_extraidas = payload.extracted_variables
    
    
    if variables_extraidas.interessolicitud is not None:
        interes_str = str(variables_extraidas.interessolicitud).strip()
        if interes_str.lower() == "no" or interes_str == "":
            enviar_correo = False
            logging.info("Validacion: 'interessolicitud' es 'No' o vacio. No se enviara correo.")
            msg = (
                f"El cliente {variables_entrada.Nombre} con correo {variables_entrada.EMAIL} "
                "no esta interesado en el webinar."
            )
            await error_notify(
                method_name="procesar_webhook_webinar",
                client_id=variables_entrada.Nombre,
                error_message=msg,
            )
    
    if variables_extraidas.contesto_llamada is False and variables_extraidas.estado is False:
        enviar_correo = False
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=(
                f"El cliente {variables_entrada.Nombre} con correo {variables_entrada.EMAIL} "
                "no contesto llamada y estado es False."
            ),
        )
        logging.info("Validacion: 'contesto_llamada' y 'estado' son False. No se enviara correo.")
        
    if not enviar_correo:
        logging.info("No se cumplieron las validaciones para enviar el correo.")
        msg = (
            f"El cliente {variables_entrada.Nombre} con correo {variables_entrada.EMAIL} "
            "no cumple condiciones para envio de correo."
        )
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=msg,
        )
        return {
            "status": "success",
            "message": "Validaciones no cumplidas, no se envio correo.",
        }
        
    # 3. Enviar correo si la validacion pasa
    logging.info(f"Validacion superada. Intentando enviar correo a: {variables_entrada.Nombre} con correo guardado y extraida de variable de entrada : {variables_entrada.EMAIL}")
    
    
    # --- Validación de decisión de correo ---
    # Si desicion_correo es True, usar EMAIL de variables_entrada
    # Si desicion_correo es False, usar correo_cliente de variables_extraidas
    if variables_extraidas.desicion_correo is True:
        destinatario = variables_entrada.EMAIL
        logging.info(f"Usando correo guardado (desicion_correo=True): {destinatario}")
    elif variables_extraidas.desicion_correo is False:
        destinatario = variables_extraidas.correo_cliente
        logging.info(f"Usando correo proporcionado por cliente (desicion_correo=False): {destinatario}")
    else:
        # Si no se proporciona desicion_correo, usar correo_cliente por defecto
        destinatario = variables_extraidas.correo_cliente
        logging.info(f"desicion_correo no definido, usando correo_cliente por defecto: {destinatario}")
        
    if not destinatario:
        logging.warning("No se puede enviar correo, no hay destinatario (ni CORREO ni correo_cliente).")
        await error_notify(
            method_name="procesar_webhook_webinar",
            client_id=variables_entrada.Nombre,
            error_message=(
                f"No se pudo enviar correo por falta de destinatario. "
                f"para la persona {variables_entrada.Nombre}"
            ),
        )
        return {
            "status": "warning",
            "message": "No se pudo enviar correo por falta de destinatario.",
        }
        
    
    numero_telefono_input = getattr(variables_entrada, "Contacto", None)
    if not numero_telefono_input:
        logging.warning("No se recibio numero_telefono en el payload. No se registrara flujo en BD.")
        
    logging.info(f"Validacion superada. Intentando enviar correo a: {variables_entrada.Nombre} con correo : {variables_entrada.EMAIL}")
    await info_notify(
        method_name="procesar_webhook_webinar",
        client_id=variables_entrada.Nombre,
        info_message=(
            f"Intentando enviar correo a: {variables_entrada.Nombre} con correo : {variables_entrada.EMAIL}"
        ),
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
            info_message=(
                f"{confirmacion_response} hacia {destinatario} "
                f"para el cliente {variables_entrada.Nombre}."
                f"con correo {destinatario}"
            ),
        )
        
        
        flujo_id = None

        # 4. Solo intentamos guardar en BD si tenemos telefono
        if variables_entrada.Contacto:
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
                # El correo ya fue enviado; no lanzamos error para no romper el webhook.
        else:
            logging.warning("No se registrara en BD por faltar numero_telefono.")
        
        payload_respuesta = {
            "status": "success",
            "enviado_a": variables_entrada.Nombre,
            "correo_destinatario": destinatario,
            "status_correo": "Enviado",
            "numero_telefono": numero_telefono_input,
        }
        return payload_respuesta
    
    except Exception as e:
        logging.error(f"Error al enviar correo: {e}", exc_info=True)
        raise Exception(f"Error en el servicio de envio de correo: {e}")
        
    
    
