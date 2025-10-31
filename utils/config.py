import os
import json
from dotenv import load_dotenv

load_dotenv()

# Cargar variables de entorno desde .env
AUTH_PAYLOAD_PROD_STR = os.getenv("AUTH_PAYLOAD_PROD") or "{}"
AUTH_PAYLOAD_DEMO_STR = os.getenv("AUTH_PAYLOAD_DEMO") or "{}"

try:
    AUTH_PAYLOAD_PROD = json.loads(AUTH_PAYLOAD_PROD_STR)
except Exception:
    AUTH_PAYLOAD_PROD = {}

try:
    AUTH_PAYLOAD_DEMO = json.loads(AUTH_PAYLOAD_DEMO_STR)
except Exception:
    AUTH_PAYLOAD_DEMO = {}

AUTH_URL = os.getenv("AUTH_URL")
API_URL = os.getenv("API_URL")
ORG_ID = os.getenv("ORG_ID")
PAYABLE_URL = os.getenv("PAYABLE_URL")
GET_PAYABLE_URL = os.getenv("GET_PAYABLE_URL")

# Variables globales para el token y su expiración (compartidas)
TOKEN_DATA = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0
}