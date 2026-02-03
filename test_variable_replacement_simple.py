#!/usr/bin/env python3
"""
Script de prueba simplificado para verificar el reemplazo de variables dinámicas
sin dependencias de BD.
"""
import json
import re

class TestExternalClient:
    """Versión simplificada de ExternalClient para pruebas"""

    VAR_PATTERN = re.compile(r'\{([^}]+)\}')

    def __init__(self, header=None, body=None):
        self.header = header or {}
        self.body = body or {}
        self.dynamic_values = {}

    def set_dynamic_values(self, values):
        """Establecer valores dinámicos para reemplazo"""
        self.dynamic_values = values
        print(f"🔄 Variables dinámicas establecidas: {list(values.keys())}")

    def _resolve_value(self, value):
        """Resolver un valor individual"""
        if isinstance(value, str):
            def replace_match(match):
                var_name = match.group(1)
                if var_name in self.dynamic_values:
                    return str(self.dynamic_values[var_name])
                return match.group(0)  # Mantener sin cambios si no se encuentra
            return self.VAR_PATTERN.sub(replace_match, value)
        return value

    def _process_dict(self, data):
        """Procesar diccionario recursivamente"""
        if isinstance(data, dict):
            return {k: self._process_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._process_dict(item) for item in data]
        else:
            return self._resolve_value(data)

async def test_variable_replacement():
    """Prueba el reemplazo de variables dinámicas"""

    print("🧪 PRUEBA DE REEMPLAZO DE VARIABLES DINÁMICAS")
    print("=" * 60)

    # Configuración de prueba (igual a la de BD)
    test_header = {
        "Content-Type": "application/json",
        "Config-Organization-ID": "{ORG_ID}",
        "Organization-ID": "{ORG_ID}",
        "Authorization": "{access_token}"
    }

    test_body = {
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

    # Crear cliente de prueba
    client = TestExternalClient(header=test_header, body=test_body)

    # Valores de prueba (iguales al payload del error)
    test_values = {
        "ORG_ID": "test-org-123",
        "access_token": "Bearer test-token-abc123",
        "creditLineId": "864bf2e4-d1ed-4512-909d-216716ab18a1",
        "principal": 1500000,
        "time": 150,
        "paymentFrequency": 30,
        "initialFee": 166666.66666666666,
        "disbursementMethod": "",
        "source": "",
        "redirectUrl": "",
        "callbackUrl": "",
        "meta": None
    }

    print("📋 Configuración original:")
    print(f"Headers: {json.dumps(client.header, indent=2)}")
    print(f"Body: {json.dumps(client.body, indent=2)}")
    print()

    # Inyectar variables dinámicas
    print("🔄 Inyectando variables dinámicas...")
    client.set_dynamic_values(test_values)
    print()

    # Verificar procesamiento
    print("🔍 Resultado del procesamiento:")

    # Headers procesados
    processed_headers = client._process_dict(client.header)
    print(f"Headers procesados: {json.dumps(processed_headers, indent=2)}")

    # Body procesado
    processed_body = client._process_dict(client.body)
    print(f"Body procesado: {json.dumps(processed_body, indent=2, default=str)}")

    # Verificar que creditLineId se reemplazó
    credit_line_value = processed_body.get('creditLineId')
    if credit_line_value == test_values['creditLineId']:
        print("✅ SUCCESS: creditLineId se reemplazó correctamente")
    else:
        print(f"❌ ERROR: creditLineId no se reemplazó. Valor actual: {credit_line_value}")

    # Verificar que meta se reemplazó correctamente (debe ser None)
    meta_value = processed_body.get('meta')
    if meta_value is None:
        print("✅ SUCCESS: meta se reemplazó correctamente (None)")
    else:
        print(f"❌ ERROR: meta no se reemplazó correctamente. Valor actual: {meta_value}")

    # Verificar otros campos
    org_id_header = processed_headers.get('Organization-ID')
    if org_id_header == test_values['ORG_ID']:
        print("✅ SUCCESS: Organization-ID en header se reemplazó correctamente")
    else:
        print(f"❌ ERROR: Organization-ID en header no se reemplazó. Valor actual: {org_id_header}")

    auth_header = processed_headers.get('Authorization')
    if auth_header == test_values['access_token']:
        print("✅ SUCCESS: Authorization header se reemplazó correctamente")
    else:
        print(f"❌ ERROR: Authorization header no se reemplazó. Valor actual: {auth_header}")

    print()
    print("🎯 CONCLUSIÓN:")
    success_count = 0
    if credit_line_value == test_values['creditLineId']: success_count += 1
    if meta_value is None: success_count += 1
    if org_id_header == test_values['ORG_ID']: success_count += 1
    if auth_header == test_values['access_token']: success_count += 1

    if success_count == 4:
        print("✅ El reemplazo de variables funciona correctamente")
        print("   El problema debe estar en la configuración de BD o en la llamada a set_dynamic_values()")
    else:
        print(f"❌ Hay problemas con el reemplazo de variables ({success_count}/4 exitosos)")
        print("   Revisa la implementación de _process_dict y _resolve_value")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_variable_replacement())