# Resumen de Cambios - Implementación de procesar_llamada_renovacionYrefinanciamiento

## Fecha: 17 de Diciembre de 2025

---

## 📝 Descripción General

Se ha implementado completamente la función `procesar_llamada_renovacionYrefinanciamiento` en el archivo [utils/enviar_correo_IA.py](utils/enviar_correo_IA.py) con validaciones para 7 casos de negocio diferentes que combinan:

1. **Envío de correos** (usando `enviar_correo_renovacion`)
2. **Peticiones POST a webhooks externos** (usando nueva función `_hacer_peticion_webhook_externo`)

---

## 🔧 Cambios Realizados

### 1. Importación de httpx

Se agregó `import httpx` para realizar peticiones HTTP asincrónicas.

```python
import httpx
```

---

### 2. Nueva Función Auxiliar: `_hacer_peticion_webhook_externo`

**Ubicación:** [utils/enviar_correo_IA.py](utils/enviar_correo_IA.py) líneas 27-97

**Responsabilidades:**

- Realiza peticiones POST a webhooks externos
- Maneja errores de conexión, timeout y excepciones
- Registra logs detallados de cada petición
- Notifica errores usando `error_notify()`

**Parámetros:**

- `url` (str): URL del webhook externo
- `payload` (Dict): Datos a enviar en formato JSON
- `timeout` (int): Tiempo máximo de espera (default: 10 segundos)

**Retorna:**

```python
{
    "status": "success" | "error",
    "status_code": int,  # Solo si exitoso
    "response": dict,    # Respuesta del webhook
    "message": str       # Mensaje de error si aplica
}
```

---

### 3. Función Principal: `procesar_llamada_renovacionYrefinanciamiento`

**Ubicación:** [utils/enviar_correo_IA.py](utils/enviar_correo_IA.py) líneas 99-890

**Funcionalidad:**
Implementa lógica compleja con 7 casos de validación que pueden ejecutar:

- Envío de correos de renovación
- Peticiones a webhooks externos
- Combinaciones de ambos

---

## 📋 Casos de Validación Implementados

### CASO 1: Renovación + Correo + Webhook

- **Condición:** `renovacion == "Si"` AND (`acpt_info_email == True` OR `aceptoinfocorreo == "Si"`)
- **Acciones:** Enviar correo + Llamar webhook con tipo "renovacion"
- **Status:** ✅ Implementado

### CASO 2: Sin Renovación, Solo Correo

- **Condición:** `renovacion == "No"` AND (`acpt_info_email == True` OR `aceptoinfocorreo == "Si"`)
- **Acciones:** Enviar correo informativo (sin webhook)
- **Status:** ✅ Implementado

### CASO 3: Refinanciamiento Básico

- **Condición:** `refinanciar_bool == True` AND `refinanciar == "Si"`
- **Acciones:** Llamar webhook con tipo "refinanciamiento"
- **Status:** ✅ Implementado

### CASO 4: Refinanciamiento con Asesoría

- **Condición:** `refinanciar_bool == True` AND `refinanciar == "Si"` AND `agendo_asst_assr == "Si"`
- **Acciones:** Llamar webhook con tipo "refinanciamiento_con_asesoria"
- **Status:** ✅ Implementado

### CASO 5: Refinanciamiento con Fecha de Asesoría

- **Condición:** CASO 4 + `fecha_asst_assor` contiene fecha válida
- **Acciones:** Llamar webhook con tipo "refinanciamiento_con_fecha" + fecha
- **Status:** ✅ Implementado

### CASO 6: Asesor Confirmado

- **Condición:** `refinanciar == "Si"` AND `refinanciar_bool == True` AND `asst_assr_bool == True`
- **Acciones:** Llamar webhook con tipo "refinanciamiento_asesoria_confirmada"
- **Status:** ✅ Implementado

### CASO 7: Correo de Seguimiento

- **Condición:** `aceptoinfocorreo == "Si"` AND `refinanciar == "No"` AND `refinanciar_bool == False` AND `renovacion == "No"`
- **Acciones:** Enviar correo informativo de seguimiento
- **Status:** ✅ Implementado

---

## 🎯 Características Principales

### Manejo Inteligente de Correos

- Determina automáticamente el destinatario basado en:
  1. `desicion_correo` (preferencia del cliente)
  2. `correo_cliente` (email alternativo)
  3. `CORREO` (email guardado como fallback)

### Logging Exhaustivo

- Registra cada paso del proceso
- Indica con ✅ las acciones exitosas
- Alerta con ❌ los errores
- Advierte con ⚠️ situaciones anormales

### Notificaciones de Error

- Usa `error_notify()` para reportar problemas
- Usa `info_notify()` para eventos exitosos
- Cada notificación incluye:
  - Nombre del método
  - ID del cliente
  - Mensaje descriptivo

### Manejo de Errores Robusto

- Try-except en múltiples niveles
- No propaga excepciones innecesariamente
- Retorna estados parciales cuando hay fallos parciales
- Documenta errores en la respuesta

### Respuesta Estructurada

```python
{
    "status": "success" | "partial" | "error" | "warning",
    "cliente": str,
    "correo": str,
    "acciones_ejecutadas": [str, ...],
    "errores": [str, ...] | None,
    "message": str  # En caso de error
}
```

---

## 🔄 Flujo de Ejecución

1. **Parsing de Variables**

   - Extrae nombres, correos, números telefónicos
   - Identifica si hay variables específicas

2. **Determinación de Destinatario**

   - Sigue orden de preferencia establecido
   - Valida que no sea None o vacío

