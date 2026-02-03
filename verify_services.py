#!/usr/bin/env python3
"""
Script para verificar la configuración de servicios externos en BD
"""
import asyncio
import json
from utils.database import consultar_servicio_externo

async def verify_services():
    """Verificar configuración de servicios"""

    print("🔍 VERIFICACIÓN DE CONFIGURACIÓN DE SERVICIOS EXTERNOS")
    print("=" * 60)

    services_to_check = [
        "KUENTA_PAYABLE_CREATE",
        "KUENTA_LINES_LIST",
        "KUENTA_TOTP_APPROVE"
    ]

    for service_code in services_to_check:
        print(f"\n📋 Servicio: {service_code}")
        print("-" * 40)

        try:
            service = await consultar_servicio_externo(service_code)
            if not service:
                print(f"❌ Servicio {service_code} no encontrado")
                continue

            print(f"✅ Servicio encontrado: {service['nombre_servicio']}")
            print(f"   URL: {service['url']}")
            print(f"   Método: {service['metodo']}")
            print(f"   Timeout: {service['timeout_ms']}ms")

            # Verificar headers
            headers = service.get('header', {})
            print(f"   Headers ({len(headers)}):")
            for k, v in headers.items():
                print(f"     {k}: {v}")

            # Verificar body
            body = service.get('body', {})
            print(f"   Body ({len(body)} campos):")
            for k, v in body.items():
                print(f"     {k}: {v}")

            # Verificar placeholders en body
            placeholders_found = []
            for k, v in body.items():
                if isinstance(v, str) and '{' in v and '}' in v:
                    placeholders_found.append(f"{k}: {v}")

            if placeholders_found:
                print(f"   ✅ Placeholders encontrados ({len(placeholders_found)}):")
                for ph in placeholders_found:
                    print(f"     {ph}")
            else:
                print("   ⚠️  No se encontraron placeholders en el body")

        except Exception as e:
            print(f"❌ Error verificando {service_code}: {e}")

    print("\n" + "=" * 60)
    print("🎯 INSTRUCCIONES PARA CORRECCIÓN:")
    print("Si algún servicio no tiene placeholders correctos, ejecuta:")
    print("mysql -u root -p < fix_payable_body.sql")

if __name__ == "__main__":
    asyncio.run(verify_services())