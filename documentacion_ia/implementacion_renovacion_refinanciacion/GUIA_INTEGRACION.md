# 🔗 GUÍA DE INTEGRACIÓN

## Cómo Integrar la Función en tu Aplicación

---

## 📋 Checklist Pre-Integración

- [ ] Python 3.8+
- [ ] `httpx` instalado (`pip install httpx`)
- [ ] `logica.py` accesible
- [ ] Base de datos configurada
- [ ] SMTP configurado
- [ ] `settings` configurado

---

## 🔧 Paso 1: Verificar Dependencias

### Verificar que httpx está instalado:

```bash
pip list | grep httpx
```

### Si no está instalado:

```bash
pip install httpx
```

### Verificar otras dependencias:

```bash
pip list | grep -E "fastapi|pydantic|aiomysql"
```

---

## ⚙️ Paso 2: Configurar settings.py

Abre `utils/config.py` y agrega:

```python
# Webhook externo para refinanciamiento
WEBHOOK_URL_EXTERNAL = "https://tu-webhook-externo.com/webhook"
```

**Opciones:**

- Local: `http://localhost:8000/webhook`
- Staging: `https://staging.ejemplo.com/webhook`
- Production: `https://api.ejemplo.com/webhook`

---

## 📤 Paso 3: Integrar en logica.py

Abre `logica.py` y agrega el import:

```python
# En la sección de importes
from utils.enviar_correo_IA import (
    procesar_webhook_renovacion,
    procesar_webhook_webinar,
    procesar_llamada_renovacionYrefinanciamiento  # ← AGREGAR ESTA LÍNEA
)
```

---

## 🛣️ Paso 4: Crear Endpoint (Opcional)

Si necesitas un endpoint dedicado, agrega a `logica.py`:

```python
@app.post("/webhooks/renovacion-y-refinanciamiento")
async def webhook_renovacion_refinanciamiento(payload: WebhookPayload):
    """
    Endpoint que procesa webhooks de renovación y refinanciamiento.

    Valida 7 casos de negocio:
    1. Renovación + Correo + Webhook
    2. Sin Renovación, Solo Correo
    3-6. Refinanciamiento (variantes)
    7. Correo de Seguimiento
    """
    try:
        resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
        return resultado
    except Exception as e:
        logging.error(f"Error en webhook: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }
```

---

## 🧪 Paso 5: Prueba Básica

Crea un archivo `test_integracion.py`:

```python
import asyncio
import json
from models.models import WebhookPayload
from utils.enviar_correo_IA import procesar_llamada_renovacionYrefinanciamiento

async def test_basico():
    """Prueba básica del CASO 1"""

    payload = WebhookPayload(
        input_variables={
            "NOMBRE_TITULAR": "Test User",
            "CORREO": "test@example.com",
            "Celular": "3001234567",
            "LINEA_CREDITO": "impulsa test"
        },
        extracted_variables={
            "renovacion": "Si",
            "refinanciar": "No",
            "refinanciar_bool": False,
            "acpt_info_email": True,
            "aceptoinfocorreo": "Si",
            "agendo_asst_assr": "No",
            "asst_assr_bool": False,
            "desicion_correo": True,
            "correo_cliente": None,
            "primer_name": "Test"
        }
    )

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
    print("Resultado:")
    print(json.dumps(resultado, indent=2, default=str))

    # Validar
    assert resultado["status"] in ["success", "partial"], f"Status inválido: {resultado['status']}"
    assert len(resultado.get("acciones_ejecutadas", [])) > 0, "No se ejecutaron acciones"
    print("✅ Prueba básica pasada")

if __name__ == "__main__":
    asyncio.run(test_basico())
```

### Ejecutar prueba:

```bash
python test_integracion.py
```

---

## 🚀 Paso 6: Llamar desde Aplicación Existente

### Ejemplo en logica.py:

