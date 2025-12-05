import logging
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from utils.config import settings
from utils.email_template import get_html_template, get_html_template_webinar
from utils.notify_error import error_notify


async def _send_async_email(message: MIMEMultipart, destinatario: str = None) -> dict:
    """
    Envía correo SMTP con 2 intentos y backoff.
    
    Retorna dict:
    {
        "status": "success" | "error",
        "message": "descripción",
        "destinatario": "email",
        "intentos": número
    }
    
    NUNCA lanza excepción. Notifica error y detiene tras 2 intentos fallidos.
    """
    destinatario_real = destinatario or message.get('To', 'desconocido')
    
    if not destinatario_real or '@' not in destinatario_real:
        logging.error(f"Correo destino invalido. Formato: {destinatario_real}")
        return {
            "status": "error",
            "message": f"Correo invalido: {destinatario_real}",
            "destinatario": destinatario_real,
            "intentos": 0
        }
    
    use_tls = settings.SMTP_PORT == 465
    timeout_seconds = 20
    max_intentos = 2
    backoff_seconds = 3
    last_error = None
    
    logging.info(f"Iniciando envio de correo a: {destinatario_real}")
    logging.info(f"Servidor SMTP: {settings.SMTP_SERVER}:{settings.SMTP_PORT}")
    logging.info(f"Modo conexion: SSL (465)" if use_tls else "STARTTLS (587)")
    logging.info(f"Timeout: {timeout_seconds}s. Reintentos: {max_intentos}")

    for intento in range(1, max_intentos + 1):
        try:
            logging.info(f"Intento {intento}/{max_intentos}: Conectando a servidor SMTP...")
            
            async with aiosmtplib.SMTP(
                hostname=settings.SMTP_SERVER,
                port=settings.SMTP_PORT,
                timeout=timeout_seconds,
                use_tls=use_tls,
            ) as smtp:
                logging.info(f"Intento {intento}/{max_intentos}: Conectado exitosamente")
                
                if settings.SMTP_PORT == 587:
                    logging.info(f"Intento {intento}/{max_intentos}: Ejecutando STARTTLS...")
                    await smtp.starttls()
                    logging.info(f"Intento {intento}/{max_intentos}: STARTTLS completado")
                
                logging.info(f"Intento {intento}/{max_intentos}: Autenticando con usuario: {settings.SMTP_USER}")
                await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
                logging.info(f"Intento {intento}/{max_intentos}: Autenticacion exitosa")
                
                logging.info(f"Intento {intento}/{max_intentos}: Enviando mensaje...")
                await smtp.send_message(message)
                logging.info(f"Intento {intento}/{max_intentos}: Mensaje enviado")

            logging.info(f"EXITO: Correo enviado a {destinatario_real} en intento {intento}")
            return {
                "status": "success",
                "message": "Correo enviado exitosamente",
                "destinatario": destinatario_real,
                "intentos": intento
            }

        except asyncio.TimeoutError as e:
            last_error = e
            logging.warning(f"Intento {intento}/{max_intentos}: TIMEOUT - Conexion agotada. {str(e)[:80]}")
            if intento < max_intentos:
                logging.info(f"Esperando {backoff_seconds}s antes del siguiente intento...")
                await asyncio.sleep(backoff_seconds)

        except aiosmtplib.SMTPConnectTimeoutError as e:
            last_error = e
            logging.warning(f"Intento {intento}/{max_intentos}: CONNECT TIMEOUT - Servidor no responde en {timeout_seconds}s")
            logging.warning(f"Servidor: {settings.SMTP_SERVER}:{settings.SMTP_PORT}")
            if intento < max_intentos:
                logging.info(f"Esperando {backoff_seconds}s antes del siguiente intento...")
                await asyncio.sleep(backoff_seconds)

        except aiosmtplib.SMTPReadTimeoutError as e:
            last_error = e
            logging.warning(f"Intento {intento}/{max_intentos}: READ TIMEOUT - Servidor tardo en responder")
            if intento < max_intentos:
                logging.info(f"Esperando {backoff_seconds}s antes del siguiente intento...")
                await asyncio.sleep(backoff_seconds)

        except aiosmtplib.SMTPAuthenticationError as e:
            last_error = e
            logging.error(f"Intento {intento}/{max_intentos}: ERROR DE AUTENTICACION")
            logging.error(f"Usuario: {settings.SMTP_USER}")
            logging.error(f"Servidor: {settings.SMTP_SERVER}:{settings.SMTP_PORT}")
            logging.error(f"Detalle: {str(e)[:100]}")
            break

        except aiosmtplib.SMTPRecipientsRefused as e:
            last_error = e
            logging.error(f"Intento {intento}/{max_intentos}: DESTINATARIO RECHAZADO - {destinatario_real}")
            logging.error(f"Detalle: {str(e)[:100]}")
            break

        except Exception as e:
            last_error = e
            logging.error(f"Intento {intento}/{max_intentos}: ERROR INESPERADO - {type(e).__name__}: {str(e)[:100]}")
            if intento < max_intentos:
                logging.info(f"Esperando {backoff_seconds}s antes del siguiente intento...")
                await asyncio.sleep(backoff_seconds)

    # TRAS AGOTAR REINTENTOS - DETENER Y NOTIFICAR
    error_type = type(last_error).__name__ if last_error else "UnknownError"
    error_msg = str(last_error)[:200] if last_error else "Error desconocido"
    
    logging.error(f"FALLO DEFINITIVO: No se pudo enviar correo a {destinatario_real} tras {max_intentos} intentos")
    logging.error(f"Tipo de error: {error_type}")
    logging.error(f"Mensaje de error: {error_msg}")
    logging.error(f"Accion requerida: Revisar configuracion SMTP o contactar al proveedor del servidor de correo")

    mensaje_error = (
        f"Fallo en envio de correo tras {max_intentos} intentos agotados.\n"
        f"Destinatario: {destinatario_real}\n"
        f"Servidor: {settings.SMTP_SERVER}:{settings.SMTP_PORT}\n"
        f"Usuario: {settings.SMTP_USER}\n"
        f"Tipo de error: {error_type}\n"
        f"Detalle: {error_msg}\n"
        f"Accion: Revisar configuracion SMTP, verificar credenciales y conectividad del servidor."
    )

    try:
        asyncio.create_task(
            error_notify(
                method_name="enviar_correo",
                client_id=destinatario_real,
                error_message=mensaje_error
            )
        )
        logging.info(f"Notificacion de error encolada para {destinatario_real}")
    except Exception as e:
        logging.error(f"Error al encolar notificacion: {e}")

    return {
        "status": "error",
        "message": f"No se pudo enviar correo tras {max_intentos} intentos. Error: {error_type}",
        "destinatario": destinatario_real,
        "intentos": max_intentos
    }


