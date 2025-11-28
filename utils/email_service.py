import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Importa la configuración (settings) y la plantilla (template)
from utils.config import settings
from utils.email_template import get_html_template, get_html_template_webinar

async def enviar_correo_renovacion(destinatario: str, nombre: str, link_renovacion: str, semestre: str, link_asesor: str):
    """
    Construye y envía el correo HTML de renovación.
    """
    asunto = "Renueva tu crédito educativo con One2credit"
    
    # 1. Obtener la plantilla HTML
    html_template = get_html_template()
    
    # 2. Reemplazar los placeholders
    # (Asegúrate de que los placeholders en email_template.py coincidan)
    semestre_texto = f"para el semestre {semestre}" if semestre else "para el próximo semestre"
    
    cuerpo_html = html_template.replace("{{NOMBRE}}", nombre)
    cuerpo_html = cuerpo_html.replace("{{SEMESTRE_INFO}}", semestre_texto)
    cuerpo_html = cuerpo_html.replace("{{LINK_RENOVACION}}", link_renovacion or link_asesor) # Si no hay link, usa el de asesor
    cuerpo_html = cuerpo_html.replace("{{LINK_ASESOR}}", link_asesor)

    # 3. Configurar el mensaje
    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_USER
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo_html, "html"))

    # 4. Enviar el correo
    try:
        # Usar SMTP_SSL si el puerto es 465
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
                msg_return="Correo enviado exitosamente"
                return msg_return
        else:
            with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
                msg_return="Correo enviado exitosamente"
                return msg_return
        
        logging.info(f"Correo enviado a {destinatario}")

    except Exception as e:
        logging.error(f"Error al enviar correo SMTP a {destinatario}: {e}", exc_info=True)
        # Relanza la excepción para que el servicio la capture
        raise Exception(f"Fallo en SMTP: {e}")
    
    
async def enviar_correo_webinar(destinatario: str, nombre: str):
    """
    Construye y envía el correo HTML para invitación a webinar.
    """
    asunto = "Invitación al Webinar de One2credit"
    
    # 1. Obtener la plantilla HTML
    html_template = get_html_template_webinar()
    
    # 2. Reemplazar los placeholders
    cuerpo_html = html_template.replace("{{contact.FIRSTNAME}}", nombre)
    

    # 3. Configurar el mensaje
    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_USER
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo_html, "html"))

    # 4. Enviar el correo
    try:
        # Usar SMTP_SSL si el puerto es 465
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
                msg_return="Correo enviado exitosamente"
                return msg_return
        else:
            with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
                msg_return="Correo enviado exitosamente"
                return msg_return
        
        logging.info(f"Correo enviado a {destinatario}")

    except Exception as e:
        logging.error(f"Error al enviar correo SMTP a {destinatario}: {e}", exc_info=True)
        # Relanza la excepción para que el servicio la capture
        raise Exception(f"Fallo en SMTP: {e}")