```python
@app.post("/procesar-renovacion")
async def procesar_renovacion(payload: WebhookPayload):
    """Endpoint existente que llama a la nueva función"""

    # Procesar renovación y refinanciamiento
    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)

    # Hacer algo con el resultado
    if resultado["status"] == "success":
        # Log de éxito
        logging.info(f"✅ Procesado exitosamente: {resultado}")
        return resultado
    else:
        # Log de error
        logging.warning(f"⚠️ Procesado con errores: {resultado}")
        return resultado
```

---

## 📊 Paso 7: Monitoreo

### Agregar métricas básicas:

```python
from datetime import datetime

async def procesar_con_metricas(payload):
    """Versión con métricas"""
    inicio = datetime.now()

    resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)

    duracion = (datetime.now() - inicio).total_seconds()

    # Log de métricas
    logging.info(f"Métrica - Duración: {duracion}s, Status: {resultado['status']}")

    return resultado
```

---

## 🔍 Paso 8: Validación

Antes de ir a producción, valida:

```python
# Checklist de validación
validaciones = {
    "Código sin errores": True,  # Ya verificado
    "Settings configurados": False,  # VERIFICAR
    "Webhook externo accesible": False,  # VERIFICAR
    "Correos funcionan": False,  # VERIFICAR
    "Base de datos conecta": False,  # VERIFICAR
    "Pruebas pasadas": False,  # VERIFICAR
}

# Completar cada validación
```

---

## 💾 Paso 9: Deployment

### En tu servidor de producción:

```bash
# 1. Actualizar código
git pull origin main

# 2. Instalar dependencias (si hay nuevas)
pip install -r requirements.txt

# 3. Verificar sintaxis
python -m py_compile utils/enviar_correo_IA.py

# 4. Ejecutar tests
pytest test_integracion.py

# 5. Reiniciar aplicación
systemctl restart mi-aplicacion
```

---

## 📝 Paso 10: Documentación

### Para tu equipo, crea un README:

```markdown
# Webhook de Renovación y Refinanciamiento

## Función

Procesa webhooks con 7 casos de validación:

- Renovación con correo y webhook
- Refinanciamiento con varias opciones
- Correos de seguimiento

## Endpoint

POST /webhooks/renovacion-y-refinanciamiento

## Payload Esperado

Ver DOCUMENTACION_CASOS_VALIDACION.md

## Ejemplos

Ver EJEMPLOS_PRUEBAS.md

## Soporte

Contactar a [tu-email]
```

---

## 🐛 Troubleshooting de Integración

### Error: "ModuleNotFoundError: No module named 'httpx'"

```bash
pip install httpx
```

### Error: "WEBHOOK_URL_EXTERNAL not configured"

```python
# En utils/config.py, agregar:
WEBHOOK_URL_EXTERNAL = "tu-url"
```

### Error: "Connection refused"

```python
# Verificar que el webhook externo está corriendo
# curl -X POST https://tu-webhook-externo.com/webhook
```

### Error: "Email no se envía"

```python
# Verificar credenciales SMTP en settings
# Revisar logs de email_service.py
```

### Error: "Timeout en webhook"

```python
# Aumentar timeout en llamada:
respuesta = await _hacer_peticion_webhook_externo(url, payload, timeout=30)
```

---

## 📞 Checklist Final

- [ ] Code review completado
- [ ] Tests unitarios pasados
- [ ] Tests de integración pasados
- [ ] Documentación actualizada
- [ ] Settings configurados
- [ ] Webhook externo validado
- [ ] Credenciales verificadas
- [ ] Logs configurados
- [ ] Monitoreo activo
- [ ] Rollback plan listo

---

## 🎉 ¡Listo para Producción!

Una vez completados todos los pasos:

```python
# Ejecutar prueba de fuego
resultado = await procesar_llamada_renovacionYrefinanciamiento(payload_completo)

# Si resultado["status"] == "success", ¡estás listo!
print("✅ Integración completada exitosamente")
```

---

## 📚 Referencias

- [Código Principal](utils/enviar_correo_IA.py)
- [Documentación Completa](DOCUMENTACION_CASOS_VALIDACION.md)
- [Ejemplos de Prueba](EJEMPLOS_PRUEBAS.md)
- [Diagrama de Flujo](DIAGRAMA_FLUJO_VISUAL.md)
