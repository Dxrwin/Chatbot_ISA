import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo
from pagos.utilidades.fechas import normalizar_fecha_payvalida


class ErrorValidacionPayvalida(Exception):
    """
    Error de validación local antes de llamar a Payválida.

    Este error debe transformarse en HTTP 422 desde el controlador.
    """

    def __init__(
        self,
        mensaje: str,
        campo: str,
        valor: object,
        regla: str,
        codigo: str = "VALIDACION_PAYVALIDA",
    ):
        self.codigo = codigo
        self.mensaje = mensaje
        self.campo = campo
        self.valor = convertir_valor_seguro(valor)
        self.regla = regla
        super().__init__(mensaje)

    def a_respuesta(self) -> dict:
        """
        Retorna un dict seguro para JSONResponse / HTTPException.
        """
        return {
            "codigo": self.codigo,
            "mensaje": self.mensaje,
            "campo": self.campo,
            "valor_recibido": self.valor,
            "regla": self.regla,
        }


def convertir_valor_seguro(valor: Any) -> Any:
    """
    Convierte valores no serializables a tipos seguros para JSON.
    """
    if isinstance(valor, Decimal):
        return str(valor)

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    if isinstance(valor, dict):
        return {clave: convertir_valor_seguro(valor_item) for clave, valor_item in valor.items()}

    if isinstance(valor, list):
        return [convertir_valor_seguro(item) for item in valor]

    return valor


def validar_correo_cliente(correo: Optional[str]) -> None:
    """
    Valida que exista un correo de cliente con formato básico válido.

    Aunque Payválida recibe el correo, también se requiere internamente
    para trazabilidad, notificaciones futuras y datos personales completos.
    """
    correo = str(correo or "").strip()

    if not correo:
        raise ErrorValidacionPayvalida(
            mensaje="El correo del cliente es obligatorio.",
            campo="cliente.correo",
            valor=correo,
            regla="Debe enviar un correo electrónico válido.",
        )

    patron = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"

    if not re.fullmatch(patron, correo):
        raise ErrorValidacionPayvalida(
            mensaje="El correo del cliente no tiene un formato válido.",
            campo="cliente.correo",
            valor=correo,
            regla="Debe tener formato correo@dominio.com.",
        )


def validar_referencia_payvalida_colombia(referencia: str) -> None:
    """
    Valida la referencia que se enviará a Payválida para Colombia.

    Reglas:
    - Solo números.
    - No inicia en cero.
    - Sin letras.
    - Sin espacios.
    - Sin caracteres especiales.
    - Máximo 15 dígitos.
    """
    referencia = str(referencia or "").strip()

    if not re.fullmatch(r"[1-9][0-9]{0,14}", referencia):
        raise ErrorValidacionPayvalida(
            mensaje="Referencia inválida para Payválida Colombia.",
            campo="referencia_externa",
            valor=referencia,
            regla=(
                "Debe ser numérica, no iniciar en cero, no contener letras, "
                "no contener espacios, no contener caracteres especiales y tener máximo 15 dígitos."
            ),
        )


def validar_pais_y_moneda(codigo_pais: Any, moneda: Any) -> None:
    """
    Valida país y moneda soportados por este módulo en la primera versión.

    Actualmente el flujo está diseñado para Colombia con COP.
    """
    try:
        codigo_pais_entero = int(codigo_pais)
    except Exception:
        raise ErrorValidacionPayvalida(
            mensaje="Código de país inválido.",
            campo="pago.codigo_pais",
            valor=codigo_pais,
            regla="Debe ser numérico. Para Colombia debe ser 343.",
        )

    moneda_normalizada = str(moneda or "").strip().upper()

    if codigo_pais_entero != 343:
        raise ErrorValidacionPayvalida(
            mensaje="Código de país no soportado por el módulo de pagos.",
            campo="pago.codigo_pais",
            valor=codigo_pais,
            regla="Para esta integración inicial solo se permite Colombia: 343.",
        )

    if moneda_normalizada != "COP":
        raise ErrorValidacionPayvalida(
            mensaje="Moneda no soportada para Colombia.",
            campo="pago.moneda",
            valor=moneda,
            regla="Para Colombia solo se permite COP.",
        )


def validar_descripcion_payvalida(descripcion: Optional[str]) -> None:
    """
    Valida la descripción antes de enviarla a Payválida.

    Se rechazan tildes y caracteres especiales para evitar rechazos del proveedor.
    """
    descripcion = str(descripcion or "").strip()

    if not descripcion:
        raise ErrorValidacionPayvalida(
            mensaje="La descripción no puede estar vacía.",
            campo="pago.descripcion",
            valor=descripcion,
            regla="Debe contener texto y no incluir caracteres especiales.",
        )

    if not re.fullmatch(r"[A-Za-z0-9 ]{1,255}", descripcion):
        raise ErrorValidacionPayvalida(
            mensaje="Descripción inválida para Payválida.",
            campo="pago.descripcion",
            valor=descripcion,
            regla="Use solo letras sin tilde, números y espacios. No use caracteres especiales.",
        )


