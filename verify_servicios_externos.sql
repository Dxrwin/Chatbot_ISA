-- ========================================================================================
-- SCRIPT DE VERIFICACIÓN DE SERVICIOS EXTERNOS
-- Ejecutar después de aplicar update_servicios_externos.sql
-- ========================================================================================
-- ===== VERIFICACIÓN GENERAL =====
-- Contar total de servicios
SELECT COUNT(*) as total_servicios,
    SUM(activo = 1) as servicios_activos,
    SUM(activo = 0) as servicios_inactivos
FROM servicios_externos;
-- Verificar servicios específicos con variables dinámicas
SELECT codigo,
    nombre_servicio,
    metodo,
    url,
    JSON_PRETTY(header) as header_configurado,
    JSON_PRETTY(body) as body_configurado,
    timeout_ms,
    reintentos,
    activo
FROM servicios_externos
WHERE codigo IN (
        'AUTH_TOKEN',
        'KUENTA_LINES_LIST',
        'KUENTA_TOTP_APPROVE',
        'KUENTA_ORDER_STATUS',
        'KUENTA_PAYABLE_GET',
        'KUENTA_PAYABLE_CREATE',
        'KUENTA_PAYABLE_CONFIRM',
        'KUENTA_PRODUCT_GET'
    )
ORDER BY codigo;
-- ===== VERIFICACIÓN DE VARIABLES DINÁMICAS =====
-- Verificar que los headers contienen variables dinámicas
SELECT codigo,
    nombre_servicio,
    JSON_EXTRACT(header, '$.Authorization') as authorization_header,
    JSON_EXTRACT(header, '$.Config-Organization-ID') as org_id_header,
    JSON_EXTRACT(header, '$.Organization-ID') as organization_id_header
FROM servicios_externos
WHERE codigo LIKE 'KUENTA_%'
ORDER BY codigo;
-- Verificar servicios con body dinámico
SELECT codigo,
    nombre_servicio,
    JSON_PRETTY(body) as body_dinamico,
    JSON_LENGTH(body) as numero_campos_body
FROM servicios_externos
WHERE JSON_LENGTH(body) > 0
ORDER BY codigo;
-- ===== VERIFICACIÓN DE CONFIGURACIÓN POR SERVICIO =====
-- AUTH_TOKEN
SELECT 'AUTH_TOKEN' as servicio,
    url,
    metodo,
    JSON_PRETTY(header) as headers,
    JSON_PRETTY(body) as body
FROM servicios_externos
WHERE codigo = 'AUTH_TOKEN';
-- KUENTA_PAYABLE_CREATE (debe tener body completo)
SELECT 'KUENTA_PAYABLE_CREATE' as servicio,
    url,
    metodo,
    JSON_PRETTY(header) as headers,
    JSON_PRETTY(body) as body_dinamico
FROM servicios_externos
WHERE codigo = 'KUENTA_PAYABLE_CREATE';
-- KUENTA_PAYABLE_CONFIRM (nuevo servicio PATCH)
SELECT 'KUENTA_PAYABLE_CONFIRM' as servicio,
    url,
    metodo,
    JSON_PRETTY(header) as headers,
    JSON_PRETTY(body) as body
FROM servicios_externos
WHERE codigo = 'KUENTA_PAYABLE_CONFIRM';
-- KUENTA_TOTP_APPROVE (debe tener totp en body)
SELECT 'KUENTA_TOTP_APPROVE' as servicio,
    url,
    metodo,
    JSON_PRETTY(header) as headers,
    JSON_EXTRACT(body, '$.totp') as totp_variable
FROM servicios_externos
WHERE codigo = 'KUENTA_TOTP_APPROVE';
-- ===== VERIFICACIÓN DE INTEGRIDAD =====
-- Verificar que no hay servicios duplicados
SELECT codigo,
    COUNT(*) as cantidad
FROM servicios_externos
GROUP BY codigo
HAVING COUNT(*) > 1;
-- Verificar que todos los servicios tienen headers válidos
SELECT codigo,
    nombre_servicio,
    JSON_VALID(header) as header_valido,
    JSON_VALID(body) as body_valido
FROM servicios_externos
WHERE NOT (
        JSON_VALID(header)
        AND JSON_VALID(body)
    );
-- Verificar configuración de timeout y reintentos
SELECT codigo,
    nombre_servicio,
    metodo,
    timeout_ms,
    reintentos,
    CASE
        WHEN metodo = 'GET'
        AND timeout_ms >= 10000 THEN 'OK'
        WHEN metodo IN ('POST', 'PUT', 'PATCH')
        AND timeout_ms >= 15000 THEN 'OK'
        ELSE 'REVISAR'
    END as configuracion_timeout
FROM servicios_externos
WHERE activo = 1
ORDER BY metodo,
    codigo;
-- ===== RESUMEN FINAL =====
SELECT 'RESUMEN DE CONFIGURACIÓN' as titulo,
    COUNT(*) as total_servicios_configurados,
    SUM(JSON_LENGTH(header) > 0) as servicios_con_headers,
    SUM(JSON_LENGTH(body) > 0) as servicios_con_body_dinamico,
    SUM(activo = 1) as servicios_activos
FROM servicios_externos
WHERE codigo LIKE 'KUENTA_%'
    OR codigo = 'AUTH_TOKEN';
-- ========================================================================================
-- FIN DE LA VERIFICACIÓN
-- ========================================================================================