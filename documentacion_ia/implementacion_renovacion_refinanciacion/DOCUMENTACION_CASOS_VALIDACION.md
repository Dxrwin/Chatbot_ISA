# Documentación: Función procesar_llamada_renovacionYrefinanciamiento

## Descripción General

La función `procesar_llamada_renovacionYrefinanciamiento` orquesta la lógica de negocio para renovación y refinanciamiento. Valida variables extraídas y ejecuta:

- Envío de correos de renovación
- Peticiones POST a webhooks externos para refinanciamiento

---

## Casos de Validación

### CASO 1: Renovación con Aceptación de Correo

**Condiciones:**

- `renovacion == "Si"` AND
- (`acpt_info_email == True` OR `aceptoinfocorreo == "Si"`)

**Acciones:**

- ✅ Enviar correo de renovación
- ✅ Llamar a webhook externo con tipo "renovacion"

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "renovacion": "Si",
    "acpt_info_email": true,
    "aceptoinfocorreo": "Si",
    "primer_name": "Darwin",
    "desicion_correo": true
  }
}
```

**Logging:**

```
INFO: CASO 1: renovacion=Si + aceptación de correo → Envío de correo + webhook
INFO: Iniciando petición POST a webhook: https://webhook.ejemplo.com/renovacion
✅ Correo de renovación enviado a darwinandres901@gmail.com
✅ Petición webhook ejecutada
```

---

### CASO 2: Sin Renovación pero con Aceptación de Correo

**Condiciones:**

- `renovacion == "No"` AND
- (`acpt_info_email == True` OR `aceptoinfocorreo == "Si"`)

**Acciones:**

- ✅ Enviar correo informativo (sin webhook)

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "renovacion": "No",
    "aceptoinfocorreo": "Si",
    "acpt_info_email": true,
    "primer_name": "Darwin"
  }
}
```

**Logging:**

```
INFO: CASO 2: renovacion=No + aceptación de correo → Solo envío de correo
✅ Correo enviado a darwinandres901@gmail.com
```

---

### CASO 3: Refinanciamiento Básico

**Condiciones:**

- `refinanciar_bool == True` AND
- `refinanciar == "Si"`

**Acciones:**

- ✅ Llamar webhook externo con tipo "refinanciamiento"

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "refinanciar_bool": true,
    "refinanciar": "Si"
  }
}
```

**Payload del webhook:**

```json
{
  "cliente": "darwin andres pacheco",
  "email": "darwinandres901@gmail.com",
  "telefono": "3002613153",
  "tipo": "refinanciamiento",
  "linea_credito": "impulsa unicosta"
}
```

---

### CASO 4: Refinanciamiento con Asesoría Agendada

**Condiciones:**

- `refinanciar_bool == True` AND
- `refinanciar == "Si"` AND
- `agendo_asst_assr == "Si"`

**Acciones:**

- ✅ Llamar webhook externo con tipo "refinanciamiento_con_asesoria"

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "refinanciar_bool": true,
    "refinanciar": "Si",
    "agendo_asst_assr": "Si"
  }
}
```

**Payload del webhook:**

```json
{
  "cliente": "darwin andres pacheco",
  "email": "darwinandres901@gmail.com",
  "telefono": "3002613153",
  "tipo": "refinanciamiento_con_asesoria",
  "linea_credito": "impulsa unicosta",
  "agendo_asesor": true
}
```

---

### CASO 5: Refinanciamiento con Fecha de Asesoría Confirmada

**Condiciones:**

- `refinanciar_bool == True` AND
- `refinanciar == "Si"` AND
- `agendo_asst_assr == "Si"` AND
- `fecha_asst_assor` contiene una fecha válida (ej: "22/12/2025")

**Acciones:**

- ✅ Llamar webhook externo con tipo "refinanciamiento_con_fecha"

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "refinanciar_bool": true,
    "refinanciar": "Si",
    "agendo_asst_assr": "Si",
    "fecha_asst_assor": "22/12/2025"
  }
}
```

**Payload del webhook:**

```json
{
  "cliente": "darwin andres pacheco",
  "email": "darwinandres901@gmail.com",
  "telefono": "3002613153",
  "tipo": "refinanciamiento_con_fecha",
  "linea_credito": "impulsa unicosta",
  "agendo_asesor": true,
  "fecha_asesor": "22/12/2025"
}
```

---

### CASO 6: Refinanciamiento con Asesor Confirmado

**Condiciones:**

- `refinanciar == "Si"` AND
- `refinanciar_bool == True` AND
- `asst_assr_bool == True`

**Acciones:**

- ✅ Llamar webhook externo con tipo "refinanciamiento_asesoria_confirmada"

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "refinanciar": "Si",
    "refinanciar_bool": true,
    "asst_assr_bool": true
  }
}
```

**Payload del webhook:**

