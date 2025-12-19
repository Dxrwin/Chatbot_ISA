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
        
        return {"status": "success","message":"Correo enviado exitosamente"}
    except Exception as exc:
        logging.error(f"Error al enviar correo SMTP a {message['To']}: {exc}", exc_info=True)
        raise Exception(f"Fallo en SMTP: {exc}")


async def enviar_correo_renovacion(destinatario: str, nombre: str, semestre: str, link_asesor: str):
    """
    Construye y envía el correo HTML de renovación reutilizando la plantilla.
    """
    asunto = "Renueva tu crédito educativo con One2credit"
    
    # Ajustamos placeholders del template con los datos del cliente
    html_template = get_html_template()
    semestre_texto = f"para el semestre {semestre}" if semestre else "para el próximo semestre"
    cuerpo_html = html_template.replace("{{NOMBRE}}", nombre)
    cuerpo_html = cuerpo_html.replace("{{SEMESTRE_INFO}}", semestre_texto)
    #cuerpo_html = cuerpo_html.replace("{{LINK_RENOVACION}}", link_renovacion or link_asesor)  # Si no hay link, usa el de asesor
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






# import asyncio
# import logging
# import smtplib
# import ssl
# from concurrent.futures import ThreadPoolExecutor
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
# from functools import partial

# from utils.config import settings
# from utils.email_template import get_html_template, get_html_template_webinar
# from utils.notify_error import error_notify

# # Pool de hilos para ejecutar las operaciones SMTP sin bloquear el event loop
# SMTP_THREAD_POOL = ThreadPoolExecutor(max_workers=5)


# def _send_email_sync(message: MIMEMultipart, destinatario_real: str, timeout_seconds: int) -> dict:
#     """
#     Envia el correo de manera sincrona usando smtplib.
#     Esta funcion se corre dentro de un thread del pool configurado arriba.
#     """
#     use_tls = settings.SMTP_PORT == 465
#     context = ssl.create_default_context()

#     logging.info("Conectando a servidor SMTP %s:%s", settings.SMTP_SERVER, settings.SMTP_PORT)
#     if use_tls:
#         smtp_client = smtplib.SMTP_SSL(
#             host=settings.SMTP_SERVER,
#             port=settings.SMTP_PORT,
#             timeout=timeout_seconds,
#             context=context,
#         )
#     else:
#         smtp_client = smtplib.SMTP(
#             host=settings.SMTP_SERVER,
#             port=settings.SMTP_PORT,
#             timeout=timeout_seconds,
#         )

#     with smtp_client as smtp:
#         smtp.ehlo()
#         if not use_tls:
#             logging.info("Ejecutando STARTTLS para la conexion SMTP")
#             smtp.starttls(context=context)
#             smtp.ehlo()

#         logging.info("Autenticando contra SMTP como %s", settings.SMTP_USER)
#         smtp.login(settings.SMTP_USER, settings.SMTP_PASS)

#         logging.info("Enviando mensaje SMTP a %s", destinatario_real)
#         smtp.send_message(message)

#     logging.info("Envio SMTP completado para %s", destinatario_real)
#     return {
#         "status": "success",
#         "message": "Correo enviado exitosamente",
#         "destinatario": destinatario_real,
#         "intentos": 1,
#     }


# async def _send_async_email(message: MIMEMultipart, destinatario: str | None = None) -> dict:
#     """
#     Wrapper asincrono que valida el destinatario y delega el envio SMTP
#     a un hilo del pool para evitar bloquear el event loop.
#     """
#     destinatario_real = destinatario or message.get("To", "desconocido")
#     if not destinatario_real or "@" not in destinatario_real:
#         logging.error("Correo destino invalido. Formato recibido: %s", destinatario_real)
#         return {
#             "status": "error",
#             "message": f"Correo invalido: {destinatario_real}",
#             "destinatario": destinatario_real,
#             "intentos": 0,
#         }

#     timeout_seconds = 15
#     loop = asyncio.get_running_loop()

#     error_type = "UnknownError"
#     error_msg = "Error desconocido"

