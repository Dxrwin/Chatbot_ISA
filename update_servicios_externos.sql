-- ========================================================================================
-- SCRIPT COMPLETO PARA ACTUALIZAR SERVICIOS EXTERNOS CON VARIABLES DINÁMICAS
-- Fecha: 30 de enero de 2026
-- Descripción: Actualiza la tabla servicios_externos para usar variables dinámicas
--              en headers y body, eliminando duplicación por método HTTP
-- ========================================================================================
-- ===== VERIFICAR ESTRUCTURA DE LA TABLA =====
-- Asegurarse de que la tabla tiene las columnas necesarias
DESCRIBE servicios_externos;
-- ===== ACTUALIZAR REGISTROS EXISTENTES =====
-- 1. AUTH TOKEN (Sin cambios, pero mejorar estructura)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json'
    ),
    body = JSON_OBJECT(),
    nombre_servicio = 'Kuenta - Auth Token'
WHERE codigo = 'AUTH_TOKEN';
-- 2. KUENTA_LINES_LIST (Agregar headers dinámicos)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(),
    nombre_servicio = 'Kuenta - Listar Líneas de Producto'
WHERE codigo = 'KUENTA_LINES_LIST';
-- 3. KUENTA_TOTP_APPROVE (Agregar headers y body dinámico)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT('totp', '{codigo_totp}'),
    nombre_servicio = 'Kuenta - TOTP Approve'
WHERE codigo = 'KUENTA_TOTP_APPROVE';
-- 4. KUENTA_ORDER_STATUS (Agregar headers dinámicos)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(),
    nombre_servicio = 'Kuenta - Estado de Orden'
WHERE codigo = 'KUENTA_ORDER_STATUS';
-- 5. KUENTA_PAYABLE_GET (Agregar headers dinámicos)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(),
    nombre_servicio = 'Kuenta - Consultar Payable'
WHERE codigo = 'KUENTA_PAYABLE_GET';
-- 6. KUENTA_PAYABLE_CREATE (Agregar headers Y body dinámico completo)
UPDATE servicios_externos
SET header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(
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
    nombre_servicio = 'Kuenta - Crear Payable',
    timeout_ms = 15000,
    reintentos = 3
WHERE codigo = 'KUENTA_PAYABLE_CREATE';
-- ===== INSERTAR NUEVOS REGISTROS (si faltan) =====
-- KUENTA_PAYABLE_CONFIRM (Para el nuevo endpoint PATCH)
INSERT INTO servicios_externos (
        nombre_servicio,
        codigo,
        url,
        metodo,
        timeout_ms,
        reintentos,
        activo,
        header,
        body
    )
VALUES (
        'Kuenta - Confirmar Payable',
        'KUENTA_PAYABLE_CONFIRM',
        'https://api.kuenta.co/v1/payables/{credit_id}/confirm',
        'PATCH',
        15000,
        3,
        1,
        JSON_OBJECT(
            'Content-Type',
            'application/json',
            'Config-Organization-ID',
            '{ORG_ID}',
            'Organization-ID',
            '{ORG_ID}',
            'Authorization',
            '{access_token}'
        ),
        JSON_OBJECT()
    ) ON DUPLICATE KEY
UPDATE header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(),
    timeout_ms = 15000,
    reintentos = 3,
    activo = 1;
-- KUENTA_PRODUCT_GET (Para obtener producto por ID en calcular_financiamiento)
INSERT INTO servicios_externos (
        nombre_servicio,
        codigo,
        url,
        metodo,
        timeout_ms,
        reintentos,
        activo,
        header,
        body
    )
VALUES (
        'Kuenta - Obtener Producto',
        'KUENTA_PRODUCT_GET',
        'https://api.kuenta.co/v1/products/{linea_producto}',
        'GET',
        15000,
        2,
        1,
        JSON_OBJECT(
            'Content-Type',
            'application/json',
            'Config-Organization-ID',
            '{ORG_ID}',
            'Organization-ID',
            '{ORG_ID}',
            'Authorization',
            '{access_token}'
        ),
        JSON_OBJECT()
    ) ON DUPLICATE KEY
UPDATE header = JSON_OBJECT(
        'Content-Type',
        'application/json',
        'Config-Organization-ID',
        '{ORG_ID}',
        'Organization-ID',
        '{ORG_ID}',
        'Authorization',
        '{access_token}'
    ),
    body = JSON_OBJECT(),
    timeout_ms = 15000,
    reintentos = 2,
    activo = 1;
-- ===== VERIFICACIÓN DE CAMBIOS =====
-- Verificar que todos los servicios estén actualizados
SELECT id,
    nombre_servicio,
    codigo,
    url,
    metodo,
    timeout_ms,
    reintentos,
    activo,
    JSON_PRETTY(header) as header,
    JSON_PRETTY(body) as body,
    creado_en,
    actualizado_en
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
-- Contar servicios activos
SELECT COUNT(*) as total_servicios,
    SUM(activo = 1) as servicios_activos,
    SUM(activo = 0) as servicios_inactivos
FROM servicios_externos;
-- ===== SCRIPT DE ROLLBACK (en caso de necesitar revertir) =====
/*
 -- ROLLBACK: Restaurar configuración anterior (si es necesario)
 -- Este script revierte los cambios a configuración básica sin variables dinámicas
 
 UPDATE servicios_externos SET
 header = JSON_OBJECT('Content-Type', 'application/json'),
 body = JSON_OBJECT()
 WHERE codigo IN ('KUENTA_LINES_LIST', 'KUENTA_TOTP_APPROVE', 'KUENTA_ORDER_STATUS', 'KUENTA_PAYABLE_GET');
 
 UPDATE servicios_externos SET
 header = JSON_OBJECT('Content-Type', 'application/json'),
 body = JSON_OBJECT()
 WHERE codigo = 'KUENTA_PAYABLE_CREATE';
 
 DELETE FROM servicios_externos
 WHERE codigo IN ('KUENTA_PAYABLE_CONFIRM', 'KUENTA_PRODUCT_GET');
 */
-- ========================================================================================
-- FIN DEL SCRIPT
-- ========================================================================================