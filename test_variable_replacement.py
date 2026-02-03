#!/usr/bin/env python3
"""
Script de prueba para verificar el reemplazo de variables dinámicas
en ExternalClient.
"""
import asyncio
import json
from utils.external_client import ExternalClient

async def test_variable_replacement():
    """Prueba el reemplazo de variables dinámicas"""

    print("🧪 PRUEBA DE REEMPLAZO DE VARIABLES DINÁMICAS")
    print("=" * 60)

    # Simular configuración de BD
    test_config = {
        "nombre_servicio": "Test Payable Create",
        "codigo": "TEST_PAYABLE",
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

    # Crear cliente con configuración de prueba
    client = ExternalClient(**test_config)

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
    print(f"Variables inyectadas: {list(test_values.keys())}")
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

    print()
    print("🎯 CONCLUSIÓN:")
    if credit_line_value == test_values['creditLineId'] and meta_value is None:
        print("✅ El reemplazo de variables funciona correctamente")
    else:
        print("❌ Hay problemas con el reemplazo de variables")
        print("   - Revisa la configuración en BD")
        print("   - Verifica que set_dynamic_values() se llama antes de run()")

if __name__ == "__main__":
    asyncio.run(test_variable_replacement())