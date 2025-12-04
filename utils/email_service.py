import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiosmtplib

from utils.config import settings
from utils.email_template import get_html_template, get_html_template_webinar


async def _send_async_email(message: MIMEMultipart) -> str:
    """
    Envía el mensaje SMTP con aiosmtplib para no bloquear el event loop de FastAPI.
    """
    use_tls = settings.SMTP_PORT == 465  # SSL puro en 465; STARTTLS en otros puertos
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_SERVER,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            start_tls=not use_tls,
            use_tls=use_tls,
        )
        logging.info(f"Correo enviado a {message['To']}")
        return "Correo enviado exitosamente"
    except Exception as exc:
        logging.error(f"Error al enviar correo SMTP a {message['To']}: {exc}", exc_info=True)
        raise Exception(f"Fallo en SMTP: {exc}")


async def enviar_correo_renovacion(destinatario: str, nombre: str, link_renovacion: str, semestre: str, link_asesor: str):
    """
    Construye y envía el correo HTML de renovación reutilizando la plantilla.
    """
    asunto = "Renueva tu crédito educativo con One2credit"
    
    # Ajustamos placeholders del template con los datos del cliente
    html_template = get_html_template()
    semestre_texto = f"para el semestre {semestre}" if semestre else "para el próximo semestre"
    cuerpo_html = html_template.replace("{{NOMBRE}}", nombre)
    cuerpo_html = cuerpo_html.replace("{{SEMESTRE_INFO}}", semestre_texto)
    cuerpo_html = cuerpo_html.replace("{{LINK_RENOVACION}}", link_renovacion or link_asesor)  # Si no hay link, usa el de asesor
    cuerpo_html = cuerpo_html.replace("{{LINK_ASESOR}}", link_asesor)

    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_USER
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo_html, "html"))

    # Envía usando cliente async para no bloquear el hilo de FastAPI
    return await _send_async_email(msg)
    
    
async def enviar_correo_webinar(destinatario: str, nombre: str):
    """
    Construye y envía el correo HTML para invitación a webinar.
    """
    asunto = "Invitación al Webinar de One2credit"
    
    # Ajustamos placeholders del template con los datos del cliente
    html_template = get_html_template_webinar()
    cuerpo_html = html_template.replace("{{ contact.FIRSTNAME }}", nombre)
    
    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_USER
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo_html, "html"))

    # Envía usando cliente async para no bloquear el hilo de FastAPI
    return await _send_async_email(msg)
