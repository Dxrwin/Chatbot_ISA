# 📄 Scripts SQL para Servicios Externos con Variables Dinámicas

## 📋 Descripción

Esta carpeta contiene los scripts SQL necesarios para implementar el sistema de **servicios externos con variables dinámicas** en la base de datos. Este sistema permite:

- ✅ **Eliminar duplicación** de servicios por método HTTP
- ✅ **Variables dinámicas** en headers y body (`{ORG_ID}`, `{access_token}`, etc.)
- ✅ **Configuración centralizada** desde base de datos
- ✅ **Mantenimiento simplificado** sin tocar código Python

## 📁 Archivos Incluidos

| Archivo                               | Descripción                                           |
| ------------------------------------- | ----------------------------------------------------- |
| `update_servicios_externos.sql`       | **Script principal** - Actualiza servicios existentes |
| `create_servicios_externos_table.sql` | Crea tabla si no existe                               |
| `verify_servicios_externos.sql`       | Verifica que los cambios se aplicaron correctamente   |
| `test_servicios_externos.py`          | Script Python para probar la funcionalidad            |

## 🚀 Guía de Implementación

### Paso 1: Crear tabla (si no existe)

```bash
# Ejecutar solo si la tabla servicios_externos no existe
mysql -u [usuario] -p [base_datos] < create_servicios_externos_table.sql
```

### Paso 2: Actualizar servicios existentes

```bash
# Aplicar cambios principales
mysql -u [usuario] -p [base_datos] < update_servicios_externos.sql
```

### Paso 3: Verificar cambios

```bash
# Verificar que todo se aplicó correctamente
mysql -u [usuario] -p [base_datos] < verify_servicios_externos.sql
```

### Paso 4: Probar funcionalidad

```bash
# Ejecutar script de prueba Python
python test_servicios_externos.py
```

## 🔧 Servicios Configurados

### Servicios Actualizados

| Código                   | Servicio                  | Método | Variables Dinámicas                        |
| ------------------------ | ------------------------- | ------ | ------------------------------------------ |
| `AUTH_TOKEN`             | Token de autenticación    | POST   | -                                          |
| `KUENTA_LINES_LIST`      | Listar líneas de producto | GET    | `ORG_ID`, `access_token`                   |
| `KUENTA_TOTP_APPROVE`    | Confirmar TOTP            | POST   | `ORG_ID`, `access_token`, `codigo_totp`    |
| `KUENTA_ORDER_STATUS`    | Estado de orden           | GET    | `ORG_ID`, `access_token`                   |
| `KUENTA_PAYABLE_GET`     | Consultar payable         | GET    | `ORG_ID`, `access_token`                   |
| `KUENTA_PAYABLE_CREATE`  | Crear payable             | POST   | `ORG_ID`, `access_token`, + body completo  |
| `KUENTA_PAYABLE_CONFIRM` | Confirmar payable         | PATCH  | `ORG_ID`, `access_token`, `credit_id`      |
| `KUENTA_PRODUCT_GET`     | Obtener producto          | GET    | `ORG_ID`, `access_token`, `linea_producto` |

### Variables Dinámicas Disponibles

| Variable               | Descripción             | Ejemplo                                |
| ---------------------- | ----------------------- | -------------------------------------- |
| `{ORG_ID}`             | ID de organización      | `12345`                                |
| `{access_token}`       | Token de autenticación  | `Bearer abc123...`                     |
| `{credit_id}`          | ID del crédito          | `8c082794-796c-4987-ac28-e4918bea590d` |
| `{codigo_totp}`        | Código TOTP             | `123456`                               |
| `{linea_producto}`     | ID de línea de producto | `prod-123`                             |
| `{creditLineId}`       | ID de línea de crédito  | `line-456`                             |
| `{principal}`          | Monto principal         | `2500000`                              |
| `{time}`               | Plazo                   | `12`                                   |
| `{paymentFrequency}`   | Frecuencia de pago      | `30`                                   |
| `{initialFee}`         | Cuota inicial           | `125000`                               |
| `{disbursementMethod}` | Método de desembolso    | `bank_transfer`                        |
| `{source}`             | Origen                  | `web`                                  |
| `{redirectUrl}`        | URL de redirección      | `https://example.com/redirect`         |
| `{callbackUrl}`        | URL de callback         | `https://example.com/callback`         |
| `{meta}`               | Metadatos               | `{"key": "value"}`                     |

## 🔍 Verificación de Resultados

Después de ejecutar los scripts, deberías ver:

```sql
-- Resultado esperado en verify_servicios_externos.sql
+--------------------------------+----------------------+
| titulo                         | total_servicios_configurados |
+--------------------------------+----------------------+
| RESUMEN DE CONFIGURACIÓN       | 8                    |
+--------------------------------+----------------------+

-- Todos los servicios con headers dinámicos
+--------------------+---------------------+-----------------+
| codigo             | authorization_header | org_id_header   |
+--------------------+---------------------+-----------------+
| KUENTA_LINES_LIST  | "{access_token}"    | "{ORG_ID}"      |
| KUENTA_TOTP_APPROVE| "{access_token}"    | "{ORG_ID}"      |
| ...                | ...                 | ...             |
+--------------------+---------------------+-----------------+
```

## 🐛 Solución de Problemas

### Error: "Table doesn't exist"

```bash
# Ejecutar script de creación de tabla
mysql -u [usuario] -p [base_datos] < create_servicios_externos_table.sql
```

### Error: "Duplicate entry"

Los scripts usan `ON DUPLICATE KEY UPDATE`, pero si hay conflictos:

```sql
# Verificar duplicados
SELECT codigo, COUNT(*) FROM servicios_externos GROUP BY codigo HAVING COUNT(*) > 1;

# Limpiar duplicados manualmente si es necesario
DELETE t1 FROM servicios_externos t1
INNER JOIN servicios_externos t2
WHERE t1.id > t2.id AND t1.codigo = t2.codigo;
```

### Variables no se reemplazan

Verificar que el código Python use `set_dynamic_values()`:

```python
client = await ExternalClient.from_code("KUENTA_PAYABLE_CREATE")
client.set_dynamic_values({
    "ORG_ID": "123",
    "access_token": "abc123",
    # ... otras variables
})
```

## 📞 Soporte

Si encuentras problemas:

1. ✅ Ejecuta `verify_servicios_externos.sql` para diagnosticar
2. ✅ Revisa logs de la aplicación Python
3. ✅ Verifica que las variables dinámicas se pasan correctamente
4. ✅ Confirma que la tabla tiene la estructura correcta

---

**Fecha de creación**: 30 de enero de 2026
**Versión**: 1.0
**Autor**: Sistema de Servicios Externos</content>
<parameter name="filePath">c:\Users\Usuario\Documents\webhook para flujo ISA2.0\README_servicios_externos.md