def validar_fecha_expiracion_payvalida(fecha_expiracion: Optional[str]) -> None:
    """
    Valida fecha de expiración en formato DD/MM/YYYY.

    Regla de negocio:
    - No puede estar vacía.
    - Debe tener formato DD/MM/YYYY.
    - Debe ser igual o posterior a hoy.
    - No debe superar 30 días calendario.
    """
    fecha_texto = normalizar_fecha_payvalida(fecha_expiracion) or ""

    if not fecha_texto:
        raise ErrorValidacionPayvalida(
            mensaje="La fecha de expiración es obligatoria.",
            campo="pago.fecha_expiracion",
            valor=fecha_expiracion,
            regla="Debe tener formato DD/MM/YYYY.",
        )

    try:
        fecha = datetime.strptime(fecha_texto, "%d/%m/%Y").date()
    except ValueError:
        raise ErrorValidacionPayvalida(
            mensaje="Fecha de expiración inválida.",
            campo="pago.fecha_expiracion",
            valor=fecha_expiracion,
            regla="Debe tener formato DD/MM/YYYY. Ejemplo: 30/05/2026.",
        )

    hoy = datetime.now(ZoneInfo("America/Bogota")).date()

    if fecha < hoy:
        raise ErrorValidacionPayvalida(
            mensaje="La fecha de expiración no puede ser anterior a hoy.",
            campo="pago.fecha_expiracion",
            valor=fecha_expiracion,
            regla="Debe ser igual o posterior a la fecha actual.",
        )

    if fecha > hoy + timedelta(days=30):
        raise ErrorValidacionPayvalida(
            mensaje="La fecha de expiración no puede superar 30 días calendario.",
            campo="pago.fecha_expiracion",
            valor=fecha_expiracion,
            regla="Debe estar dentro de los próximos 30 días.",
        )


def validar_monto_payvalida(monto: Any) -> None:
    """
    Valida el monto antes de llamar a Payválida.

    Aunque Payválida permite monto 0 para monto abierto, este módulo lo rechaza
    porque las órdenes internas deben tener un valor cerrado.
    """
    try:
        monto_decimal = Decimal(str(monto))
    except (InvalidOperation, ValueError):
        raise ErrorValidacionPayvalida(
            mensaje="Monto inválido.",
            campo="pago.monto",
            valor=monto,
            regla="Debe ser numérico.",
        )

    if monto_decimal <= 0:
        raise ErrorValidacionPayvalida(
            mensaje="Monto inválido.",
            campo="pago.monto",
            valor=monto,
            regla="Debe ser mayor a cero.",
        )

    if abs(monto_decimal.as_tuple().exponent) > 2:
        raise ErrorValidacionPayvalida(
            mensaje="Monto inválido.",
            campo="pago.monto",
            valor=monto,
            regla="No debe tener más de dos decimales.",
        )


def obtener_metodos_pago_permitidos() -> list[str]:
    """
    Obtiene métodos permitidos desde variable de entorno.

    Si no se configura PAYVALIDA_METODOS_PERMITIDOS, solo se permite vacío,
    para que Payválida muestre los métodos disponibles del comercio.
    """
    valor = os.getenv("PAYVALIDA_METODOS_PERMITIDOS", "").strip()

    if not valor:
        return []

    return [
        metodo.strip().upper()
        for metodo in valor.split(",")
        if metodo.strip()
    ]


def validar_metodo_pago(metodo_pago: Optional[str]) -> None:
    """
    Valida método de pago antes de llamar a Payválida.

    Regla:
    - Vacío es permitido.
    - Si se envía un método, debe estar configurado en PAYVALIDA_METODOS_PERMITIDOS.
    """
    metodo = str(metodo_pago or "").strip().upper()

    if metodo == "":
        return

    metodos_permitidos = obtener_metodos_pago_permitidos()

    if metodo not in metodos_permitidos:
        raise ErrorValidacionPayvalida(
            mensaje="Método de pago no permitido o no configurado para el comercio.",
            campo="pago.metodo_pago",
            valor=metodo_pago,
            regla=(
                "Envíe vacío para permitir los métodos disponibles del comercio, "
                "o configure PAYVALIDA_METODOS_PERMITIDOS con los métodos autorizados."
            ),
        )


def validar_payload_creacion_antes_de_payvalida(solicitud) -> None:
    """
    Ejecuta todas las validaciones locales antes de crear la orden en Payválida.

    Esta función debe ejecutarse antes de:
    - Crear orden local.
    - Insertar en ordenes_pago.
    - Insertar en solicitudes_proveedor_pago.
    - Llamar a Payválida.
    """
    validar_correo_cliente(solicitud.cliente.correo)
    validar_referencia_payvalida_colombia(solicitud.referencia_externa)
    validar_pais_y_moneda(solicitud.pago.codigo_pais, solicitud.pago.moneda)
    validar_descripcion_payvalida(solicitud.pago.descripcion)
    validar_fecha_expiracion_payvalida(solicitud.pago.fecha_expiracion)
    validar_monto_payvalida(solicitud.pago.monto)
    validar_metodo_pago(solicitud.pago.metodo_pago)
