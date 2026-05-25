class ExcepcionPago(Exception):
    """
    Excepción base para errores del módulo de pagos.
    """


class ErrorValidacionPago(ExcepcionPago):
    """
    Error causado por datos inválidos recibidos en una petición.
    """


class ErrorProveedorPago(ExcepcionPago):
    """
    Error causado por una respuesta fallida o inválida del proveedor de pagos.
    """


class ErrorOrdenNoEncontrada(ExcepcionPago):
    """
    Error cuando no se encuentra una orden de pago local.
    """


class ErrorWebhookPayvalida(ExcepcionPago):
    """
    Error procesando una notificación webhook de Payválida.
    """
