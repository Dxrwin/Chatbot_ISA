from fastapi import APIRouter

from pagos.presentacion.depuracion.controlador_auditoria_pagos import (
    enrutador as enrutador_auditoria,
)
from pagos.presentacion.depuracion.controlador_control_pagos import (
    enrutador as enrutador_control,
)
from pagos.presentacion.depuracion.controlador_diagnostico_pagos import (
    enrutador as enrutador_diagnostico,
)


enrutador_depuracion_pagos = APIRouter(
    prefix="/depuracion/pagos",
    tags=["Depuración, control y auditoría de pagos"],
)

enrutador_depuracion_pagos.include_router(enrutador_auditoria)
enrutador_depuracion_pagos.include_router(enrutador_control)
enrutador_depuracion_pagos.include_router(enrutador_diagnostico)