
import asyncio
import json
from utils.external_client import ExternalClient
from utils.config import settings

async def test_servicios_externos():
    """
    Función de prueba para verificar que los servicios externos funcionan correctamente
    con las variables dinámicas.
    """
    print("🧪 PRUEBA DE SERVICIOS EXTERNOS CON VARIABLES DINÁMICAS")
    print("=" * 60)

    # Valores de prueba (simular valores reales)
    test_values = {
        "ORG_ID": "test-org-123",
        "access_token": "Bearer test-token-abc123",
        "credit_id": "test-credit-456",
        "codigo_totp": "123456",
        "linea_producto": "test-product-789",
        "creditLineId": "test-line-101",
        "principal": "2500000",
        "time": 12,
        "paymentFrequency": 30,
        "initialFee": "125000",
        "disbursementMethod": "bank_transfer",
        "source": "web",
        "redirectUrl": "https://example.com/redirect",
        "callbackUrl": "https://example.com/callback",
        "meta": {"test": True}
    }

    # Lista de servicios a probar
    servicios_prueba = [
        ("AUTH_TOKEN", {}),
        ("KUENTA_LINES_LIST", {"ORG_ID", "access_token"}),
        ("KUENTA_TOTP_APPROVE", {"ORG_ID", "access_token", "codigo_totp"}),
        ("KUENTA_ORDER_STATUS", {"ORG_ID", "access_token"}),
        ("KUENTA_PAYABLE_GET", {"ORG_ID", "access_token"}),
        ("KUENTA_PAYABLE_CREATE", {"ORG_ID", "access_token", "creditLineId", "principal", "time", "paymentFrequency", "initialFee", "disbursementMethod", "source", "redirectUrl", "callbackUrl", "meta"}),
        ("KUENTA_PAYABLE_CONFIRM", {"ORG_ID", "access_token", "credit_id"}),
        ("KUENTA_PRODUCT_GET", {"ORG_ID", "access_token", "linea_producto"})
    ]

    for codigo, variables_requeridas in servicios_prueba:
        print(f"\n🔍 Probando servicio: {codigo}")
        print("-" * 40)

        try:
            # Crear cliente externo
            client = await ExternalClient.from_code(codigo, client_id="test-client")

            # Preparar variables dinámicas
            dynamic_vars = {k: v for k, v in test_values.items() if k in variables_requeridas}

            # Inyectar variables
            client.set_dynamic_values(dynamic_vars)

            print(f"✅ Servicio cargado: {client.nombre_servicio}")
            print(f"📍 URL: {client.url}")
            print(f"🔧 Método: {client.metodo}")
            print(f"⏱️  Timeout: {client.timeout_ms}ms")
            print(f"🔄 Reintentos: {client.reintentos}")
            print(f"📊 Headers configurados: {len(client.header)} campos")
            print(f"📦 Body configurado: {len(client.body)} campos")
            print(f"🔑 Variables dinámicas: {list(dynamic_vars.keys())}")

            # Mostrar headers y body procesados (sin ejecutar realmente)
            processed_headers = client._process_dict(client.header)
            processed_body = client._process_dict(client.body)

            print(f"📋 Headers procesados: {json.dumps(processed_headers, indent=2)}")
            if processed_body:
                print(f"📋 Body procesado: {json.dumps(processed_body, indent=2)}")

            print("✅ Configuración correcta")

        except ValueError as e:
            print(f"❌ Error: Servicio '{codigo}' no encontrado en BD: {e}")
        except Exception as e:
            print(f"❌ Error inesperado en '{codigo}': {e}")

    print("\n" + "=" * 60)
    print("🎉 PRUEBA COMPLETADA")
    print("Si todos los servicios muestran '✅ Configuración correcta',")
    print("entonces las variables dinámicas están funcionando correctamente.")

if __name__ == "__main__":
    asyncio.run(test_servicios_externos())