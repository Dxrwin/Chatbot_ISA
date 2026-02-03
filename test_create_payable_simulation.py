#!/usr/bin/env python3
"""
Script de prueba final que simula exactamente el endpoint create_payable
"""
import asyncio
import json
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simular la configuración de BD (igual que en fix_payable_body.sql)
test_config = {
    "nombre_servicio": "KUENTA Payable Create",
    "codigo": "KUENTA_PAYABLE_CREATE",
    "url": "https://api.test.com/payables",
    "metodo": "POST",
    "timeout_ms": 15000,
    "reintentos": 3,
    "header": {
        "Content-Type": "application/json",
        "Config-Organization-ID": "{ORG_ID}",
        "Organization-ID": "{ORG_ID}",
        "Authorization": "{access_token}"
    },
    "body": {
        "creditLineId": "{creditLineId}",
        "principal": "{principal}",
        "time": "{time}",
        "paymentFrequency": "{paymentFrequency}",
        "initialFee": "{initialFee}",
        "disbursementMethod": "{disbursementMethod}",
        "source": "{source}",
        "redirectUrl": "{redirectUrl}",
        "callbackUrl": "{callbackUrl}",
        "meta": "{meta}"
    }
}

class TestExternalClient:
    """Versión simplificada de ExternalClient para pruebas"""

    VAR_PATTERN = r'\{([^}]+)\}'

    def __init__(self, config):
        self.nombre_servicio = config["nombre_servicio"]
        self.codigo = config["codigo"]
        self.url = config["url"]
        self.metodo = config["metodo"]
        self.timeout_ms = config["timeout_ms"]
        self.reintentos = config["reintentos"]
        self.header = config["header"]
        self.body = config["body"]
        self.dynamic_values = {}

    def set_dynamic_values(self, values):
        """Establecer valores dinámicos para reemplazo"""
        self.dynamic_values.update(values or {})
        logger.info(f"[{self.codigo}] Variables dinámicas establecidas: {list(values.keys())}")

    def _resolve_value(self, value):
        """Resolver un valor del body/headers conservando el tipo real"""
        import re
        if isinstance(value, str):
            # ¿Es SOLO un placeholder? ej: "{meta}"
            full_match = re.fullmatch(self.VAR_PATTERN, value)
            if full_match:
                var_name = full_match.group(1)
                if var_name in self.dynamic_values:
                    # Devolvemos el valor como es (dict, None, int, etc)
                    return self.dynamic_values[var_name]
                # Si no está definido en dynamic_values, devolvemos el string tal cual
                return value

            # Si es un string con texto + placeholders mezclados → usamos reemplazo normal
            def replacer(match):
                var_name = match.group(1)
                if var_name in self.dynamic_values:
                    return str(self.dynamic_values[var_name])
                return match.group(0)
            return re.sub(self.VAR_PATTERN, replacer, value)

        # Caso 2: dict
        if isinstance(value, dict):
            return self._process_dict(value)

        # Caso 3: lista
        if isinstance(value, list):
            return [self._resolve_value(item) for item in value]

        # Otros tipos (int, float, bool, None)
        return value

    def _process_dict(self, d):
        """Procesar recursivamente un diccionario reemplazando variables"""
        if not isinstance(d, dict):
            return d

        result = {}
        for key, value in d.items():
            result[key] = self._resolve_value(value)
        return result

    async def run(self):
        """Simular ejecución - solo procesar y mostrar resultados"""
        # Reemplazar variables en URL
        final_url = self._resolve_value(self.url)

        # Reemplazar variables en headers
        final_headers = self._process_dict(self.header)

        # Reemplazar variables en body
        final_body = self._process_dict(self.body) if self.body else {}

        # Logs de depuración
        logger.info(f"[{self.codigo}] URL final: {final_url}")
        logger.info(f"[{self.codigo}] Headers: {final_headers}")
        logger.info(f"[{self.codigo}] Body final: {final_body}")

        # Simular respuesta exitosa
        return {
            "status": 200,
            "data": {"id": "test-payable-id", "status": "created"}
        }

async def test_create_payable_simulation():
    """Simular exactamente lo que hace create_payable"""

    print("🧪 SIMULACIÓN COMPLETA DEL ENDPOINT CREATE_PAYABLE")
    print("=" * 70)

    # Simular payload del request (igual al error reportado)
    class MockPayload:
        def __init__(self):
            self.creditLineId = "864bf2e4-d1ed-4512-909d-216716ab18a1"
            self.time = 150
            self.paymentFrequency = 30
            self.disbursementMethod = ""
            self.source = ""
            self.redirectUrl = ""
            self.callbackUrl = ""
            self.meta = None

    payload = MockPayload()

    # Simular variables calculadas (igual que en logica.py)
    ORG_ID = "test-org-123"
    token = "Bearer test-token-abc123"
    principal = 1500000
    initial_fee = 166666.66666666666

    print("📋 DATOS DE ENTRADA:")
    print(f"  ORG_ID: {ORG_ID}")
    print(f"  token: {token}")
    print(f"  payload.creditLineId: {payload.creditLineId}")
    print(f"  principal: {principal}")
    print(f"  payload.time: {payload.time}")
    print(f"  payload.paymentFrequency: {payload.paymentFrequency}")
    print(f"  initial_fee: {initial_fee}")
    print(f"  payload.disbursementMethod: {payload.disbursementMethod}")
    print(f"  payload.source: {payload.source}")
    print(f"  payload.redirectUrl: {payload.redirectUrl}")
    print(f"  payload.callbackUrl: {payload.callbackUrl}")
    print(f"  payload.meta: {payload.meta}")
    print()

    # Crear cliente con configuración de BD
    ext_client_post = TestExternalClient(test_config)

    print("🔄 PROCESANDO COMO EN LOGICA.PY:")
    print("   1. Creando dynamic_vars...")

    # Esto es exactamente lo que hace logica.py
    dynamic_vars = {
        "ORG_ID": ORG_ID,
        "access_token": token,
        "creditLineId": payload.creditLineId,
        "principal": principal,
        "time": payload.time,
        "paymentFrequency": payload.paymentFrequency,
        "initialFee": initial_fee,
        "disbursementMethod": payload.disbursementMethod,
        "source": payload.source,
        "redirectUrl": payload.redirectUrl,
        "callbackUrl": payload.callbackUrl,
        "meta": payload.meta,
    }

    print(f"   2. dynamic_vars creado: {dynamic_vars}")
    print("   3. Llamando ext_client_post.set_dynamic_values(dynamic_vars)...")

    ext_client_post.set_dynamic_values(dynamic_vars)

    print("   4. Ejecutando ext_client_post.run()...")

    # Ejecutar
    response = await ext_client_post.run()

    print()
    print("🎯 RESULTADO FINAL:")
    print(f"   Response status: {response['status']}")
    print(f"   Response data: {response['data']}")

    print()
    print("✅ CONCLUSIÓN:")
    print("   Si no hay errores en los logs anteriores, el reemplazo funciona correctamente.")
    print("   El problema original (uuid: incorrect UUID length: {creditLineId}) debería estar resuelto.")
    print()
    print("📝 PRÓXIMOS PASOS:")
    print("   1. Aplicar fix_payable_body.sql en la BD si no está aplicado")
    print("   2. Reiniciar la aplicación")
    print("   3. Probar el endpoint create_payable con un payload real")

if __name__ == "__main__":
    asyncio.run(test_create_payable_simulation())