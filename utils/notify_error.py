from datetime import datetime
from fastapi import FastAPI
import aiosmtplib
import html
import asyncio
from email.mime.text import MIMEText
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ======================================================
# CACHE TEMPORAL EN MEMORIA (para inspección manual)
# ======================================================
LOG_CACHE = []
CACHE_LIMIT = 100  # número máximo de logs que se conservarán en memoria


def generate_trace_id():
    """Genera un identificador único para el log."""
    return f"LOG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

def get_formatted_datetime():
    """Devuelve fecha y hora actual en formato dd/mm/yy hr:min"""
    now = datetime.now()
    return now.strftime("%d/%m/%y %H:%M")


# ENVÍO DE EMAIL
async def send_log_email(method_name: str, client_id: str, error_message: str):
    """Envía el log por correo usando SMTP asincrónico"""
    date_time = get_formatted_datetime()

    message_body = (
        f"--- {method_name} ---\n"
        f"--- {date_time} ---\n"
        f"--- {method_name} - {client_id} ---\n\n"
        f"{error_message}"
    )

    msg = MIMEText(message_body)
    msg["Subject"] = f"[ERROR LOG] {method_name}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_SERVER,
            port=SMTP_PORT,
            start_tls=True,
            username=EMAIL_FROM,
            password=EMAIL_PASSWORD,
        )
        return {"status": "success", "sent_to": EMAIL_TO}
    except Exception as e:
        # No llamar a error_notify desde aquí (evita recursión)
        return {"status": "error", "error": str(e)}


async def send_info_email(method_name: str, client_id: str, info_message: str, entity_id: str = None):
    """Envía una notificación informativa por correo (asincrónico)."""
    date_time = get_formatted_datetime()
    body_lines = [
        f"--- {method_name} ---",
        f"--- {date_time} ---",
        f"--- {method_name} - {client_id} ---",
        ""
    ]
    if entity_id:
        body_lines.append(f"ID: {entity_id}")
    body_lines.append(info_message)
    message_body = "\n".join(body_lines)

    msg = MIMEText(message_body)
    msg["Subject"] = f"[INFO] {method_name}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_SERVER,
            port=SMTP_PORT,
            start_tls=True,
            username=EMAIL_FROM,
            password=EMAIL_PASSWORD,
        )
        return {"status": "success", "sent_to": EMAIL_TO}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    


async def send_log_telegram(method_name: str, client_id: str, error_message: str):
    """Envía un log de error al chat de Telegram (texto seguro en HTML <pre>)"""
    date_time = get_formatted_datetime()
    message = (
        f"🚨 LOG DE ERROR\n\n"
        f"Método: {method_name}\n"
        f"Fecha: {date_time}\n"
        f"Cliente: {client_id}\n\n"
        f"Error:\n{error_message}"
    )

    # Escapar HTML y enviar dentro de <pre> para evitar problemas de parseo
    message_html = f"<pre>{html.escape(message)}</pre>"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message_html, "parse_mode": "HTML"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return {"status": "success"}
                else:
                    error_text = await response.text()
                    return {"status": "error", "error": error_text}
    except Exception as e:
        # No llamar a error_notify desde aquí (evita recursión)
        return {"status": "error", "error": str(e)}


async def send_info_telegram(method_name: str, client_id: str, info_message: str, entity_id: str = None):
    """Envía una notificación informativa a Telegram."""
    date_time = get_formatted_datetime()
    header = "✅ NOTIFICACIÓN INFORMATIVA"
    message_lines = [
        header,
        f"Método: {method_name}",
        f"Fecha: {date_time}",
        f"Cliente: {client_id}",
    ]
    if entity_id:
        message_lines.append(f"ID: {entity_id}")
    message_lines.append("")
    message_lines.append(info_message)

    message = "\n".join(message_lines)
    message_html = f"<pre>{html.escape(message)}</pre>"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message_html, "parse_mode": "HTML"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return {"status": "success"}
                else:
                    error_text = await response.text()
                    return {"status": "error", "error": error_text}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    


# FUNCIÓN CENTRAL DE NOTIFICACIÓN DE ERRORES
async def error_notify(method_name: str, client_id: str, error_message: str):
    """
    Función central que envía el log a correo y Telegram de forma asíncrona.
    Retorna la combinación de resultados de ambos canales.
    """
    #if trace_id is None:
        #trace_id = f"TRACE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    # Añadimos traceback si se desea (opcional)
    # tb = traceback.format_exc()
    # if "NoneType: None" not in tb and tb.strip():
    #     # si hay traceback que no sea vacío, lo agregamos
    #     error_message = f"{error_message}\n\nTraceback:\n{tb}"

    # Ejecutar envíos en paralelo
    
    results = await asyncio.gather(
        # send_log_email(method_name, client_id, error_message),
        send_log_telegram(method_name, client_id, error_message),
        return_exceptions=True
    )

    # Normalizar excepciones si las hay
    normalized = []
    for r in results:
        if isinstance(r, Exception):
            normalized.append({"status": "error", "error": str(r)})
        else:
            normalized.append(r)
            
        # Guardar en cache
    _save_to_cache({
        "timestamp": get_formatted_datetime(),
        "method": method_name,
        "client_id": client_id,
        "message": error_message[:250] + ("..." if len(error_message) > 250 else ""),
        "results": normalized,
    })

    return {"results": normalized}

async def info_notify(method_name: str, client_id: str, info_message: str, entity_id: str = None):
    """
    Envía notificación informativa por email y telegram en paralelo.
    Guarda registro en cache con tipo "info".
    """
    results = await asyncio.gather(
        # send_info_email(method_name, client_id, info_message, entity_id),
        send_info_telegram(method_name, client_id, info_message, entity_id),
        return_exceptions=True
    )

    normalized = []
    for r in results:
        if isinstance(r, Exception):
            normalized.append({"status": "error", "error": str(r)})
        else:
            normalized.append(r)

    # Guardar en cache como informativo
    _save_to_cache({
        "timestamp": get_formatted_datetime(),
        "type": "info",
        "method": method_name,
        "client_id": client_id,
        "entity_id": entity_id,
        "message": info_message[:250] + ("..." if len(info_message) > 250 else ""),
        "results": normalized,
    })

    return {"results": normalized}



# ======================================================
# GESTIÓN DE CACHE
# ======================================================
def _save_to_cache(log_entry: dict):
    """Guarda un log en memoria con un límite circular."""
    LOG_CACHE.append(log_entry)
    if len(LOG_CACHE) > CACHE_LIMIT:
        LOG_CACHE.pop(0)

async def get_cached_logs(limit: int = 50):
    """Devuelve los últimos logs almacenados (async para poder usar await)."""
    # devolver copia para evitar mutaciones externas
    return LOG_CACHE[-limit:].copy()