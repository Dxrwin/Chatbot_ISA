import logging
from typing import Dict, Any

# Modelos Pydantic
from models.models import WebhookPayload, InputVariables, ExtractedVariables

# Servicios desacoplados
from utils.database import guardar_en_bd
from utils.email_service import enviar_correo_renovacion

async def procesar_webhook(payload: WebhookPayload) -> Dict[str, Any]:
    """
    Servicio principal que orquesta la lógica de negocio del webhook.
    """
    input_vars = payload.input_variables
    extracted_vars = payload.extracted_variables

    # 1. Guardar en la base de datos
    try:
        # La función de BD ahora es asíncrona o se ejecuta en un thread pool
        # Para este ejemplo, la mantendremos sincrónica por simplicidad
        # En un entorno de producción real, usarías una librería async de BD
        guardar_en_bd(input_vars)
        logging.info(f"Datos guardados en BD para: {input_vars.NOMBRE_TITULAR}")
    except Exception as e:
        logging.error(f"Error al guardar en BD durante el procesamiento: {e}")
        # Puedes decidir si continuar o fallar aquí
        # Por ahora, solo lo logueamos y continuamos
    
    # 2. Extraer variables y aplicar lógica de validación
    enviar_correo = True
    
    # Si interes_renovar contiene 'No' o está vacío, no se envía correo
    if extracted_vars.interes_renovar is not None and \
        (str(extracted_vars.interes_renovar).strip().lower() == "no" or \
        str(extracted_vars.interes_renovar).strip() == ""):
        enviar_correo = False
        logging.info("Validación: 'interes_renovar' es 'No' o vacío. No se enviará correo.")

    # Si contesto_llamada es False y estado es False, no se envía correo
    if extracted_vars.contesto_llamada is False and extracted_vars.estado is False:
        enviar_correo = False
        logging.info("Validación: 'contesto_llamada' y 'estado' son False. No se enviará correo.")

    # 3. Enviar correo si la validación pasa
    if enviar_correo:
        logging.info(f"Validación superada. Intentando enviar correo a: {input_vars.NOMBRE_TITULAR}")
        
        # Determinar el destinatario
        destinatario = input_vars.CORREO if input_vars.CORREO else extracted_vars.correo_cliente
        
        if not destinatario:
            logging.warning("No se puede enviar correo, no hay destinatario (ni CORREO ni correo_cliente).")
            return {"status": "warning", "message": "Datos guardados, pero no se pudo enviar correo por falta de destinatario."}

        try:
            link_whatsapp_asesor = "https://wa.me/573182856386" # Link de asesor
            
            await enviar_correo_renovacion(
                destinatario=destinatario,
                nombre=input_vars.NOMBRE_TITULAR or "Cliente", # Fallback por si el nombre es None
                link_renovacion=input_vars.LINK,
                semestre=str(input_vars.SEMESTRE),
                link_asesor=link_whatsapp_asesor
            )
            logging.info("Correo enviado correctamente ✅")
            
            # Payload de respuesta
            payload_respuesta = {
                "enviado_a": input_vars.NOMBRE_TITULAR,
                "correo_destinatario": destinatario,
                "link_renovacion": input_vars.LINK,
                "status_correo": "Enviado"
            }
            return payload_respuesta

        except Exception as e:
            logging.error(f"Error al enviar correo: {e}", exc_info=True)
            raise Exception(f"Error en el servicio de envío de correo: {e}")

    else:
        logging.info("No se cumplieron las validaciones para enviar el correo.")
        return {"status": "success", "message": "Validaciones no cumplidas, no se envió correo."}