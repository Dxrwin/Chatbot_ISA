from fastapi import APIRouter

from pagos.presentacion.controladores.ordenes_pago_controlador import (
    enrutador as enrutador_ordenes_pago,
)
from pagos.presentacion.controladores.webhook_payvalida_controlador import (
    enrutador as enrutador_webhook_payvalida,
)

from pagos.presentacion.depuracion.enrutador_depuracion_pagos import (
    enrutador_depuracion_pagos,
)

# Enrutador principal del módulo de pagos.
# Todas las rutas quedan agrupadas bajo /api/v1.
enrutador_pagos = APIRouter(prefix="/api/v1")

enrutador_pagos.include_router(enrutador_ordenes_pago)
enrutador_pagos.include_router(enrutador_webhook_payvalida)
enrutador_pagos.include_router(enrutador_depuracion_pagos)