#     try:
#         send_callable = partial(_send_email_sync, message, destinatario_real, timeout_seconds)
#         return await loop.run_in_executor(SMTP_THREAD_POOL, send_callable)
#     except smtplib.SMTPAuthenticationError as exc:
#         logging.error("Error de autenticacion SMTP para %s: %s", destinatario_real, exc)
#         error_type = type(exc).__name__
#         error_msg = str(exc)[:200]
#     except smtplib.SMTPRecipientsRefused as exc:
#         logging.error("Destinatario rechazado (%s): %s", destinatario_real, exc)
#         error_type = type(exc).__name__
#         error_msg = str(exc)[:200]
#     except (
#         smtplib.SMTPConnectError,
#         smtplib.SMTPDataError,
#         smtplib.SMTPServerDisconnected,
#         smtplib.SMTPException,
#     ) as exc:
#         logging.error("Error SMTP para %s: %s", destinatario_real, exc)
#         error_type = type(exc).__name__
#         error_msg = str(exc)[:200]
#     except Exception as exc:
#         logging.error("Error inesperado enviando correo a %s: %s", destinatario_real, exc)
#         error_type = type(exc).__name__
#         error_msg = str(exc)[:200]

#     mensaje_error = (
#         "Envio SMTP fallido.\n"
#         f"Destinatario: {destinatario_real}\n"
#         f"Servidor: {settings.SMTP_SERVER}:{settings.SMTP_PORT}\n"
#         f"Usuario: {settings.SMTP_USER}\n"
#         f"Tipo de error: {error_type}\n"
#         f"Detalle: {error_msg}\n"
#         "Accion: Revisar configuracion SMTP, verificar credenciales y conectividad del servidor."
#     )

#     try:
#         asyncio.create_task(
#             error_notify(
#                 method_name="enviar_correo",
#                 client_id=destinatario_real,
#                 error_message=mensaje_error,
#             )
#         )
#         logging.info("Notificacion de error encolada para %s", destinatario_real)
#     except Exception as exc:
#         logging.error("Error al encolar notificacion de fallo SMTP: %s", exc)

#     # return {
#     #     "status": "error",
#     #     "message": f"No se pudo enviar correo. Error: {error_type}",
#     #     "destinatario": destinatario_real,
#     #     "intentos": 1,
#     # }


# async def enviar_correo_renovacion(destinatario: str, nombre: str, semestre: str, link_asesor: str) -> dict:
#     """
#     Construye y envia el correo HTML de renovacion.
#     Retorna dict con status success/error.
#     """
#     try:
#         asunto = "Renueva tu crédito educativo con One2credit"

#         html_template = get_html_template()
#         semestre_texto = f"para el semestre {semestre}" if semestre else "para el próximo semestre"
#         cuerpo_html = html_template.replace("{{NOMBRE}}", nombre)
#         cuerpo_html = cuerpo_html.replace("{{SEMESTRE_INFO}}", semestre_texto)
#         cuerpo_html = cuerpo_html.replace("{{LINK_RENOVACION}}", link_asesor)
#         cuerpo_html = cuerpo_html.replace("{{LINK_ASESOR}}", link_asesor)

#         msg = MIMEMultipart("alternative")
#         msg["From"] = settings.SMTP_USER
#         msg["To"] = destinatario
#         msg["Subject"] = asunto
#         msg.attach(MIMEText(cuerpo_html, "html"))

#         return await _send_async_email(msg, destinatario)

#     except Exception as exc:
#         logging.error("Error construyendo correo de renovacion: %s", exc)
#         return {
#             "status": "error",
#             "message": f"Error al construir correo: {exc}",
#             "destinatario": destinatario,
#             "intentos": 0,
#         }


# async def enviar_correo_webinar(destinatario: str, nombre: str) -> dict:
#     """
#     Construye y envia el correo HTML para invitacion a webinar.
#     Retorna dict con status success/error.
#     """
#     try:
#         asunto = "Invitación al Webinar de One2credit"

#         html_template = get_html_template_webinar()
#         cuerpo_html = html_template.replace("{{ contact.FIRSTNAME }}", nombre)

#         msg = MIMEMultipart("alternative")
#         msg["From"] = settings.SMTP_USER
#         msg["To"] = destinatario
#         msg["Subject"] = asunto
#         msg.attach(MIMEText(cuerpo_html, "html"))

#         return await _send_async_email(msg, destinatario)

#     except Exception as exc:
#         logging.error("Error construyendo correo de webinar: %s", exc)
#         return {
#             "status": "error",
#             "message": f"Error al construir correo: {exc}",
#             "destinatario": destinatario,
#             "intentos": 0,
#         }
