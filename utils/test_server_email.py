import os
import asyncio
import aiohttp
import aiosmtplib
from email.mime.text import MIMEText
from fastapi import FastAPI, HTTPException
from datetime import datetime

app = FastAPI()

SMTP_SERVER="smtp.gmail.com"
SMTP_PORT=587
EMAIL_FROM="soportehorizonte0@gmail.com"
EMAIL_PASSWORD="syza qyoi ojfq rzip"
EMAIL_TO="soportehorizonte0@gmail.com"

TELEGRAM_BOT_TOKEN = "8375003473:AAHwtDhxdcNarEoZegK-YoyMNqNuaJvBLNo"
TELEGRAM_CHAT_ID = "6616074415"

# Simula trace_id o identificador de log
def generate_trace_id():
    return f"LOG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

# =======================================================
# FUNCIÓN PARA ENVIAR EMAIL ASINCRÓNICAMENTE
# =======================================================
async def send_log_email(trace_id: str, log_message: str):
    """Envía un log por correo usando SMTP asincrónico."""
    msg = MIMEText(f"Trace ID: {trace_id}\n\nLog Message:\n{log_message}")
    msg["Subject"] = f"[LOG ERROR] {trace_id}"
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
        print(f"✅ Email enviado correctamente ({trace_id})")
        return {"trace_id": trace_id, "status": "enviado", "detail": "Email enviado correctamente", "log_message": log_message}
    except Exception as e:
        print(f"❌ Error al enviar el correo: {e}")

# =======================================================
# FUNCIÓN PARA ENVIAR LOG A TELEGRAM
# =======================================================
async def send_log_telegram(trace_id: str, log_message: str):
    """Envía un mensaje al chat de Telegram con el log."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    message = f"🚨 *Error Log*\n\n*Trace ID:* `{trace_id}`\n\n*Mensaje:* {log_message}"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    print(f"✅ Log enviado a Telegram ({trace_id})")
                else:
                    error_text = await response.text()
                    print(f"❌ Error al enviar a Telegram: {response.status} - {error_text}")
    except Exception as e:
        print(f"❌ Excepción enviando a Telegram: {e}")

# =======================================================
# TEST SMTP Y TELEGRAM ASYNC
# =======================================================
async def test_smtp_connection(trace_id: str):
    """Simula una prueba general de envío de logs (correo + Telegram)."""
    log_message = "Prueba de notificación asincrónica desde FastAPI."
    await send_log_email(trace_id, log_message)
    await send_log_telegram(trace_id, log_message)
    return {"trace_id": trace_id, "status": "ok"}

# =======================================================
# ENDPOINT PARA PROBAR MANUALMENTE
# =======================================================
@app.get("/test-notification")
async def test_notification():
    trace_id = generate_trace_id()
    result = await send_log_email(trace_id, "Prueba de notificación asincrónica desde FastAPI.")
    
    return {"message": "Notificación de prueba enviada correctamente", "result": result}


# ===============================
# OPCIONAL: Test al iniciar app
# ===============================
@app.on_event("startup")
async def startup_event():
    """
    Se ejecuta automáticamente al iniciar la aplicación.
    """
    trace_id = generate_trace_id()
    print(f"[{trace_id}] 🚀 FastAPI iniciando... verificando conexión SMTP...")
    try:
        await send_log_email(trace_id, "Prueba de conexión SMTP al iniciar FastAPI.")
        await send_log_telegram(trace_id, "Prueba de conexión Telegram al iniciar FastAPI.")
    except Exception as e:
        print(f"[{trace_id}] ⚠️ Error inicial SMTP: {e}")