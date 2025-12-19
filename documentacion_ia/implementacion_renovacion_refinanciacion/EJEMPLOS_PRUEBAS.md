# Ejemplos de Prueba para procesar_llamada_renovacionYrefinanciamiento

Este archivo contiene ejemplos de payloads para cada uno de los 7 casos y cómo probarlos.

## Importaciones necesarias

```python
import asyncio
import json
from models.models import WebhookPayload
from utils.enviar_correo_IA import procesar_llamada_renovacionYrefinanciamiento
```

---

## CASO 1: Renovación + Correo + Webhook

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "darwin andres pacheco",
    "CORREO": "darwinandres901@gmail.com",
    "Celular": "3002613153",
    "LINEA_CREDITO": "impulsa unicosta",
    "SEMESTRE": "2026-1"
  },
  "extracted_variables": {
    "renovacion": "Si",
    "refinanciar": "No",
    "refinanciar_bool": false,
    "acpt_info_email": true,
    "aceptoinfocorreo": "Si",
    "agendo_asst_assr": "No",
    "asst_assr_bool": false,
    "fecha_asst_assor": null,
    "desicion_correo": true,
    "correo_cliente": null,
    "primer_name": "Darwin",
    "contesto_llamada": true,
    "estado": null,
    "comentario_libre": "Cliente interesado en renovar"
  }
}
```

**Código de prueba:**

```python
async def test_caso_1():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "darwin andres pacheco",
            "CORREO": "darwinandres901@gmail.com",
            "Celular": "3002613153",
            "LINEA_CREDITO": "impulsa unicosta",
            "SEMESTRE": "2026-1"
        },
        extracted_variables={
            "renovacion": "Si",
            "refinanciar": "No",
            "refinanciar_bool": False,
            "acpt_info_email": True,
            "aceptoinfocorreo": "Si",
            "agendo_asst_assr": "No",
            "asst_assr_bool": False,
            "fecha_asst_assor": None,
            "desicion_correo": True,
            "correo_cliente": None,
            "primer_name": "Darwin",
            "contesto_llamada": True,
            "estado": None,
            "comentario_libre": "Cliente interesado en renovar"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 1 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - correo_renovacion en acciones_ejecutadas
    # - webhook_renovacion en acciones_ejecutadas
    # - status: "success"

# Ejecutar
asyncio.run(test_caso_1())
```

---

## CASO 2: Sin Renovación, Solo Correo

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "juan perez",
    "CORREO": "juan@example.com",
    "Celular": "3001234567",
    "LINEA_CREDITO": "impulsa universitaria"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "No",
    "refinanciar_bool": false,
    "acpt_info_email": true,
    "aceptoinfocorreo": "Si",
    "agendo_asst_assr": "No",
    "asst_assr_bool": false,
    "fecha_asst_assor": null,
    "desicion_correo": true,
    "correo_cliente": null,
    "primer_name": "Juan"
  }
}
```

**Código de prueba:**

```python
async def test_caso_2():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "juan perez",
            "CORREO": "juan@example.com",
            "Celular": "3001234567",
            "LINEA_CREDITO": "impulsa universitaria"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "No",
            "refinanciar_bool": False,
            "acpt_info_email": True,
            "aceptoinfocorreo": "Si",
            "agendo_asst_assr": "No",
            "asst_assr_bool": False,
            "desicion_correo": True,
            "correo_cliente": None,
            "primer_name": "Juan"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 2 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - correo_informativo en acciones_ejecutadas
    # - NO webhook
    # - status: "success"

asyncio.run(test_caso_2())
```

---

## CASO 3: Refinanciamiento Básico

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "maria garcia",
    "CORREO": "maria@example.com",
    "Celular": "3009876543",
    "LINEA_CREDITO": "impulsa premium"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "Si",
    "refinanciar_bool": true,
    "acpt_info_email": false,
    "aceptoinfocorreo": "No",
    "agendo_asst_assr": "No",
    "asst_assr_bool": false,
    "fecha_asst_assor": null,
    "desicion_correo": false,
    "correo_cliente": null,
    "primer_name": "Maria"
  }
}
```

**Código de prueba:**

```python
async def test_caso_3():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "maria garcia",
            "CORREO": "maria@example.com",
            "Celular": "3009876543",
            "LINEA_CREDITO": "impulsa premium"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "Si",
            "refinanciar_bool": True,
            "acpt_info_email": False,
            "aceptoinfocorreo": "No",
            "agendo_asst_assr": "No",
            "asst_assr_bool": False,
            "desicion_correo": False,
            "correo_cliente": None,
            "primer_name": "Maria"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 3 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - webhook_refinanciamiento en acciones_ejecutadas
    # - NO correo
    # - status: "success"

