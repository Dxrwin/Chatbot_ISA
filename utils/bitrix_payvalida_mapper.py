import hashlib
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from pagos.esquemas.esquema_orden_pago import SolicitudCrearOrdenPago
from pagos.utilidades.fechas import normalizar_fecha_payvalida

SISTEMA_ORIGEN_BITRIX_CARTERA = "bitrix_llamadas_cartera"
DESCRIPCION_PAGO_CARTERA = "Pago cartera libranza"


def limpiar_texto_pago(valor: Any) -> Optional[str]:
    if valor is None:
        return None

    texto = str(valor).strip()
    return texto or None


def correo_valido(valor: Any) -> bool:
    texto = limpiar_texto_pago(valor)
    if not texto:
        return False
    return re.fullmatch(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", texto) is not None


def limpiar_correo_pago(valor: Any) -> Optional[str]:
    texto = limpiar_texto_pago(valor)
    if not texto:
        return None

    texto = texto.lower()
    return texto if correo_valido(texto) else None


def normalizar_descripcion_payvalida(valor: str) -> str:
    texto = unicodedata.normalize("NFD", valor)
    texto = "".join(caracter for caracter in texto if unicodedata.category(caracter) != "Mn")
    texto = re.sub(r"[^A-Za-z0-9 ]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def obtener_variable_entrada(variables_entrada: Dict[str, Any], nombre: str) -> Any:
    objetivo = nombre.strip().lower()
    for clave, valor in variables_entrada.items():
        if str(clave).strip().lower() == objetivo:
            return valor
    return None


def obtener_id_deal(deal_result: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(deal_result, dict):
        return None

    item = deal_result.get("item") if isinstance(deal_result.get("item"), dict) else deal_result
    id_deal = item.get("id") or item.get("ID")

    try:
        return int(id_deal)
    except (TypeError, ValueError):
        return None


def obtener_item_deal(deal_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(deal_result, dict):
        return {}

    item = deal_result.get("item")
    return item if isinstance(item, dict) else deal_result


def generar_referencia_externa_bitrix(cedula: Any, id_deal: int) -> tuple[str, Optional[str]]:
    cedula_digitos = re.sub(r"\D", "", str(cedula or ""))
    ultimos_cedula = cedula_digitos[-3:].zfill(3)
    fecha = datetime.now(ZoneInfo("America/Bogota")).strftime("%d%m%y")
    deal = str(id_deal)[-6:].zfill(6)
    referencia_original = f"{ultimos_cedula}{fecha}{deal}"
    referencia = referencia_original[-15:]

    if referencia.startswith("0"):
        referencia = f"1{referencia[1:]}"

    return referencia, referencia_original if referencia != referencia_original else None


def generar_referencia_externa_sin_deal(
    cedula: Any,
    id_libranza: Any,
    monto: Decimal,
    fecha_acuerdo_pago: Any,
) -> tuple[str, Optional[str]]:
    """
    Genera una referencia numerica estable sin depender del ID del deal Bitrix.
    Mantiene maximo 15 digitos y evita iniciar en cero.
    """
    cedula_digitos = re.sub(r"\D", "", str(cedula or ""))
    ultimos_cedula = cedula_digitos[-3:].zfill(3)
    fecha = datetime.now(ZoneInfo("America/Bogota")).strftime("%d%m%y")
    base = "|".join(
        [
            cedula_digitos,
            re.sub(r"\s+", "", str(id_libranza or "")).upper(),
            formatear_monto_clave(monto),
            formatear_fecha_clave(fecha_acuerdo_pago),
        ]
    )
    hash_numerico = str(int(hashlib.sha256(base.encode("utf-8")).hexdigest(), 16))[-6:].zfill(6)
    referencia_original = f"{ultimos_cedula}{fecha}{hash_numerico}"
    referencia = referencia_original[-15:]

    if referencia.startswith("0"):
        referencia = f"1{referencia[1:]}"

    return referencia, referencia_original if referencia != referencia_original else None


def formatear_monto_clave(monto: Decimal) -> str:
    texto = format(monto.normalize(), "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def formatear_fecha_clave(valor: Any) -> str:
    texto = limpiar_texto_pago(valor) or ""
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).strftime("%d%m%Y")
        except ValueError:
            continue
    return re.sub(r"\D", "", texto)


def construir_clave_pago_bitrix(
    sistema_origen: str,
    cedula: Any,
    id_libranza: Any,
    monto: Decimal,
    fecha_acuerdo_pago: Any,
) -> str:
    cedula_normalizada = re.sub(r"\D", "", str(cedula or ""))
    libranza_normalizada = re.sub(r"\s+", "", str(id_libranza or "")).upper()
    return ":".join(
        [
            sistema_origen,
            cedula_normalizada,
            libranza_normalizada,
            formatear_monto_clave(monto),
            formatear_fecha_clave(fecha_acuerdo_pago),
        ]
    )


def extraer_monto_pactado(output_vars: Dict[str, Any], input_variables: Dict[str, Any]) -> Decimal:
    valor = (
        output_vars.get("valor_confirmado")
        or output_vars.get("valorconfirmado")
        or obtener_variable_entrada(input_variables, "VALOR_CONFIRMADO")
    )

    monto = convertir_monto_a_decimal(valor)
    if monto is None:
        raise ValueError("No se encontró valor_confirmado válido para crear la orden Payvalida.")

    return monto


def obtener_valor_confirmado_original(
    output_vars: Dict[str, Any],
    input_variables: Dict[str, Any],
) -> Optional[str]:
    """
    Conserva el valor como llego desde el agente/input para trazabilidad visual.

    El monto operativo se normaliza por separado para Payvalida.
    """
    valor = (
        output_vars.get("valor_confirmado")
        or output_vars.get("valorconfirmado")
        or output_vars.get("valor_a_pagar")
        or obtener_variable_entrada(input_variables, "VALOR_CONFIRMADO")
    )
    return limpiar_texto_pago(valor)


def convertir_monto_a_decimal(valor: Any) -> Optional[Decimal]:
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    texto = re.sub(r"[^\d,.\-]", "", texto)
    if not texto:
        return None

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes[-1]) <= 2 else texto.replace(",", "")
    elif "." in texto:
        partes = texto.split(".")
        if len(partes) > 2:
            texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) <= 2 else "".join(partes)
        elif len(partes[-1]) > 2:
            texto = texto.replace(".", "")

    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def construir_nombre_cliente(contacto: Dict[str, Any], input_variables: Dict[str, Any]) -> Optional[str]:
    nombre_contacto = " ".join(
        parte
        for parte in [
            limpiar_texto_pago(contacto.get("name")),
            limpiar_texto_pago(contacto.get("lastName") or contacto.get("last_name")),
        ]
        if parte
    ).strip()

    return nombre_contacto or limpiar_texto_pago(obtener_variable_entrada(input_variables, "NOMBRE"))


def construir_solicitud_pago_desde_bitrix(
    input_variables: Dict[str, Any],
    output_vars: Dict[str, Any],
    validation: Dict[str, Any],
    contact: Dict[str, Any],
    deal_result: Optional[Dict[str, Any]] = None,
) -> SolicitudCrearOrdenPago:
    id_deal = obtener_id_deal(deal_result)
    if id_deal is None:
        raise ValueError("No se encontró id de deal Bitrix para generar referencia externa Payvalida.")

    cedula = limpiar_texto_pago(obtener_variable_entrada(input_variables, "CEDULA"))
    referencia_externa, referencia_original = generar_referencia_externa_bitrix(cedula, id_deal)
    item_deal = obtener_item_deal(deal_result)

    correo_input = limpiar_correo_pago(obtener_variable_entrada(input_variables, "CORREO"))
    telefono_input = limpiar_texto_pago(obtener_variable_entrada(input_variables, "TELEFONO"))
    correo_bitrix = limpiar_correo_pago(contact.get("email"))
    telefono_bitrix = limpiar_texto_pago(contact.get("phone"))
    monto = extraer_monto_pactado(output_vars, input_variables)
    valor_confirmado_original = obtener_valor_confirmado_original(output_vars, input_variables)
    fecha_acuerdo_pago = normalizar_fecha_payvalida(
        limpiar_texto_pago(output_vars.get("fechacuerdopago"))
    )
    id_libranza = limpiar_texto_pago(obtener_variable_entrada(input_variables, "ID_LIBRANZA"))
    clave_pago_bitrix = construir_clave_pago_bitrix(
        sistema_origen=SISTEMA_ORIGEN_BITRIX_CARTERA,
        cedula=cedula,
        id_libranza=id_libranza,
        monto=monto,
        fecha_acuerdo_pago=fecha_acuerdo_pago,
    )

    metadatos = {
        "origen": "webhooks_call_completed_bitrix",
        "id_contacto_bitrix": contact.get("id") or contact.get("ID"),
        "id_deal_bitrix": id_deal,
        "categoryId": item_deal.get("categoryId"),
        "stageId": item_deal.get("stageId"),
        "clave_pago_bitrix": clave_pago_bitrix,
        "cedula": cedula,
        "id_libranza": id_libranza,
        "pagaduria": limpiar_texto_pago(obtener_variable_entrada(input_variables, "PAGADURIA")),
        "telefono_input": telefono_input,
        "telefono_bitrix": telefono_bitrix,
        "correo_input": correo_input,
        "correo_bitrix": correo_bitrix,
        "mora_total": obtener_variable_entrada(input_variables, "MORA_TOTAL"),
        "mora": obtener_variable_entrada(input_variables, "MORA"),
        "cuota": obtener_variable_entrada(input_variables, "CUOTA"),
        "valor_confirmado": str(monto),
        "valor_confirmado_original": valor_confirmado_original,
        "valor_confirmado_legible": valor_confirmado_original or str(monto),
        "gestion_final": limpiar_texto_pago(output_vars.get("gestion_final")),
        "motivo_principal": limpiar_texto_pago(output_vars.get("motivo_principal")),
        "interes_pagar": limpiar_texto_pago(output_vars.get("interes_pagar")),
        "pago_hoy": limpiar_texto_pago(output_vars.get("pago_hoy")),
        "fecha_acuerdo_pago": fecha_acuerdo_pago,
        "resumen_llamada": limpiar_texto_pago(output_vars.get("resumenllamada")),
        "detalle_acuerdo": limpiar_texto_pago(output_vars.get("detalle_acuerdo")),
        "validation": validation,
    }

    if referencia_original:
        metadatos["referencia_externa_original"] = referencia_original

    return SolicitudCrearOrdenPago(
        sistema_origen=SISTEMA_ORIGEN_BITRIX_CARTERA,
        referencia_externa=referencia_externa,
        cliente={
            "correo": correo_bitrix or correo_input,
            "tipo_documento": "CC",
            "numero_documento": cedula,
            "nombre": construir_nombre_cliente(contact, input_variables),
            "telefono": telefono_bitrix or telefono_input,
        },
        pago={
            "monto": monto,
            "moneda": "COP",
            "codigo_pais": 343,
            "descripcion": normalizar_descripcion_payvalida(DESCRIPCION_PAGO_CARTERA),
            "fecha_expiracion": fecha_acuerdo_pago,
            "iva": "0",
            "metodo_pago": "",
            "recurrente": False,
        },
        metadatos=metadatos,
    )


def construir_solicitud_pago_desde_bitrix_sin_deal(
    input_variables: Dict[str, Any],
    output_vars: Dict[str, Any],
    validation: Dict[str, Any],
    contact: Dict[str, Any],
) -> SolicitudCrearOrdenPago:
    cedula = limpiar_texto_pago(obtener_variable_entrada(input_variables, "CEDULA"))
    correo_input = limpiar_correo_pago(obtener_variable_entrada(input_variables, "CORREO"))
    telefono_input = limpiar_texto_pago(obtener_variable_entrada(input_variables, "TELEFONO"))
    correo_bitrix = limpiar_correo_pago(contact.get("email"))
    telefono_bitrix = limpiar_texto_pago(contact.get("phone"))
    monto = extraer_monto_pactado(output_vars, input_variables)
    valor_confirmado_original = obtener_valor_confirmado_original(output_vars, input_variables)
    fecha_acuerdo_pago = normalizar_fecha_payvalida(
        limpiar_texto_pago(output_vars.get("fechacuerdopago"))
    )
    id_libranza = limpiar_texto_pago(obtener_variable_entrada(input_variables, "ID_LIBRANZA"))
    referencia_externa, referencia_original = generar_referencia_externa_sin_deal(
        cedula=cedula,
        id_libranza=id_libranza,
        monto=monto,
        fecha_acuerdo_pago=fecha_acuerdo_pago,
    )
    clave_pago_bitrix = construir_clave_pago_bitrix(
        sistema_origen=SISTEMA_ORIGEN_BITRIX_CARTERA,
        cedula=cedula,
        id_libranza=id_libranza,
        monto=monto,
        fecha_acuerdo_pago=fecha_acuerdo_pago,
    )

    metadatos = {
        "origen": "webhooks_call_completed_bitrix",
        "id_contacto_bitrix": contact.get("id") or contact.get("ID"),
        "clave_pago_bitrix": clave_pago_bitrix,
        "cedula": cedula,
        "id_libranza": id_libranza,
        "pagaduria": limpiar_texto_pago(obtener_variable_entrada(input_variables, "PAGADURIA")),
        "telefono_input": telefono_input,
        "telefono_bitrix": telefono_bitrix,
        "correo_input": correo_input,
        "correo_bitrix": correo_bitrix,
        "mora_total": obtener_variable_entrada(input_variables, "MORA_TOTAL"),
        "mora": obtener_variable_entrada(input_variables, "MORA"),
        "cuota": obtener_variable_entrada(input_variables, "CUOTA"),
        "valor_confirmado": str(monto),
        "valor_confirmado_original": valor_confirmado_original,
        "valor_confirmado_legible": valor_confirmado_original or str(monto),
        "gestion_final": limpiar_texto_pago(output_vars.get("gestion_final")),
        "motivo_principal": limpiar_texto_pago(output_vars.get("motivo_principal")),
        "interes_pagar": limpiar_texto_pago(output_vars.get("interes_pagar")),
        "pago_hoy": limpiar_texto_pago(output_vars.get("pago_hoy")),
        "fecha_acuerdo_pago": fecha_acuerdo_pago,
        "resumen_llamada": limpiar_texto_pago(output_vars.get("resumenllamada")),
        "detalle_acuerdo": limpiar_texto_pago(output_vars.get("detalle_acuerdo")),
        "validation": validation,
    }

    if referencia_original:
        metadatos["referencia_externa_original"] = referencia_original

    return SolicitudCrearOrdenPago(
        sistema_origen=SISTEMA_ORIGEN_BITRIX_CARTERA,
        referencia_externa=referencia_externa,
        cliente={
            "correo": correo_bitrix or correo_input,
            "tipo_documento": "CC",
            "numero_documento": cedula,
            "nombre": construir_nombre_cliente(contact, input_variables),
            "telefono": telefono_bitrix or telefono_input,
        },
        pago={
            "monto": monto,
            "moneda": "COP",
            "codigo_pais": 343,
            "descripcion": normalizar_descripcion_payvalida(DESCRIPCION_PAGO_CARTERA),
            "fecha_expiracion": fecha_acuerdo_pago,
            "iva": "0",
            "metodo_pago": "",
            "recurrente": False,
        },
        metadatos=metadatos,
    )