```json
{
  "cliente": "darwin andres pacheco",
  "email": "darwinandres901@gmail.com",
  "telefono": "3002613153",
  "tipo": "refinanciamiento_asesoria_confirmada",
  "linea_credito": "impulsa unicosta",
  "asesor_confirmado": true
}
```

---

### CASO 7: Correo de Seguimiento (sin Renovación ni Refinanciamiento)

**Condiciones:**

- `aceptoinfocorreo == "Si"` AND
- `refinanciar == "No"` AND
- `refinanciar_bool == False` AND
- `renovacion == "No"`

**Acciones:**

- ✅ Enviar correo informativo de seguimiento

**Ejemplo de payload:**

```json
{
  "extracted_variables": {
    "aceptoinfocorreo": "Si",
    "refinanciar": "No",
    "refinanciar_bool": false,
    "renovacion": "No",
    "primer_name": "Darwin"
  }
}
```

**Logging:**

```
INFO: CASO 7: Sin renovación ni refinanciamiento, pero con aceptación → Correo
✅ Correo informativo CASO 7 enviado a darwinandres901@gmail.com
```

---

## Manejo de Errores

### Logging

Todos los errores se registran con `logging.error()`:

```python
logging.error(f"❌ No se envió correo: {respuesta_correo.get('message')}")
```

### Notificaciones de Error

Se utiliza `error_notify()` para notificar errores:

```python
await error_notify(
    method_name="procesar_llamada_renovacionYrefinanciamiento",
    client_id=nombre_cliente,
    error_message=f"Error específico del caso: {detalles}"
)
```

### Respuesta en Caso de Error

```json
{
  "status": "error",
  "message": "Error en el procesamiento",
  "acciones": [],
  "cliente": "darwin andres pacheco"
}
```

---

## Función Auxiliar: \_hacer_peticion_webhook_externo

Realiza peticiones POST seguras a webhooks externos con:

- Manejo de timeouts
- Reintentos de conexión
- Manejo de excepciones
- Logging detallado

**Parámetros:**

- `url`: URL del webhook externo
- `payload`: Diccionario con datos a enviar
- `timeout`: Tiempo máximo de espera (por defecto 10 segundos)

**Respuesta exitosa:**

```json
{
  "status": "success",
  "status_code": 200,
  "response": {...}
}
```

**Respuesta de error:**

```json
{
  "status": "error",
  "message": "Descripción del error",
  "status_code": 500
}
```

---

## Configuración Requerida

En `utils/config.py`, asegúrate de tener:

```python
WEBHOOK_URL_EXTERNAL = "https://tu-webhook-externo.com/webhook"
```

Si no está configurada, la función loguea una advertencia y continúa sin hacer las peticiones webhook.

---

## Variables Importantes del Payload

### input_variables (campos requeridos/opcionales):

- `NOMBRE_TITULAR`: Nombre del cliente
- `CORREO`: Email principal
- `Celular`: Número de teléfono
- `LINEA_CREDITO`: Línea de crédito
- `SEMESTRE`: Semestre académico

### extracted_variables (validaciones):

- `renovacion`: "Si" o "No"
- `refinanciar`: "Si" o "No"
- `refinanciar_bool`: Boolean
- `acpt_info_email`: Boolean
- `aceptoinfocorreo`: "Si" o "No"
- `agendo_asst_assr`: "Si" o "No"
- `asst_assr_bool`: Boolean
- `fecha_asst_assor`: Fecha en formato "DD/MM/YYYY"
- `desicion_correo`: Boolean o None
- `correo_cliente`: Email alternativo
- `primer_name`: Primer nombre del cliente

---

## Ejemplo Completo de Payload (Caso Combinado)

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
    "agendo_asst_assr": "Si",
    "asst_assr_bool": true,
    "fecha_asst_assor": "22/12/2025",
    "desicion_correo": true,
    "correo_cliente": "darwinandres901@gmail.com",
    "primer_name": "Darwin"
  }
}
```

**Resultado esperado:** Se ejecutarán CASO 1 y CASO 2 (envío de correo + webhook de renovación)

---

## Respuesta Exitosa Combinada

```json
{
  "status": "success",
  "cliente": "darwin andres pacheco",
  "correo": "darwinandres901@gmail.com",
  "acciones_ejecutadas": ["correo_renovacion", "webhook_renovacion"],
  "errores": null
}
```

---

## Troubleshooting

### El webhook no se ejecuta

- Verificar que `WEBHOOK_URL_EXTERNAL` esté configurada en `settings`
- Revisar los logs para ver si hay errores de conexión
- Validar que la URL es accesible desde el servidor

### El correo no se envía

- Verificar que `destinatario` no sea None o vacío
- Revisar credenciales de SMTP en `email_service.py`
- Consultar logs de `enviar_correo_renovacion()`

### No se ejecuta ninguna acción

- Verificar que las variables extraídas coincidan con las condiciones
- Revisar los logs para ver qué validaciones no se cumplieron
- Usar `error_notify()` para obtener detalles de la notificación de error
