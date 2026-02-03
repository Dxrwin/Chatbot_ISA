-- ========================================================================================
-- SCRIPT PARA CORREGIR EL BODY DE KUENTA_PAYABLE_CREATE
-- Ejecutar si el body en BD no está configurado correctamente
-- ========================================================================================
-- Verificar el body actual
SELECT codigo,
    JSON_PRETTY(body) as body_actual
FROM servicios_externos
WHERE codigo = 'KUENTA_PAYABLE_CREATE';
-- Corregir el body con placeholders correctos
UPDATE servicios_externos
SET body = JSON_OBJECT(
        'creditLineId',
        '{creditLineId}',
        'principal',
        '{principal}',
        'time',
        '{time}',
        'paymentFrequency',
        '{paymentFrequency}',
        'initialFee',
        '{initialFee}',
        'disbursementMethod',
        '{disbursementMethod}',
        'source',
        '{source}',
        'redirectUrl',
        '{redirectUrl}',
        'callbackUrl',
        '{callbackUrl}',
        'meta',
        '{meta}'
    ),
    timeout_ms = 15000,
    reintentos = 3
WHERE codigo = 'KUENTA_PAYABLE_CREATE';
-- Verificar la corrección
SELECT codigo,
    JSON_PRETTY(body) as body_corregido,
    timeout_ms,
    reintentos
FROM servicios_externos
WHERE codigo = 'KUENTA_PAYABLE_CREATE';
-- ========================================================================================
-- ALTERNATIVA: Si prefieres body vacío en BD y usar set_body()
-- ========================================================================================
/*
 -- Opción alternativa: body vacío en BD
 UPDATE servicios_externos SET
 body = JSON_OBJECT(),
 timeout_ms = 15000,
 reintentos = 3
 WHERE codigo = 'KUENTA_PAYABLE_CREATE';
 
 -- Y en el código usar:
 # ext_client_post.set_body(new_payload)
 # en lugar de set_dynamic_values()
 */