3. **Evaluación de Casos** (pueden ser múltiples)

   - Evalúa cada condición independientemente
   - Ejecuta acciones correspondientes
   - Acumula errores sin detener la ejecución

4. **Registro de Acciones**

   - Almacena en lista `acciones_ejecutadas`
   - Documenta en `errores` cualquier problema

5. **Respuesta Final**
   - Retorna resumen completo
   - Notifica sobre resultado

---

## 📦 Variables de Payload Esperadas

### input_variables:

```python
{
    "NOMBRE_TITULAR": str,      # Requerido
    "CORREO": str,              # Requerido
    "Celular": str,             # Opcional
    "LINEA_CREDITO": str,       # Opcional
    "SEMESTRE": str,            # Opcional
    "Nombre": str,              # Alternativa a NOMBRE_TITULAR
    "EMAIL": str,               # Alternativa a CORREO
    "Contacto": str,            # Alternativa a Celular
    "PHONE_NUMBER": str,        # Alternativa a Celular
    "Universidad": str,         # Información adicional
    "ESTADO_CREDITO": str,      # Información adicional
    "CUOTAS_PENDIENTES": int,   # Información adicional
    "SALDO_PENDIENTE": str      # Información adicional
}
```

### extracted_variables:

```python
{
    "renovacion": "Si" | "No",
    "refinanciar": "Si" | "No",
    "refinanciar_bool": bool,
    "acpt_info_email": bool,
    "aceptoinfocorreo": "Si" | "No",
    "agendo_asst_assr": "Si" | "No",
    "asst_assr_bool": bool,
    "fecha_asst_assor": str | None,  # Formato: "DD/MM/YYYY"
    "desicion_correo": bool | None,
    "correo_cliente": str | None,
    "primer_name": str,
    "contesto_llamada": bool,
    "estado": bool | None,
    "comentario_libre": str,
    "objetivo": str,
    # ... otras variables opcionales
}
```

---

## 🚀 Uso

### Importar la función:

```python
from utils.enviar_correo_IA import procesar_llamada_renovacionYrefinanciamiento
```

### Llamar la función:

```python
resultado = await procesar_llamada_renovacionYrefinanciamiento(payload)
```

### Acceder a resultados:

```python
if resultado["status"] == "success":
    print(f"Acciones ejecutadas: {resultado['acciones_ejecutadas']}")
    # ['correo_renovacion', 'webhook_renovacion']
else:
    print(f"Errores: {resultado['errores']}")
    # ['Error al enviar correo', 'Webhook rechazado']
```

---

## 📄 Documentación Generada

Se han creado dos archivos de referencia:

1. **[DOCUMENTACION_CASOS_VALIDACION.md](DOCUMENTACION_CASOS_VALIDACION.md)**

   - Explicación detallada de cada caso
   - Ejemplos de payloads
   - Logs esperados
   - Troubleshooting

2. **[EJEMPLOS_PRUEBAS.md](EJEMPLOS_PRUEBAS.md)**
   - Código de prueba para cada caso
   - Funciones async de ejemplo
   - Suite completa de pruebas
   - Variables de entorno requeridas

---

## ⚙️ Configuración Requerida

En `utils/config.py` o `.env`:

```python
WEBHOOK_URL_EXTERNAL = "https://tu-webhook-externo.com/webhook"
```

**Nota:** Si no está configurada, la función continúa sin hacer peticiones webhook (con warning en logs).

---

## ✅ Validaciones de Sintaxis

✓ Sin errores de sintaxis  
✓ Tipado correcto  
✓ Importaciones correctas  
✓ Variables bien definidas  
✓ Indentación correcta

---

## 🧪 Pruebas Recomendadas

### Unit Tests:

- Cada uno de los 7 casos
- Manejo de errores en correos
- Manejo de errores en webhooks
- Combinaciones múltiples de casos

### Integration Tests:

- Con servicio de correos real
- Con webhook externo real
- Con base de datos

### Edge Cases:

- Payload sin destinatario
- Variables None o vacías
- Webhook no disponible
- Timeout en conexión

---

## 📝 Notas Importantes

1. **Múltiples Casos:** La función evalúa TODOS los casos que cumplan condiciones, no solo el primero.

2. **Idempotencia:** La función NO implementa caché de deduplicación como `procesar_webhook_webinar`. Si necesita, considere agregar mecanismo similar.

3. **Timeout de Webhook:** Configurado en 10 segundos por defecto. Modificable en llamada.

4. **Errores No Fatales:** Los errores en correos o webhooks no detienen el procesamiento. Se documentan y continúa.

5. **Status Codes:**
   - `success`: Todo ejecutado exitosamente
   - `partial`: Algunas acciones fallaron
   - `error`: Error crítico
   - `warning`: No se ejecutó ninguna acción

---

## 🔐 Seguridad

- URLs de webhook se registran en logs (considere cifrar en producción)
- Payloads contienen datos personales (asegurar cifrado en tránsito)
- Error handling robusto previene exposición de detalles innecesarios

---

## 📞 Contacto / Soporte

Para consultas sobre la implementación, revisar:

1. Logs de la aplicación
2. [DOCUMENTACION_CASOS_VALIDACION.md](DOCUMENTACION_CASOS_VALIDACION.md)
3. [EJEMPLOS_PRUEBAS.md](EJEMPLOS_PRUEBAS.md)
4. Código comentado en [utils/enviar_correo_IA.py](utils/enviar_correo_IA.py)
