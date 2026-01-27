CREATE TABLE servicios_externos (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    nombre_servicio VARCHAR(100) NOT NULL,
    codigo          VARCHAR(50)  NOT NULL UNIQUE,
    url             VARCHAR(500) NOT NULL,
    metodo          VARCHAR(10)  NOT NULL,
    timeout_ms      INT          NOT NULL DEFAULT 10000,
    reintentos      INT          NOT NULL DEFAULT 0,
    activo          TINYINT(1)   NOT NULL DEFAULT 1,
    header          JSON         NULL,
    body            JSON         NULL,
    creado_en       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- If the table already exists, apply these changes manually:
-- ALTER TABLE servicios_externos ADD COLUMN header JSON NULL;
-- ALTER TABLE servicios_externos ADD COLUMN body JSON NULL;