async def enviar_correo_renovacion(destinatario: str, nombre: str, link_renovacion: str, semestre: str, link_asesor: str) -> dict:
    """
    Construye y envía el correo HTML de renovación.
    Retorna dict con status success/error.
    """
    try:
        asunto = "Renueva tu crédito educativo con One2credit"

        html_template = get_html_template()
        semestre_texto = f"para el semestre {semestre}" if semestre else "para el próximo semestre"
        cuerpo_html = html_template.replace("{{NOMBRE}}", nombre)
        cuerpo_html = cuerpo_html.replace("{{SEMESTRE_INFO}}", semestre_texto)
        cuerpo_html = cuerpo_html.replace("{{LINK_RENOVACION}}", link_renovacion or link_asesor)
        cuerpo_html = cuerpo_html.replace("{{LINK_ASESOR}}", link_asesor)

        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_USER
        msg["To"] = destinatario
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo_html, "html"))

        return await _send_async_email(msg, destinatario)
    
    except Exception as e:
        logging.error(f"Error construyendo correo de renovacion: {e}")
        return {
            "status": "error",
            "message": f"Error al construir correo: {str(e)}",
            "destinatario": destinatario,
            "intentos": 0
        }


async def enviar_correo_webinar(destinatario: str, nombre: str) -> dict:
    """
    Construye y envía el correo HTML para invitación a webinar.
    Retorna dict con status success/error.
    """
    try:
        asunto = "Invitación al Webinar de One2credit"

        html_template = get_html_template_webinar()
        cuerpo_html = html_template.replace("{{ contact.FIRSTNAME }}", nombre)

        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_USER
        msg["To"] = destinatario
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo_html, "html"))

        return await _send_async_email(msg, destinatario)
    
    except Exception as e:
        logging.error(f"Error construyendo correo de webinar: {e}")
        return {
            "status": "error",
            "message": f"Error al construir correo: {str(e)}",
            "destinatario": destinatario,
            "intentos": 0
        }