asyncio.run(test_caso_3())
```

---

## CASO 4: Refinanciamiento con Asesoría

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "carlos lopez",
    "CORREO": "carlos@example.com",
    "Celular": "3005555555",
    "LINEA_CREDITO": "impulsa plus"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "Si",
    "refinanciar_bool": true,
    "acpt_info_email": false,
    "aceptoinfocorreo": "No",
    "agendo_asst_assr": "Si",
    "asst_assr_bool": true,
    "fecha_asst_assor": null,
    "desicion_correo": false,
    "correo_cliente": null,
    "primer_name": "Carlos"
  }
}
```

**Código de prueba:**

```python
async def test_caso_4():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "carlos lopez",
            "CORREO": "carlos@example.com",
            "Celular": "3005555555",
            "LINEA_CREDITO": "impulsa plus"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "Si",
            "refinanciar_bool": True,
            "acpt_info_email": False,
            "aceptoinfocorreo": "No",
            "agendo_asst_assr": "Si",
            "asst_assr_bool": True,
            "desicion_correo": False,
            "correo_cliente": None,
            "primer_name": "Carlos"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 4 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - webhook_refinanciamiento_asesoria en acciones_ejecutadas
    # - status: "success"

asyncio.run(test_caso_4())
```

---

## CASO 5: Refinanciamiento con Fecha de Asesoría

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "ana martinez",
    "CORREO": "ana@example.com",
    "Celular": "3003333333",
    "LINEA_CREDITO": "impulsa flex"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "Si",
    "refinanciar_bool": true,
    "acpt_info_email": false,
    "aceptoinfocorreo": "No",
    "agendo_asst_assr": "Si",
    "asst_assr_bool": true,
    "fecha_asst_assor": "22/12/2025",
    "desicion_correo": false,
    "correo_cliente": null,
    "primer_name": "Ana"
  }
}
```

**Código de prueba:**

```python
async def test_caso_5():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "ana martinez",
            "CORREO": "ana@example.com",
            "Celular": "3003333333",
            "LINEA_CREDITO": "impulsa flex"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "Si",
            "refinanciar_bool": True,
            "acpt_info_email": False,
            "aceptoinfocorreo": "No",
            "agendo_asst_assr": "Si",
            "asst_assr_bool": True,
            "fecha_asst_assor": "22/12/2025",
            "desicion_correo": False,
            "correo_cliente": None,
            "primer_name": "Ana"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 5 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - webhook_refinanciamiento_fecha en acciones_ejecutadas
    # - status: "success"

asyncio.run(test_caso_5())
```

---

## CASO 6: Asesor Confirmado

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "luis rodriguez",
    "CORREO": "luis@example.com",
    "Celular": "3007777777",
    "LINEA_CREDITO": "impulsa total"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "Si",
    "refinanciar_bool": true,
    "acpt_info_email": false,
    "aceptoinfocorreo": "No",
    "agendo_asst_assr": "Si",
    "asst_assr_bool": true,
    "fecha_asst_assor": null,
    "desicion_correo": false,
    "correo_cliente": null,
    "primer_name": "Luis"
  }
}
```

**Código de prueba:**

