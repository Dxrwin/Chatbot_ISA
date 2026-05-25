import hashlib
from decimal import Decimal
from typing import Any, Dict

from pagos.configuracion import obtener_configuracion_payvalida
from pagos.constantes import EstadosOrdenPago


class ServicioPayvalida:
    """
    Servicio encargado de reglas específicas de Payválida:
    - Checksums.
    - Payloads.
    - Mapeo de estados.
    """

    def __init__(self):
        self.configuracion = obtener_configuracion_payvalida()

    def generar_sha512(self, valor: str) -> str:
        """
        Genera hash SHA512 en mayúsculas.
        """
        return hashlib.sha512(valor.encode("utf-8")).hexdigest().upper()

    def generar_sha256(self, valor: str) -> str:
        """
        Genera hash SHA256 en mayúsculas.
        """
        return hashlib.sha256(valor.encode("utf-8")).hexdigest().upper()

    def formatear_monto_payvalida(self, monto: Decimal) -> str:
        """
        Convierte el monto al formato esperado por Payválida.

        Si el valor no tiene decimales, se envía sin .00.
        """
        decimal_monto = Decimal(str(monto))
        if decimal_monto == decimal_monto.to_integral():
            return str(int(decimal_monto))

        return str(decimal_monto)

    def generar_checksum_creacion(
        self,
        correo: str,
        codigo_pais: int,
        codigo_orden_interno: str,
        moneda: str,
        monto: str,
    ) -> str:
        """
        Checksum para crear orden:
        SHA512(email + country + order + money + amount + FIXED_HASH)
        """
        cadena = (
            f"{correo}"
            f"{codigo_pais}"
            f"{codigo_orden_interno}"
            f"{moneda}"
            f"{monto}"
            f"{self.configuracion.fixed_hash}"
        )
        return self.generar_sha512(cadena)

    def generar_checksum_consulta(self, codigo_orden_interno: str) -> str:
        """
        Checksum para consultar orden:
        SHA512(order + merchant + FIXED_HASH)
        """
        cadena = (
            f"{codigo_orden_interno}"
            f"{self.configuracion.merchant}"
            f"{self.configuracion.fixed_hash}"
        )
        return self.generar_sha512(cadena)

    def generar_checksum_notificacion(self, codigo_orden_interno: str, estado: str) -> str:
        """
        Checksum para validar webhook:
        SHA256(po_id + status + NOTIFICATION_HASH)
        """
        cadena = (
            f"{codigo_orden_interno}"
            f"{estado}"
            f"{self.configuracion.notification_hash}"
        )
        return self.generar_sha256(cadena)

    def construir_payload_creacion_orden(self, orden: Dict[str, Any]) -> Dict[str, Any]:
        """
        Construye el JSON para crear una orden en Payválida.
        """
        monto = self.formatear_monto_payvalida(orden["monto"])
        checksum = self.generar_checksum_creacion(
            correo=orden["correo_cliente"],
            codigo_pais=orden["codigo_pais"],
            codigo_orden_interno=orden["codigo_orden_interno"],
            moneda=orden["moneda"],
            monto=monto,
        )

        return {
            "merchant": self.configuracion.merchant,
            "email": orden["correo_cliente"],
            "country": orden["codigo_pais"],
            "order": orden["codigo_orden_interno"],
            "reference": orden["referencia_externa"],
            "money": orden["moneda"],
            "amount": monto,
            "description": orden["descripcion"],
            "method": orden.get("metodo_pago_solicitado") or "",
            "language": "es",
            "recurrent": bool(orden.get("recurrente")),
            "expiration": orden["fecha_expiracion_payvalida"],
            "iva": orden.get("iva", "0"),
            "checksum": checksum,
            "user_di": orden.get("numero_documento_cliente"),
            "user_type_di": orden.get("tipo_documento_cliente"),
            "user_name": orden.get("nombre_cliente"),
            "shortener": True,
        }

    def mapear_estado_payvalida(self, estado_payvalida: str) -> str:
        """
        Convierte estados de Payválida a estados internos.
        """
        if not estado_payvalida:
            return EstadosOrdenPago.DESCONOCIDA

        estado = estado_payvalida.upper().strip()

        mapa = {
            "PENDIENTE": EstadosOrdenPago.PENDIENTE,
            "APROBADA": EstadosOrdenPago.APROBADA,
            "VENCIDA": EstadosOrdenPago.VENCIDA,
            "CANCELADA": EstadosOrdenPago.CANCELADA,
            "ANULADA": EstadosOrdenPago.ANULADA,
            "APPROVED": EstadosOrdenPago.APROBADA,
            "CANCELLED": EstadosOrdenPago.CANCELADA,
        }

        return mapa.get(estado, EstadosOrdenPago.DESCONOCIDA)
