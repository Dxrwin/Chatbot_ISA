-- ========================================================================================
-- SCRIPT PARA CREAR LA TABLA servicios_externos (SI NO EXISTE)
-- Ejecutar este script SOLO si la tabla no existe en la base de datos
-- ========================================================================================
CREATE TABLE IF NOT EXISTS `servicios_externos` (
    `id` int(11) NOT NULL AUTO_INCREMENT,
    `nombre_servicio` varchar(255) NOT NULL COMMENT 'Nombre descriptivo del servicio',
    `codigo` varchar(100) NOT NULL COMMENT 'Código único para identificar el servicio',
    `url` varchar(500) NOT NULL COMMENT 'URL base del servicio (puede contener placeholders)',
    `metodo` enum('GET', 'POST', 'PUT', 'PATCH', 'DELETE') NOT NULL COMMENT 'Método HTTP',
    `timeout_ms` int(11) NOT NULL DEFAULT '10000' COMMENT 'Timeout en milisegundos',
    `reintentos` int(11) NOT NULL DEFAULT '0' COMMENT 'Número de reintentos automáticos',
    `activo` tinyint(1) NOT NULL DEFAULT '1' COMMENT 'Si el servicio está activo',
    `header` json DEFAULT NULL COMMENT 'Headers HTTP en formato JSON (pueden contener placeholders)',
    `body` json DEFAULT NULL COMMENT 'Body de la petición en formato JSON (pueden contener placeholders)',
    `creado_en` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `actualizado_en` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `codigo` (`codigo`),
    KEY `activo` (`activo`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'Tabla de configuración de servicios externos con variables dinámicas';
-- ========================================================================================
-- ÍNDICES ADICIONALES PARA MEJOR PERFORMANCE
-- ========================================================================================
-- Índice para búsquedas por código y estado activo
CREATE INDEX IF NOT EXISTS idx_servicios_codigo_activo ON servicios_externos (codigo, activo);
-- Índice para filtrar servicios activos
CREATE INDEX IF NOT EXISTS idx_servicios_activo ON servicios_externos (activo);
-- ========================================================================================
-- VERIFICACIÓN DE LA TABLA
-- ========================================================================================
-- Verificar estructura de la tabla
DESCRIBE servicios_externos;
-- Verificar índices
SHOW INDEX
FROM servicios_externos;