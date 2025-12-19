# 🔗 INTEGRACIÓN BITRIX24

## Función: `integracion_bitrix`

---

## 📝 Descripción

La función `integracion_bitrix` realiza una búsqueda de clientes en Bitrix24 usando el número de teléfono proporcionado en `input_variables.Celular`.

**Ubicación:** [utils/enviar_correo_IA.py](utils/enviar_correo_IA.py) líneas 27-135

---

## 🔧 Características

✅ Conexión asincrónica a API de Bitrix24  
✅ Busca contactos por número de teléfono  
✅ Agrega automáticamente código de país (+57)  
✅ Manejo robusto de errores y excepciones  
✅ Logging detallado de cada operación  
✅ Notificaciones con `info_notify()` y `error_notify()`  
✅ Timeout configurable (default 10 segundos)

---

## 📞 Parámetros

```python
async def integracion_bitrix(celular: str, timeout: int = 10) -> Dict[str, Any]:
```

### Argumentos:

- **celular** (str): Número de teléfono sin código país (ej: "3002613153")
- **timeout** (int): Tiempo máximo de espera en segundos (default: 10)

---

## 📤 Respuesta

### Éxito - Cliente Encontrado:

```json
{
  "status": "success",
  "message": "Cliente encontrado",
  "result": [
    {
      "ID": "12345",
      "NAME": "Darwin Pacheco",
      "EMAIL": "darwin@ejemplo.com",
      "PHONE": "+573002613153"
    }
  ],
  "id": "12345",
  "cliente_data": {
    "ID": "12345",
    "NAME": "Darwin Pacheco",
    "EMAIL": "darwin@ejemplo.com",
    "PHONE": "+573002613153"
  }
}
```

### Error - Cliente No Encontrado:

```json
{
  "status": "error",
  "message": "No se encontró cliente con teléfono +573002613153",
  "result": []
}
```

### Error - Conexión Fallida:

```json
{
  "status": "error",
  "message": "Error de conexión a Bitrix24: [error message]",
  "result": null
}
```

### Error - Timeout:

```json
{
  "status": "error",
  "message": "Timeout en conexión a Bitrix24: [error message]",
  "result": null
}
```

---

## 🔄 Flujo de Integración

### En procesar_llamada_renovacion_Y_refinanciamiento:

Los casos que invocan `integracion_bitrix`:

```
CASO 1: Renovación + Correo + Webhook
  ↓
  1. Enviar Correo
  2. Buscar Cliente en Bitrix24 (integracion_bitrix)
  3. Enviar Webhook con ID de Bitrix24

CASO 3: Refinanciamiento Básico
  ↓
  1. Buscar Cliente en Bitrix24 (integracion_bitrix)
  2. Enviar Webhook con ID de Bitrix24

CASO 4: Refinanciamiento + Asesoría
  ↓
  1. Buscar Cliente en Bitrix24 (integracion_bitrix)
  2. Enviar Webhook con ID de Bitrix24 + datos de asesoría

CASO 5: Refinanciamiento + Fecha
  ↓
  1. Buscar Cliente en Bitrix24 (integracion_bitrix)
  2. Enviar Webhook con ID de Bitrix24 + fecha

CASO 6: Asesor Confirmado
  ↓
  1. Buscar Cliente en Bitrix24 (integracion_bitrix)
  2. Enviar Webhook con ID de Bitrix24 + confirmación asesor
```

---

## 📋 Ejemplos de Uso

### Uso Básico:

```python
# Buscar cliente por teléfono
respuesta = await integracion_bitrix(celular="3002613153")

if respuesta["status"] == "success":
    id_cliente = respuesta["id"]
    print(f"✅ Cliente encontrado - ID: {id_cliente}")
else:
    print(f"❌ Error: {respuesta['message']}")
```

### Con Timeout Personalizado:

```python
# Timeout de 5 segundos
respuesta = await integracion_bitrix(
    celular="3002613153",
    timeout=5
)
```

### En el Contexto de procesar_llamada_renovacion_Y_refinanciamiento:

```python
# CASO 1: Renovación
if extracted_vars.renovacion == "Si":
    # Buscar cliente en Bitrix24
    respuesta_bitrix = await integracion_bitrix(numero_telefono)

    if respuesta_bitrix["status"] == "success":
        id_bitrix = respuesta_bitrix["id"]

        # Enviar webhook con ID
        payload_webhook = {
            "cliente": nombre_cliente,
            "telefono": numero_telefono,
            "id_bitrix": id_bitrix,  # ← ID de Bitrix24
            "tipo": "renovacion"
        }

        await _hacer_peticion_webhook_externo(webhook_url, payload_webhook)
```

---

## 🔌 Configuración API Bitrix24

### URL Actual:

```
https://horizontesas-fontumi.bitrix24.es/rest/6/untkqcnft2vadt5d/crm.contact.list
```

### Parámetros de Búsqueda:

```python
{
  "filter": {
    "PHONE": "+573002613153"  # Teléfono con código de país
  },
  "select": [
    "ID",          # ID del contacto
    "NAME",        # Nombre
    "EMAIL",       # Email
    "PHONE"        # Teléfono
  ]
}
```