```python
async def test_caso_6():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "luis rodriguez",
            "CORREO": "luis@example.com",
            "Celular": "3007777777",
            "LINEA_CREDITO": "impulsa total"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "Si",
            "refinanciar_bool": True,
            "acpt_info_email": False,
            "aceptoinfocorreo": "No",
            "agendo_asst_assr": "Si",
            "asst_assr_bool": True,
            "desicion_correo": False,
            "correo_cliente": None,
            "primer_name": "Luis"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 6 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - webhook_refinanciamiento_confirmado en acciones_ejecutadas
    # - status: "success"

asyncio.run(test_caso_6())
```

---

## CASO 7: Correo de Seguimiento (Sin Renovación ni Refinanciamiento)

**Payload:**

```json
{
  "input_variables": {
    "NOMBRE_TITULAR": "sofia torres",
    "CORREO": "sofia@example.com",
    "Celular": "3008888888",
    "LINEA_CREDITO": "impulsa basica"
  },
  "extracted_variables": {
    "renovacion": "No",
    "refinanciar": "No",
    "refinanciar_bool": false,
    "acpt_info_email": false,
    "aceptoinfocorreo": "Si",
    "agendo_asst_assr": "No",
    "asst_assr_bool": false,
    "fecha_asst_assor": null,
    "desicion_correo": false,
    "correo_cliente": null,
    "primer_name": "Sofia"
  }
}
```

**Código de prueba:**

```python
async def test_caso_7():
    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "sofia torres",
            "CORREO": "sofia@example.com",
            "Celular": "3008888888",
            "LINEA_CREDITO": "impulsa basica"
        },
        extracted_variables={
            "renovacion": "No",
            "refinanciar": "No",
            "refinanciar_bool": False,
            "acpt_info_email": False,
            "aceptoinfocorreo": "Si",
            "agendo_asst_assr": "No",
            "asst_assr_bool": False,
            "desicion_correo": False,
            "correo_cliente": None,
            "primer_name": "Sofia"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("CASO 7 - Resultado:")
    print(json.dumps(resultado, indent=2))
    # Esperado:
    # - correo_caso7 en acciones_ejecutadas
    # - status: "success"

asyncio.run(test_caso_7())
```

---

## Ejecución de Todas las Pruebas

```python
async def run_all_tests():
    print("=" * 80)
    print("EJECUTANDO TODAS LAS PRUEBAS")
    print("=" * 80)

    print("\n### CASO 1: Renovación + Correo + Webhook ###")
    await test_caso_1()

    print("\n### CASO 2: Sin Renovación, Solo Correo ###")
    await test_caso_2()

    print("\n### CASO 3: Refinanciamiento Básico ###")
    await test_caso_3()

    print("\n### CASO 4: Refinanciamiento con Asesoría ###")
    await test_caso_4()

    print("\n### CASO 5: Refinanciamiento con Fecha ###")
    await test_caso_5()

    print("\n### CASO 6: Asesor Confirmado ###")
    await test_caso_6()

    print("\n### CASO 7: Correo de Seguimiento ###")
    await test_caso_7()

    print("\n" + "=" * 80)
    print("TODAS LAS PRUEBAS COMPLETADAS")
    print("=" * 80)

# Ejecutar todas las pruebas
asyncio.run(run_all_tests())
```

---

## Casos de Error

### Cuando no hay destinatario

**Esperado:** status = "warning", message = "No se ejecutó ninguna acción"

### Cuando el webhook falla

**Esperado:** status = "partial", acciones_ejecutadas = [...], errores = [error message]

### Cuando ocurre una excepción

**Esperado:** status = "error", message = "Error en el procesamiento", client = nombre_cliente

---

## Variables de Entorno Requeridas

En `.env` o `utils/config.py`:

```
WEBHOOK_URL_EXTERNAL=https://tu-webhook-externo.com/webhook
DB_HOST=localhost
DB_USER=usuario
DB_PASSWORD_RENOVACION=contraseña
DB_NAME_RENOVACION=base_datos
SMTP_USER=tu-email@gmail.com
SMTP_PASSWORD=tu-contraseña-app
```