---

## 📊 Logging Esperado

### Búsqueda Exitosa:

```
INFO: Buscando cliente en Bitrix24 con teléfono: +573002613153
INFO: Enviando petición a Bitrix24 con payload: {...}
INFO: Respuesta de Bitrix24 - Status: 200
INFO: Respuesta JSON: {...}
✅ Cliente encontrado en Bitrix24 - ID: 12345
```

### Cliente No Encontrado:

```
INFO: Buscando cliente en Bitrix24 con teléfono: +573002613153
INFO: Enviando petición a Bitrix24 con payload: {...}
INFO: Respuesta de Bitrix24 - Status: 200
⚠️ No se encontró cliente con teléfono: +573002613153
❌ No se encontró contacto en Bitrix24 con teléfono +573002613153
```

### Error de Conexión:

```
INFO: Buscando cliente en Bitrix24 con teléfono: +573002613153
INFO: Enviando petición a Bitrix24 con payload: {...}
❌ Error de conexión a Bitrix24: [error details]
```

---

## ⚠️ Manejo de Errores

### Posibles Errores:

| Error            | Causa                     | Acción                           |
| ---------------- | ------------------------- | -------------------------------- |
| Número vacío     | `celular` es None o vacío | Registra warning y retorna error |
| Conexión fallida | API no disponible         | Retorna error con mensaje        |
| Timeout          | API lenta                 | Retorna error con timeout        |
| JSON inválido    | Respuesta no JSON         | Parsea error y retorna           |
| Status ≠ 200     | API rechaza               | Retorna status code y error      |
| ID no encontrado | Result vacío              | Retorna error "no encontrado"    |

### Notificaciones de Error:

Todos los errores se notifican con `error_notify()`:

```python
await error_notify(
    method_name="integracion_bitrix",
    client_id=celular,
    error_message="Descripción del error"
)
```

---

## 🔐 Seguridad

✅ URL validada  
✅ Teléfono sanitizado  
✅ Timeout para evitar bloqueos  
✅ Error handling robusto  
✅ Logging de operaciones  
✅ No expone credenciales en logs

---

## 📈 Casos de Uso Integrados

### CASO 1: Renovación

```python
# Busca cliente antes de enviar correo de renovación
respuesta_bitrix = await integracion_bitrix(numero_telefono)
if respuesta_bitrix["status"] == "success":
    acciones_ejecutadas.append("busqueda_bitrix_renovacion")
```

### CASO 3: Refinanciamiento

```python
# Busca cliente antes de refinanciar
respuesta_bitrix = await integracion_bitrix(numero_telefono)
if respuesta_bitrix["status"] == "success":
    acciones_ejecutadas.append("busqueda_bitrix_caso3")
```

### CASO 4-6: Refinanciamiento + Asesoría

```python
# Busca cliente para asignación de asesor
respuesta_bitrix = await integracion_bitrix(numero_telefono)
if respuesta_bitrix["status"] == "success":
    acciones_ejecutadas.append("busqueda_bitrix_caso4/5/6")
```

---

## 🧪 Prueba Manual

### Código de Prueba:

```python
import asyncio
from utils.enviar_correo_IA import integracion_bitrix

async def test_bitrix():
    # Prueba con un número conocido
    respuesta = await integracion_bitrix(celular="3002613153")

    print(f"Status: {respuesta['status']}")
    print(f"Mensaje: {respuesta['message']}")

    if respuesta["status"] == "success":
        print(f"✅ ID Cliente: {respuesta['id']}")
        print(f"Datos: {respuesta['cliente_data']}")
    else:
        print(f"❌ Error: {respuesta.get('error', 'Desconocido')}")

# Ejecutar
asyncio.run(test_bitrix())
```

---

## 📞 Formato del Teléfono

**Entrada:**

```
Celular: "3002613153"  # Sin código de país
```

**Procesamiento:**

```python
telefono_completo = f"+57{str(celular).strip()}"
# Resultado: "+573002613153"
```

**Envío a Bitrix24:**

```json
{
  "filter": {
    "PHONE": "+573002613153"
  }
}
```

---

## ✅ Validación

- [x] Función implementada
- [x] Integrada en CASO 1
- [x] Integrada en CASO 3
- [x] Integrada en CASO 4
- [x] Integrada en CASO 5
- [x] Integrada en CASO 6
- [x] Logging completo
- [x] Error handling
- [x] Sin errores de sintaxis

---

## 📝 Notas Importantes

1. **Teléfono Requerido:** La función requiere un número de teléfono válido
2. **Código de País:** Se agrega automáticamente (+57 para Colombia)
3. **API de Bitrix24:** URL y credenciales están codificadas en la función
4. **Caché:** La función NO implementa caché, cada llamada consulta Bitrix24
5. **Timeout:** Default 10 segundos, ajustable según necesidad

---

## 🚀 Próximas Mejoras Sugeridas

- Implementar caché de búsquedas recientes
- Agregar reintentos automáticos en timeout
- Validación de formato de teléfono
- Soporte para múltiples códigos de país
- Actualización de datos en Bitrix24 (POST/PUT)
