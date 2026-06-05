import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from utils.auth import obtener_token
from utils.config import settings
from utils.external_client import ExternalClient

logger = logging.getLogger(__name__)

enrutador = APIRouter(prefix="/api/v1/renovaciones/perfil", tags=["Renovaciones - Perfil Kuenta"])

CODIGO_PROFILES_GET = "KUENTA_PROFILES_GET"
CODIGO_PROFILE_CUSTOM_FIELDS_PUT = "KUENTA_PROFILE_CUSTOM_FIELDS_PUT"
CODIGO_DEBTORS_GET = "KUENTA_DEBTORS_GET"
CODIGO_PAYABLE_GET = "KUENTA_PAYABLE_GET"
MAX_INTENTOS_ACTUALIZACION = 3

ASESORA_FIELD_ID_DEFAULT = "735cb75c-a3ce-4d18-9b9f-f4d13a12dbc2"
ASESORA_CONFIG_ID_DEFAULT = "00625721-83c1-4f9c-bef9-0e8c9d8818f1"
ASESORA_AUTOGESTION_VALUE = "0000"
ASESORES_KUENTA = {
    "2018": "JORYE SUAREZ",
    "2050": "NEVIER CABALLERO",
    "2046": "PAOLA CANTILLO",
    "2360": "MARIANGEL CASTIBLANCO",
    "2442": "SANDY ARIAS",
    "2444": "VISLEY SALAS",
    "2888": "YEFERSON CORREDOR",
    "2890": "LORAINE GUTIERREZ",
    "2892": "TANIA CARO",
    "2894": "LINA REBOLLO",
    "2924": "SERGIO ALVAREZ",
    "2994": "SERGIO ALVAREZ",
    ASESORA_AUTOGESTION_VALUE: "AUTOGESTION",
}

SEMESTRES = {
    "primer semestre": "1",
    "primero": "1",
    "1": "1",
    "segundo semestre": "2",
    "segundo": "2",
    "2": "2",
    "tercer semestre": "3",
    "tercero": "3",
    "3": "3",
    "cuarto semestre": "4",
    "cuarto": "4",
    "4": "4",
    "quinto semestre": "5",
    "quinto": "5",
    "5": "5",
    "sexto semestre": "6",
    "sexto": "6",
    "6": "6",
    "septimo semestre": "7",
    "septimo": "7",
    "7": "7",
    "octavo semestre": "8",
    "octavo": "8",
    "8": "8",
    "noveno semestre": "9",
    "noveno": "9",
    "9": "9",
    "decimo semestre": "10",
    "decimo": "10",
    "10": "10",
}

SECCIONES_KUENTA = {
    1: "Foto",
    4: "Contacto",
    5: "Personal",
    6: "Finanzas",
    7: "Laboral",
    8: "Referencias",
    10: "Adicionales",
}


def _respuesta_powerbot(content: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=200, content=content)


def _normalizar_texto(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _obtener(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _valor_no_vacio(payload: Dict[str, Any], *keys: str) -> Tuple[Optional[str], Any]:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return key, payload.get(key)
    return None, None


def _normalizar_semestre(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, int) and 1 <= value <= 10:
        return str(value)
    text = _normalizar_texto(value)
    return SEMESTRES.get(text)


def _nombre_asesor(value: Any) -> str:
    code = "" if value is None else str(value).strip()
    return ASESORES_KUENTA.get(code, code or "SIN VALOR")


def _es_vacio(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return (
            text == ""
            or text == "00000000-0000-0000-0000-000000000000"
            or text.startswith("0001-01-01T00:00:00")
        )
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _error_response(
    accion: str,
    tipo_error: str,
    mensaje_cliente: str,
    error: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    content: Dict[str, Any] = {
        "ok": False,
        "accion": accion,
        "tipo_error": tipo_error,
        "mensaje_cliente": mensaje_cliente,
        "error": error or {},
    }
    if extra:
        content.update(extra)
    return content


def _headers_kuenta(token: str, id_debtor: str) -> Dict[str, str]:
    return {
        "Authorization": token,
        "Config-Organization-ID": settings.ORG_ID or "",
        "config-organization-id": settings.ORG_ID or "",
        "Organization-ID": id_debtor,
        "organization-id": id_debtor,
    }


async def _obtener_token_para_powerbot(accion: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    try:
        token = await obtener_token()
    except Exception as exc:
        logger.error("Error obteniendo token Kuenta para %s: %s", accion, exc, exc_info=True)
        return None, _error_response(
            accion=accion,
            tipo_error="token_error",
            mensaje_cliente="No pudimos autenticar la solicitud con Kuenta. Intenta nuevamente.",
            error={"mensaje": str(exc)},
        )
    if not token:
        return None, _error_response(
            accion=accion,
            tipo_error="token_error",
            mensaje_cliente="No pudimos autenticar la solicitud con Kuenta. Intenta nuevamente.",
            error={"mensaje": "Token vacio"},
        )
    return token, None


async def _ejecutar_servicio(
    codigo: str,
    accion: str,
    id_debtor: str,
    dynamic_values: Dict[str, Any],
    token: str,
) -> Dict[str, Any]:
    try:
        client = await ExternalClient.from_code(codigo, client_id=id_debtor)
    except ValueError as exc:
        logger.error("Servicio externo no configurado | codigo=%s | accion=%s", codigo, accion)
        return {
            "status": 0,
            "data": {
                "tipo_error": "servicio_no_configurado",
                "mensaje": str(exc),
            },
        }
    except Exception as exc:
        logger.error("Error cargando servicio externo | codigo=%s | accion=%s", codigo, accion, exc_info=True)
        return {
            "status": 0,
            "data": {
                "tipo_error": "servicio_no_configurado",
                "mensaje": str(exc),
            },
        }

    values = {
        "access_token": token,
        "ORG_ID": settings.ORG_ID or "",
        "Config-Organization-ID": settings.ORG_ID or "",
        "id_debtor": id_debtor,
        **dynamic_values,
    }
    client.set_dynamic_values(values)
    client.set_headers(_headers_kuenta(token, id_debtor))
    logger.info(
        "Ejecutando servicio Kuenta | accion=%s | codigo=%s | id_debtor=%s | dynamic_keys=%s",
        accion,
        codigo,
        id_debtor,
        list(dynamic_values.keys()),
    )
    return await client.run()


def _extraer_profiles(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if isinstance(inner.get("profiles"), list):
        return [item for item in inner["profiles"] if isinstance(item, dict)]
    if isinstance(inner.get("profile"), dict):
        return [inner["profile"]]
    if isinstance(data.get("profile"), dict):
        return [data["profile"]]
    return []


def _extraer_debtors(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if isinstance(inner.get("debtors"), list):
        return [item for item in inner["debtors"] if isinstance(item, dict)]
    if isinstance(inner.get("debtor"), dict):
        return [inner["debtor"]]
    if isinstance(data.get("debtor"), dict):
        return [data["debtor"]]
    return []


def _nombre_debtor_desde_profile(profile: Dict[str, Any]) -> str:
    natural = _natural(profile)
    first_name = str(natural.get("firstName") or "").strip()
    last_name = str(natural.get("lastName") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    if full_name:
        return full_name
    entity = profile.get("entity") if isinstance(profile.get("entity"), dict) else {}
    return str(entity.get("nickname") or entity.get("email") or "").strip()


def _nombre_debtor(debtor: Dict[str, Any]) -> str:
    profile = debtor.get("profile") if isinstance(debtor.get("profile"), dict) else {}
    natural = profile.get("natural") if isinstance(profile.get("natural"), dict) else {}
    first_name = str(natural.get("firstName") or "").strip()
    last_name = str(natural.get("lastName") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    return full_name or str(debtor.get("nickname") or debtor.get("email") or "").strip()


def _natural(profile: Dict[str, Any]) -> Dict[str, Any]:
    natural = profile.get("natural")
    return natural if isinstance(natural, dict) else profile


def _natural_id(profile: Dict[str, Any]) -> Optional[str]:
    natural = _natural(profile)
    value = natural.get("ID") or natural.get("id")
    return str(value).strip() if value else None


def _seleccionar_profile(
    profiles: List[Dict[str, Any]],
    natural_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not profiles:
        return None
    if natural_id:
        expected = str(natural_id).strip()
        for profile in profiles:
            if _natural_id(profile) == expected:
                return profile
    return profiles[0]


def _custom_fields(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    natural = _natural(profile)
    fields = natural.get("customFields") or profile.get("customFields") or []
    return [field for field in fields if isinstance(field, dict)]


def _buscar_custom_field(profile: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    normalized_name = _normalizar_texto(name)
    for field in _custom_fields(profile):
        if _normalizar_texto(field.get("name")) == normalized_name:
            return field
    return None


def _buscar_custom_field_por_id(profile: Dict[str, Any], field_id: str) -> Optional[Dict[str, Any]]:
    expected = str(field_id or "").strip()
    if not expected:
        return None
    for field in _custom_fields(profile):
        if str(field.get("id") or "").strip() == expected:
            return field
        for child in field.get("multiField") or []:
            if isinstance(child, dict) and str(child.get("id") or "").strip() == expected:
                return child
    return None


def _tipo_valor_campo(field: Dict[str, Any]) -> str:
    if isinstance(field.get("multiField"), list) and len(field.get("multiField") or []) > 0:
        return "multiField"
    if _normalizar_texto(field.get("name")) == _normalizar_texto("nDeSemestre"):
        return "arrayValue"
    if isinstance(field.get("arrayValue"), list) and len(field.get("arrayValue") or []) > 0:
        return "arrayValue"
    if field.get("arrayValue") is not None and field.get("value") is None:
        return "arrayValue"
    return "value"


def _valor_campo_detalle(field: Dict[str, Any]) -> Any:
    value_type = _tipo_valor_campo(field)
    if value_type == "multiField":
        return field.get("multiField")
    if value_type == "arrayValue":
        return field.get("arrayValue")
    return field.get("value")


def _apartado_custom_field(profile: Dict[str, Any], field: Dict[str, Any]) -> Dict[str, Any]:
    config_id = str(field.get("configID") or "").strip()
    return {
        "apartado_id": config_id or profile.get("ID") or "",
        "apartado_nombre": "Campos personalizados Kuenta",
    }


def _serializar_custom_field(
    profile: Dict[str, Any],
    field: Dict[str, Any],
    natural_id: Optional[str],
    parent_name: Optional[str] = None,
) -> Dict[str, Any]:
    apartado = _apartado_custom_field(profile, field)
    apartado_nombre = apartado["apartado_nombre"]
    if parent_name:
        apartado_nombre = f"{apartado_nombre} / {parent_name}"
    return {
        "apartado_id": apartado["apartado_id"],
        "apartado_nombre": apartado_nombre,
        "profile_id": profile.get("ID") or profile.get("id"),
        "natural_id": natural_id,
        "ultima_actualizacion_campos": _natural(profile).get("updatedAt") or profile.get("updatedAt"),
        "name": field.get("name"),
        "id": field.get("id"),
        "configID": field.get("configID"),
        "tipo_valor": _tipo_valor_campo(field),
        "valor": _valor_campo_detalle(field),
        "value": field.get("value"),
        "arrayValue": field.get("arrayValue"),
        "multiField": field.get("multiField"),
    }


def _detalle_profile_campos(
    profile: Dict[str, Any],
    response_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    natural = _natural(profile)
    natural_id = _natural_id(profile)
    entity = profile.get("entity") if isinstance(profile.get("entity"), dict) else {}
    debtor_id = profile.get("entityID") or entity.get("ID")
    campos = []
    for field in _custom_fields(profile):
        campos.append(_serializar_custom_field(profile, field, natural_id))
        for child in field.get("multiField") or []:
            if isinstance(child, dict):
                campos.append(
                    _serializar_custom_field(
                        profile,
                        child,
                        natural_id,
                        parent_name=str(field.get("name") or ""),
                    )
                )

    campos_requeridos_faltantes = _campos_faltantes_legibles(profile, response_data or {})
    requeridos_faltantes_names = {
        _normalizar_texto(field.get("name"))
        for field in campos_requeridos_faltantes
        if field.get("name")
    }
    campos_vacios = [campo for campo in campos if _es_vacio(campo.get("valor"))]
    campos_con_valor = [campo for campo in campos if not _es_vacio(campo.get("valor"))]
    campos_vacios_normales = [
        campo
        for campo in campos_vacios
        if _normalizar_texto(campo.get("name")) not in requeridos_faltantes_names
    ]

    return {
        "id_debtor": debtor_id,
        "nombre_debtor": _nombre_debtor_desde_profile(profile),
        "profile_id": profile.get("ID") or profile.get("id"),
        "natural_id": natural_id,
        "profile_updated_at": profile.get("updatedAt"),
        "natural_updated_at": natural.get("updatedAt"),
        "apartados": [
            {
                "apartado_id": item.get("apartado_id"),
                "apartado_nombre": item.get("apartado_nombre"),
                "ultima_actualizacion_campos": item.get("ultima_actualizacion_campos"),
            }
            for item in {
                f"{campo.get('apartado_id')}::{campo.get('apartado_nombre')}": campo
                for campo in campos
            }.values()
        ],
        "campos": campos,
        "total_campos": len(campos),
        "resumen_campos": {
            "total_campos": len(campos),
            "total_campos_con_valor": len(campos_con_valor),
            "total_campos_vacios": len(campos_vacios),
            "total_campos_requeridos_faltantes": len(campos_requeridos_faltantes),
            "total_campos_vacios_normales": len(campos_vacios_normales),
        },
        "campos_requeridos_faltantes": campos_requeridos_faltantes,
        "campos_vacios_normales": campos_vacios_normales,
    }


def _valor_custom_field(profile: Dict[str, Any], name: str) -> Any:
    field = _buscar_custom_field(profile, name)
    if not field:
        return None
    value = field.get("value")
    array_value = field.get("arrayValue")
    multi_field = field.get("multiField")
    if not _es_vacio(value):
        return value
    if not _es_vacio(array_value):
        return array_value
    return multi_field


def _valor_profile(profile: Dict[str, Any], field_name: str) -> Any:
    natural = _natural(profile)
    if field_name in natural:
        return natural.get(field_name)
    if field_name in profile:
        return profile.get(field_name)
    return _valor_custom_field(profile, field_name)


async def _consultar_perfil(
    id_debtor: str,
    accion: str,
    token: str,
    natural_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
    response = await _ejecutar_servicio(
        codigo=CODIGO_PROFILES_GET,
        accion=accion,
        id_debtor=id_debtor,
        dynamic_values={},
        token=token,
    )
    status = response.get("status")
    data = response.get("data", {})
    if status not in (200, 201):
        tipo_error = data.get("tipo_error") if isinstance(data, dict) else None
        return None, response, _error_response(
            accion=accion,
            tipo_error=tipo_error or "error_kuenta",
            mensaje_cliente="No pudimos consultar el perfil en Kuenta. Intenta nuevamente.",
            error={"status_kuenta": status, "respuesta_kuenta": data},
        )

    profiles = _extraer_profiles(data)
    profile = _seleccionar_profile(profiles, natural_id)
    if not profile:
        return None, response, _error_response(
            accion=accion,
            tipo_error="perfil_no_encontrado",
            mensaje_cliente="No encontramos el perfil del cliente en Kuenta.",
            error={
                "status_kuenta": status,
                "natural_id": natural_id,
                "respuesta_kuenta": data,
            },
        )

    return profile, response, None


async def _buscar_debtor_por_telefono(
    telefono: str,
    accion: str,
    token: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
    telefono_normalizado = re.sub(r"\s+", "", str(telefono or "").strip())
    if not telefono_normalizado:
        return None, {}, _error_response(
            accion=accion,
            tipo_error="payload_incompleto",
            mensaje_cliente="Falta el numero de telefono para buscar el cliente en Kuenta.",
            error={"campo_faltante": "telefono"},
        )

    response = await _ejecutar_servicio(
        codigo=CODIGO_DEBTORS_GET,
        accion=accion,
        id_debtor=settings.ORG_ID or "",
        dynamic_values={
            "telefono": telefono_normalizado,
            "phone": telefono_normalizado,
            "q": telefono_normalizado,
            "limit": 10,
            "offset": 0,
        },
        token=token,
    )
    status = response.get("status")
    data = response.get("data", {})
    if status not in (200, 201):
        tipo_error = data.get("tipo_error") if isinstance(data, dict) else None
        return None, response, _error_response(
            accion=accion,
            tipo_error=tipo_error or "error_kuenta",
            mensaje_cliente="No pudimos buscar el cliente por telefono en Kuenta.",
            error={"status_kuenta": status, "respuesta_kuenta": data},
        )

    debtors = _extraer_debtors(data)
    if not debtors:
        return None, response, _error_response(
            accion=accion,
            tipo_error="deudor_no_encontrado",
            mensaje_cliente="No encontramos un cliente en Kuenta con ese numero de telefono.",
            error={"telefono": telefono_normalizado, "respuesta_kuenta": data},
        )
    return debtors[0], response, None


async def _resolver_debtor_desde_payload(
    payload: Dict[str, Any],
    accion: str,
    token: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    id_debtor = _obtener(payload, "id_debtor", "idDebtor", "organization_id", "organizationID")
    if id_debtor:
        return str(id_debtor), None, None

    telefono = _obtener(payload, "telefono", "phone", "numero_telefono", "numeroTelefono")
    if not telefono:
        return None, None, _error_response(
            accion=accion,
            tipo_error="payload_incompleto",
            mensaje_cliente="Falta id_debtor o telefono para identificar el cliente en Kuenta.",
            error={"campos_aceptados": ["id_debtor", "telefono"]},
        )

    debtor, _, debtor_error = await _buscar_debtor_por_telefono(str(telefono), accion, token)
    if debtor_error:
        return None, None, debtor_error
    debtor_id = debtor.get("ID") or debtor.get("id")
    if not debtor_id:
        return None, debtor, _error_response(
            accion=accion,
            tipo_error="deudor_sin_id",
            mensaje_cliente="Kuenta encontro el cliente, pero no retorno el ID del deudor.",
            error={"debtor": debtor},
        )
    return str(debtor_id), debtor, None


def _resolver_id_credito(payload: Dict[str, Any]) -> Optional[str]:
    value = _obtener(
        payload,
        "id_credito",
        "idCredito",
        "credit_id",
        "creditID",
        "id_credito_mora",
        "idCreditoMora",
    )
    return str(value).strip() if value else None


async def _consultar_payable(
    id_credito: str,
    id_debtor: str,
    accion: str,
    token: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
    try:
        client = await ExternalClient.from_code(CODIGO_PAYABLE_GET, client_id=id_debtor)
    except ValueError as exc:
        logger.error("Servicio externo no configurado | codigo=%s | accion=%s", CODIGO_PAYABLE_GET, accion)
        response = {
            "status": 0,
            "data": {
                "tipo_error": "servicio_no_configurado",
                "mensaje": str(exc),
            },
        }
    except Exception as exc:
        logger.error(
            "Error cargando servicio externo | codigo=%s | accion=%s",
            CODIGO_PAYABLE_GET,
            accion,
            exc_info=True,
        )
        response = {
            "status": 0,
            "data": {
                "tipo_error": "servicio_no_configurado",
                "mensaje": str(exc),
            },
        }
    else:
        values = {
            "access_token": token,
            "ORG_ID": settings.ORG_ID or "",
            "Config-Organization-ID": settings.ORG_ID or "",
            "id_debtor": id_debtor,
            "id_credito": id_credito,
            "idCredito": id_credito,
            "credit_id": id_credito,
            "creditID": id_credito,
        }
        client.set_dynamic_values(values)
        client.set_headers(_headers_kuenta(token, id_debtor))
        if "{id_credito}" not in client.url and "{credit_id}" not in client.url and "{creditID}" not in client.url:
            client.set_path(f"/{id_credito}")
        logger.info(
            "Consultando payable Kuenta | accion=%s | codigo=%s | id_debtor=%s | id_credito=%s",
            accion,
            CODIGO_PAYABLE_GET,
            id_debtor,
            id_credito,
        )
        response = await client.run()

    status = response.get("status")
    data = response.get("data", {})
    if status not in (200, 201):
        tipo_error = data.get("tipo_error") if isinstance(data, dict) else None
        return None, response, _error_response(
            accion=accion,
            tipo_error=tipo_error or "error_kuenta",
            mensaje_cliente="No pudimos consultar el credito en Kuenta para validar campos faltantes.",
            error={"status_kuenta": status, "respuesta_kuenta": data},
        )
    return data if isinstance(data, dict) else {}, response, None


def _resolver_natural_id(payload: Dict[str, Any], profile: Dict[str, Any]) -> Optional[str]:
    natural_id = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    if natural_id:
        return str(natural_id).strip()
    return _natural_id(profile)


def _resolver_campo_asesora(profile: Dict[str, Any]) -> Tuple[str, str]:
    field = _buscar_custom_field(profile, "asesora") or {}
    field_id = field.get("id") or ASESORA_FIELD_ID_DEFAULT
    config_id = field.get("configID") or ASESORA_CONFIG_ID_DEFAULT
    return str(field_id), str(config_id)


def _resolver_campo_semestre(payload: Dict[str, Any], profile: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    field = _buscar_custom_field(profile, "nDeSemestre") or {}
    field_id = field.get("id") or _obtener(payload, "id_campo_semestre", "idCampoSemestre", "semestre_field_id")
    config_id = field.get("configID") or _obtener(
        payload,
        "configID_campo_semestre",
        "config_id_campo_semestre",
        "configIdCampoSemestre",
        "semestre_config_id",
    )
    field_id = str(field_id).strip() if field_id else None
    config_id = str(config_id).strip() if config_id else None
    return field_id, config_id


async def _actualizar_custom_fields(
    accion: str,
    id_debtor: str,
    natural_id: str,
    custom_fields: List[Dict[str, Any]],
    token: str,
) -> Tuple[bool, Dict[str, Any]]:
    last_response: Dict[str, Any] = {}
    for intento in range(1, MAX_INTENTOS_ACTUALIZACION + 1):
        logger.info(
            "Intento actualizacion customFields Kuenta | accion=%s | intento=%s/%s | id_debtor=%s | natural_id=%s",
            accion,
            intento,
            MAX_INTENTOS_ACTUALIZACION,
            id_debtor,
            natural_id,
        )
        response = await _ejecutar_servicio(
            codigo=CODIGO_PROFILE_CUSTOM_FIELDS_PUT,
            accion=accion,
            id_debtor=id_debtor,
            dynamic_values={
                "naturalID": natural_id,
                "natural_id": natural_id,
                "customFields": custom_fields,
            },
            token=token,
        )
        last_response = response
        if response.get("status") in (200, 201):
            return True, response

    return False, last_response


async def _verificar_campo(
    accion: str,
    id_debtor: str,
    token: str,
    field_name: str,
    expected_value: Any,
    array: bool = False,
    natural_id: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    profile, response, error = await _consultar_perfil(
        id_debtor,
        accion,
        token,
        natural_id=natural_id,
    )
    if error:
        return False, profile, error

    value = _valor_custom_field(profile or {}, field_name)
    if array:
        values = value if isinstance(value, list) else [value]
        verified = str(expected_value) in [str(item) for item in values if item is not None]
    else:
        verified = str(value).strip() == str(expected_value).strip()

    if not verified:
        return False, profile, _error_response(
            accion=accion,
            tipo_error="actualizacion_no_verificada",
            mensaje_cliente="Kuenta recibio la solicitud, pero no pudimos confirmar que el campo quedara actualizado.",
            error={
                "status_kuenta": response.get("status"),
                "valor_esperado": expected_value,
                "valor_obtenido": value,
            },
        )

    return True, profile, None


def _resolver_custom_field_actualizable(
    payload: Dict[str, Any],
    profile: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    field_name = _obtener(payload, "campo", "field_name", "name", "nombre_campo", "fieldName")
    field_id = _obtener(payload, "id_campo", "field_id", "id", "fieldID")

    field = None
    if field_name:
        field = _buscar_custom_field(profile, str(field_name))
    if not field and field_id:
        field = _buscar_custom_field_por_id(profile, str(field_id))

    if not field:
        return None, _error_response(
            accion="actualizar_campo",
            tipo_error="campo_no_encontrado",
            mensaje_cliente="No encontramos ese campo en el perfil de Kuenta.",
            error={
                "campo": field_name,
                "id_campo": field_id,
                "sugerencia": "Consulta primero /campos-detalle y usa el name o id exacto del campo.",
            },
        )
    return field, None


def _extraer_valor_actualizacion(
    payload: Dict[str, Any],
    field: Dict[str, Any],
) -> Tuple[Optional[str], Any, Optional[Dict[str, Any]]]:
    scalar_key, scalar_value = _valor_no_vacio(payload, "valor", "value")
    array_key, array_value = _valor_no_vacio(payload, "arrayValue", "array_value", "valores")
    multi_key, multi_value = _valor_no_vacio(payload, "multiField", "multi_field")
    received = [
        item
        for item in [
            ("value", scalar_key, scalar_value),
            ("arrayValue", array_key, array_value),
            ("multiField", multi_key, multi_value),
        ]
        if item[1]
    ]

    if not received:
        return None, None, _error_response(
            accion="actualizar_campo",
            tipo_error="payload_incompleto",
            mensaje_cliente="Falta el valor que se debe registrar en Kuenta.",
            error={"campos_aceptados": ["valor", "value", "arrayValue", "multiField"]},
        )
    if len(received) > 1:
        return None, None, _error_response(
            accion="actualizar_campo",
            tipo_error="payload_ambiguo",
            mensaje_cliente="Envia solo un tipo de valor para actualizar el campo en Kuenta.",
            error={"campos_recibidos": [item[1] for item in received]},
        )

    explicit_type = _obtener(payload, "tipo_valor", "value_type", "tipoValor")
    if explicit_type:
        value_type = str(explicit_type).strip()
        if value_type not in ("value", "arrayValue", "multiField"):
            return None, None, _error_response(
                accion="actualizar_campo",
                tipo_error="tipo_valor_invalido",
                mensaje_cliente="El tipo de valor no es valido para actualizar Kuenta.",
                error={"tipo_valor": explicit_type, "permitidos": ["value", "arrayValue", "multiField"]},
            )
    else:
        value_type = received[0][0]
        if value_type == "value" and _normalizar_texto(field.get("name")) == _normalizar_texto("nDeSemestre"):
            value_type = "arrayValue"

    raw_value = received[0][2]
    if value_type == "arrayValue" and not isinstance(raw_value, list):
        raw_value = [raw_value]
    if value_type == "multiField" and not isinstance(raw_value, list):
        return None, None, _error_response(
            accion="actualizar_campo",
            tipo_error="multi_field_invalido",
            mensaje_cliente="El campo multiField debe recibirse como una lista de objetos.",
            error={"valor_recibido": raw_value},
        )
    return value_type, raw_value, None


def _construir_custom_field_actualizacion(
    payload: Dict[str, Any],
    field: Dict[str, Any],
    value_type: str,
    value: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    field_id = field.get("id") or _obtener(payload, "id_campo", "field_id", "id", "fieldID")
    config_id = field.get("configID") or _obtener(payload, "configID", "config_id", "configId")
    name = field.get("name") or _obtener(payload, "campo", "field_name", "name", "nombre_campo", "fieldName")

    field_id = str(field_id).strip() if field_id else ""
    config_id = str(config_id).strip() if config_id else ""
    if not field_id or not config_id:
        return None, _error_response(
            accion="actualizar_campo",
            tipo_error="configuracion_campo_incompleta",
            mensaje_cliente="No pudimos identificar id y configID del campo en Kuenta.",
            error={
                "campo": name,
                "id": field_id,
                "configID": config_id,
                "sugerencia": "Consulta /campos-detalle o envia id_campo y configID en la peticion.",
            },
        )

    item = {
        "id": field_id,
        "name": name,
        "configID": config_id,
        value_type: value,
    }
    return item, None


def _valor_actualizado_verificado(actual_value: Any, expected_value: Any, value_type: str) -> bool:
    if value_type == "arrayValue":
        actual_values = actual_value if isinstance(actual_value, list) else [actual_value]
        expected_values = expected_value if isinstance(expected_value, list) else [expected_value]
        actual_text = [str(item).strip() for item in actual_values if item is not None]
        expected_text = [str(item).strip() for item in expected_values if item is not None]
        return all(item in actual_text for item in expected_text)
    if value_type == "multiField":
        return actual_value == expected_value
    return str(actual_value if actual_value is not None else "").strip() == str(expected_value).strip()


def _extraer_missing_fields(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("missingFields"), list):
        return [field for field in data["missingFields"] if isinstance(field, dict)]
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    if isinstance(inner.get("missingFields"), list):
        return [field for field in inner["missingFields"] if isinstance(field, dict)]
    return []


def _nombre_seccion_campo(field: Dict[str, Any]) -> str:
    section_label = field.get("sectionLabel")
    if section_label:
        return str(section_label)
    category = field.get("category")
    try:
        category_int = int(category)
    except (TypeError, ValueError):
        category_int = 0
    return SECCIONES_KUENTA.get(category_int, "Informacion del perfil")


def _serializar_missing_field(item: Dict[str, Any], parent: Optional[str] = None) -> Dict[str, Any]:
    field = item.get("field") if isinstance(item.get("field"), dict) else {}
    options = field.get("options")
    multi_questions = []
    for child in field.get("multiQuestions") or []:
        if isinstance(child, dict):
            multi_questions.append(_serializar_missing_field(child, parent=str(field.get("name") or item.get("label") or "")))

    return {
        "name": field.get("name") or item.get("name") or item.get("fieldName"),
        "label": item.get("label") or field.get("label") or field.get("name"),
        "required": bool(item.get("required")),
        "type": field.get("type") or item.get("type"),
        "fieldID": item.get("fieldID") or field.get("ID"),
        "id": item.get("ID"),
        "formID": item.get("formID"),
        "configurationID": field.get("configurationID"),
        "section": field.get("section"),
        "sectionLabel": field.get("sectionLabel"),
        "sectionName": _nombre_seccion_campo(field),
        "sectionOrder": field.get("sectionOrder"),
        "category": field.get("category"),
        "min": item.get("min") if item.get("min") is not None else field.get("min"),
        "onlyAdmin": item.get("onlyAdmin"),
        "alwaysAsk": item.get("alwaysAsk"),
        "autocomplete": field.get("autocomplete"),
        "options": options if isinstance(options, list) else None,
        "parent": parent,
        "multiQuestions": multi_questions,
    }


def _campos_faltantes_desde_payable(data: Dict[str, Any]) -> Dict[str, Any]:
    missing = [_serializar_missing_field(item) for item in _extraer_missing_fields(data)]
    requeridos = [item for item in missing if item.get("required") is True]
    no_requeridos = [item for item in missing if item.get("required") is False]
    secciones = {}
    for item in missing:
        key = item.get("sectionName") or "Informacion del perfil"
        entry = secciones.setdefault(
            key,
            {
                "sectionName": key,
                "total": 0,
                "requeridos_faltantes": 0,
                "normales_vacios": 0,
            },
        )
        entry["total"] += 1
        if item.get("required") is True:
            entry["requeridos_faltantes"] += 1
        else:
            entry["normales_vacios"] += 1

    return {
        "campos_faltantes": missing,
        "campos_requeridos_faltantes": requeridos,
        "campos_vacios_normales": no_requeridos,
        "resumen_campos": {
            "total_campos_faltantes": len(missing),
            "total_campos_requeridos_faltantes": len(requeridos),
            "total_campos_vacios_normales": len(no_requeridos),
        },
        "resumen_por_seccion": list(secciones.values()),
    }


def _log_campos_faltantes_credito(
    *,
    accion: str,
    id_debtor: str,
    id_credito: str,
    resumen: Dict[str, Any],
) -> None:
    resumen_campos = resumen.get("resumen_campos") or {}
    requeridos = resumen.get("campos_requeridos_faltantes") or []
    normales = resumen.get("campos_vacios_normales") or []
    labels_requeridos = [
        str(field.get("label") or field.get("name") or "")
        for field in requeridos
        if field.get("label") or field.get("name")
    ]
    labels_normales = [
        str(field.get("label") or field.get("name") or "")
        for field in normales
        if field.get("label") or field.get("name")
    ]
    logger.info(
        "Campos faltantes Kuenta por credito | accion=%s | id_debtor=%s | id_credito=%s | total=%s | requeridos=%s | normales=%s",
        accion,
        id_debtor,
        id_credito,
        resumen_campos.get("total_campos_faltantes", 0),
        resumen_campos.get("total_campos_requeridos_faltantes", 0),
        resumen_campos.get("total_campos_vacios_normales", 0),
    )
    logger.info(
        "Campos requeridos faltantes Kuenta | accion=%s | id_credito=%s | campos=%s",
        accion,
        id_credito,
        labels_requeridos[:80],
    )
    if labels_normales:
        logger.info(
            "Campos no requeridos vacios Kuenta | accion=%s | id_credito=%s | campos=%s",
            accion,
            id_credito,
            labels_normales[:80],
        )
    for section in resumen.get("resumen_por_seccion") or []:
        logger.info(
            "Campos faltantes Kuenta por seccion | accion=%s | id_credito=%s | seccion=%s | total=%s | requeridos=%s | normales=%s",
            accion,
            id_credito,
            section.get("sectionName"),
            section.get("total"),
            section.get("requeridos_faltantes"),
            section.get("normales_vacios"),
        )


def _mensaje_campos_requeridos_credito(fields: List[Dict[str, Any]]) -> str:
    if not fields:
        return "El credito no tiene campos requeridos pendientes por completar en Kuenta."
    labels = [str(field.get("label") or field.get("name")) for field in fields]
    if len(labels) == 1:
        joined = labels[0]
    else:
        joined = ", ".join(labels[:-1]) + f" y {labels[-1]}"
    return f"Para continuar con el credito, debes completar en Kuenta estos campos requeridos: {joined}."


def _campos_faltantes_legibles(profile: Dict[str, Any], response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = []
    seen = set()
    for item in _extraer_missing_fields(response_data):
        if item.get("required") is False:
            continue
        field = item.get("field") if isinstance(item.get("field"), dict) else {}
        name = field.get("name") or item.get("name") or item.get("fieldName")
        label = item.get("label") or field.get("label") or name
        field_type = field.get("type") or item.get("type")
        if not name or name in seen:
            continue
        current_value = _valor_profile(profile, str(name))
        if not _es_vacio(current_value):
            continue
        seen.add(name)
        fields.append(
            {
                "name": name,
                "label": label,
                "type": field_type,
            }
        )
    return fields


def _mensaje_campos_faltantes(fields: List[Dict[str, Any]]) -> str:
    if not fields:
        return "El perfil no tiene campos requeridos pendientes por completar en Kuenta."
    labels = [str(field.get("label") or field.get("name")) for field in fields]
    if len(labels) == 1:
        joined = labels[0]
    else:
        joined = ", ".join(labels[:-1]) + f" y {labels[-1]}"
    return f"Para continuar con la renovacion, debes completar en Kuenta estos campos: {joined}."


async def _actualizar_asesora_core(
    *,
    payload: Dict[str, Any],
    accion: str,
    id_debtor: str,
    token: str,
    profile: Dict[str, Any],
    natural_id: str,
) -> Dict[str, Any]:
    current_value = _valor_custom_field(profile, "asesora")
    if str(current_value or "").strip() == ASESORA_AUTOGESTION_VALUE:
        return {
            "ok": True,
            "accion": accion,
            "estado": "sin_cambio",
            "mensaje_cliente": 'Ya el campo asesora tiene el dato "AUTOGESTION".',
            "campo_actualizado": {
                "name": "asesora",
                "value": ASESORA_AUTOGESTION_VALUE,
                "valor_legible": "AUTOGESTION",
            },
            "natural_id": natural_id,
        }

    field_id, config_id = _resolver_campo_asesora(profile)
    custom_fields = [
        {
            "id": field_id,
            "name": "asesora",
            "value": ASESORA_AUTOGESTION_VALUE,
            "configID": config_id,
        }
    ]
    updated, update_response = await _actualizar_custom_fields(
        accion=accion,
        id_debtor=id_debtor,
        natural_id=natural_id,
        custom_fields=custom_fields,
        token=token,
    )
    if not updated:
        data = update_response.get("data", {})
        return _error_response(
            accion=accion,
            tipo_error=data.get("tipo_error", "error_kuenta") if isinstance(data, dict) else "error_kuenta",
            mensaje_cliente="No pudimos actualizar la asesora en Kuenta. Intenta nuevamente.",
            error={"status_kuenta": update_response.get("status"), "respuesta_kuenta": data},
        )

    verified, _, verify_error = await _verificar_campo(
        accion=accion,
        id_debtor=id_debtor,
        token=token,
        field_name="asesora",
        expected_value=ASESORA_AUTOGESTION_VALUE,
        natural_id=natural_id,
    )
    if not verified and verify_error:
        return verify_error

    return {
        "ok": True,
        "accion": accion,
        "estado": "actualizado",
        "mensaje_cliente": "Campo asesora actualizado correctamente en Kuenta como AUTOGESTION.",
        "campo_actualizado": {
            "name": "asesora",
            "value": ASESORA_AUTOGESTION_VALUE,
            "valor_legible": "AUTOGESTION",
            "valor_anterior": current_value,
            "valor_anterior_legible": _nombre_asesor(current_value),
        },
        "natural_id": natural_id,
    }


async def _actualizar_semestre_core(
    *,
    payload: Dict[str, Any],
    accion: str,
    id_debtor: str,
    token: str,
    profile: Dict[str, Any],
    natural_id: str,
    requerir_dato: bool,
) -> Dict[str, Any]:
    semestre_raw = _obtener(
        payload,
        "semestre",
        "semestre_renovacion",
        "semestre_renovacion_menu",
        "semestreRenovacion",
        "semestre_renovación",
        "semestre_renovación_menu",
    )
    semestre = _normalizar_semestre(semestre_raw)
    current_value = _valor_custom_field(profile, "nDeSemestre")
    current_values = current_value if isinstance(current_value, list) else [current_value]
    current_values_text = [str(item) for item in current_values if item is not None]

    if semestre_raw in (None, ""):
        if requerir_dato:
            return _error_response(
                accion=accion,
                tipo_error="semestre_invalido",
                mensaje_cliente="El semestre ingresado no es valido. Debe estar entre 1 y 10.",
                error={"valor_recibido": semestre_raw},
            )
        if _es_vacio(current_value):
            return _error_response(
                accion=accion,
                tipo_error="dato_no_recibido",
                mensaje_cliente="No se recibio el numero de semestre y el campo sigue vacio en Kuenta.",
                error={
                    "campo": "nDeSemestre",
                    "valor_obtenido_kuenta": current_value,
                },
                extra={
                    "campo_pendiente_manual": {
                        "name": "nDeSemestre",
                        "label": "Numero de semestre",
                        "motivo": "No llego el dato en la peticion inicial y el campo esta vacio en Kuenta.",
                    }
                },
            )
        return {
            "ok": True,
            "accion": accion,
            "estado": "sin_cambio",
            "mensaje_cliente": "No se recibio semestre nuevo, pero el campo ya tiene valor en Kuenta.",
            "campo_actualizado": {
                "name": "nDeSemestre",
                "arrayValue": current_values_text,
            },
            "natural_id": natural_id,
        }

    if not semestre:
        return _error_response(
            accion=accion,
            tipo_error="semestre_invalido",
            mensaje_cliente="El semestre ingresado no es valido. Debe estar entre 1 y 10.",
            error={"valor_recibido": semestre_raw},
        )

    if semestre in current_values_text:
        return {
            "ok": True,
            "accion": accion,
            "estado": "sin_cambio",
            "mensaje_cliente": f"El campo numero de semestre ya tiene el dato {semestre} en Kuenta.",
            "campo_actualizado": {
                "name": "nDeSemestre",
                "arrayValue": [semestre],
            },
            "natural_id": natural_id,
        }

    field_id, config_id = _resolver_campo_semestre(payload, profile)
    if not field_id or not config_id:
        return _error_response(
            accion=accion,
            tipo_error="configuracion_campo_semestre_incompleta",
            mensaje_cliente="No pudimos identificar la configuracion del campo semestre en Kuenta.",
            error={
                "mensaje": "Falta id o configID del campo nDeSemestre.",
                "sugerencia": "Enviar id_campo_semestre y configID_campo_semestre o configurar una consulta que los retorne.",
            },
        )

    custom_fields = [
        {
            "id": field_id,
            "name": "nDeSemestre",
            "arrayValue": [semestre],
            "configID": config_id,
        }
    ]
    updated, update_response = await _actualizar_custom_fields(
        accion=accion,
        id_debtor=id_debtor,
        natural_id=natural_id,
        custom_fields=custom_fields,
        token=token,
    )
    if not updated:
        data = update_response.get("data", {})
        return _error_response(
            accion=accion,
            tipo_error=data.get("tipo_error", "error_kuenta") if isinstance(data, dict) else "error_kuenta",
            mensaje_cliente="No pudimos actualizar el semestre en Kuenta. Intenta nuevamente.",
            error={"status_kuenta": update_response.get("status"), "respuesta_kuenta": data},
        )

    verified, _, verify_error = await _verificar_campo(
        accion=accion,
        id_debtor=id_debtor,
        token=token,
        field_name="nDeSemestre",
        expected_value=semestre,
        array=True,
        natural_id=natural_id,
    )
    if not verified and verify_error:
        return verify_error

    return {
        "ok": True,
        "accion": accion,
        "estado": "actualizado",
        "mensaje_cliente": f"Campo numero de semestre actualizado correctamente en Kuenta: {semestre}.",
        "campo_actualizado": {
            "name": "nDeSemestre",
            "arrayValue": [semestre],
            "valor_anterior": current_value,
        },
        "natural_id": natural_id,
    }


@enrutador.post("/actualizar-asesora")
async def actualizar_asesora(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "actualizar_asesora"
    id_debtor = _obtener(payload, "id_debtor", "idDebtor", "organization_id", "organizationID")
    if not id_debtor:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="payload_incompleto",
                mensaje_cliente="Falta el identificador del cliente para actualizar el perfil en Kuenta.",
                error={"campo_faltante": "id_debtor"},
            )
        )

    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, _, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    natural_id = _resolver_natural_id(payload, profile or {})
    if not natural_id:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="natural_id_no_encontrado",
                mensaje_cliente="No pudimos identificar el perfil natural del cliente en Kuenta.",
                error={"campo_faltante": "natural_id"},
            )
        )

    resultado = await _actualizar_asesora_core(
        payload=payload,
        accion=accion,
        id_debtor=str(id_debtor),
        token=token or "",
        profile=profile or {},
        natural_id=natural_id,
    )
    return _respuesta_powerbot(resultado)


@enrutador.post("/actualizar-semestre")
async def actualizar_semestre(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "actualizar_semestre"
    id_debtor = _obtener(payload, "id_debtor", "idDebtor", "organization_id", "organizationID")

    if not id_debtor:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="payload_incompleto",
                mensaje_cliente="Falta el identificador del cliente para actualizar el perfil en Kuenta.",
                error={"campo_faltante": "id_debtor"},
            )
        )

    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, _, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    natural_id = _resolver_natural_id(payload, profile or {})
    if not natural_id:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="natural_id_no_encontrado",
                mensaje_cliente="No pudimos identificar el perfil natural del cliente en Kuenta.",
                error={"campo_faltante": "natural_id"},
            )
        )

    resultado = await _actualizar_semestre_core(
        payload=payload,
        accion=accion,
        id_debtor=str(id_debtor),
        token=token or "",
        profile=profile or {},
        natural_id=natural_id,
        requerir_dato=True,
    )
    return _respuesta_powerbot(resultado)


@enrutador.post("/actualizar-todos")
async def actualizar_todos(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "actualizar_todos"
    id_debtor = _obtener(payload, "id_debtor", "idDebtor", "organization_id", "organizationID")
    if not id_debtor:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="payload_incompleto",
                mensaje_cliente="Falta el identificador del cliente para actualizar el perfil en Kuenta.",
                error={"campo_faltante": "id_debtor"},
            )
        )

    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, _, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    natural_id = _resolver_natural_id(payload, profile or {})
    if not natural_id:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="natural_id_no_encontrado",
                mensaje_cliente="No pudimos identificar el perfil natural del cliente en Kuenta.",
                error={"campo_faltante": "natural_id"},
            )
        )

    resultados = []
    campos_no_actualizados = []
    campos_pendientes_manual = []

    resultado_asesora = await _actualizar_asesora_core(
        payload=payload,
        accion="actualizar_asesora",
        id_debtor=str(id_debtor),
        token=token or "",
        profile=profile or {},
        natural_id=natural_id,
    )
    resultados.append(resultado_asesora)
    if not resultado_asesora.get("ok"):
        campos_no_actualizados.append(
            {
                "name": "asesora",
                "label": "Asesora",
                "tipo_error": resultado_asesora.get("tipo_error"),
                "mensaje": resultado_asesora.get("mensaje_cliente"),
                "error": resultado_asesora.get("error"),
            }
        )

    resultado_semestre = await _actualizar_semestre_core(
        payload=payload,
        accion="actualizar_semestre",
        id_debtor=str(id_debtor),
        token=token or "",
        profile=profile or {},
        natural_id=natural_id,
        requerir_dato=False,
    )
    resultados.append(resultado_semestre)
    if not resultado_semestre.get("ok"):
        campo_pendiente = resultado_semestre.get("campo_pendiente_manual")
        if campo_pendiente:
            campos_pendientes_manual.append(campo_pendiente)
        campos_no_actualizados.append(
            {
                "name": "nDeSemestre",
                "label": "Numero de semestre",
                "tipo_error": resultado_semestre.get("tipo_error"),
                "mensaje": resultado_semestre.get("mensaje_cliente"),
                "error": resultado_semestre.get("error"),
            }
        )

    latest_profile, latest_response, latest_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=natural_id,
    )
    campos_faltantes = []
    if latest_error:
        campos_no_actualizados.append(
            {
                "name": "consulta_final_perfil",
                "label": "Consulta final de perfil",
                "tipo_error": latest_error.get("tipo_error"),
                "mensaje": latest_error.get("mensaje_cliente"),
                "error": latest_error.get("error"),
            }
        )
    else:
        latest_data = latest_response.get("data", {})
        campos_faltantes = _campos_faltantes_legibles(
            latest_profile or {},
            latest_data if isinstance(latest_data, dict) else {},
        )

    ok = len(campos_no_actualizados) == 0
    if ok and not campos_faltantes:
        mensaje = "Campos de autogestion actualizados correctamente en Kuenta."
    elif ok:
        mensaje = _mensaje_campos_faltantes(campos_faltantes)
    else:
        mensaje = "No pudimos actualizar todos los campos en Kuenta. Revisa los campos no actualizados."

    return _respuesta_powerbot(
        {
            "ok": ok,
            "accion": accion,
            "mensaje_cliente": mensaje,
            "natural_id": natural_id,
            "resultados": resultados,
            "campos_no_actualizados": campos_no_actualizados,
            "campos_pendientes_manual": campos_pendientes_manual,
            "campos_faltantes": campos_faltantes,
            "total_campos_faltantes": len(campos_faltantes),
        }
    )


@enrutador.post("/buscar-deudor")
async def buscar_deudor(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "buscar_deudor"
    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    telefono = _obtener(payload, "telefono", "phone", "numero_telefono", "numeroTelefono")
    if not telefono:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="payload_incompleto",
                mensaje_cliente="Falta el numero de telefono para buscar el cliente en Kuenta.",
                error={"campo_faltante": "telefono"},
            )
        )

    debtor, response, debtor_error = await _buscar_debtor_por_telefono(str(telefono), accion, token or "")
    if debtor_error:
        return _respuesta_powerbot(debtor_error)

    return _respuesta_powerbot(
        {
            "ok": True,
            "accion": accion,
            "mensaje_cliente": "Cliente encontrado correctamente en Kuenta.",
            "deudor": {
                "id_debtor": (debtor or {}).get("ID") or (debtor or {}).get("id"),
                "nombre_debtor": _nombre_debtor(debtor or {}),
                "telefono": (debtor or {}).get("phone"),
                "identificacion": (debtor or {}).get("idNumber"),
                "profile_id": (((debtor or {}).get("profile") or {}).get("ID") if isinstance((debtor or {}).get("profile"), dict) else None),
                "natural_id": (
                    ((((debtor or {}).get("profile") or {}).get("natural") or {}).get("ID"))
                    if isinstance((debtor or {}).get("profile"), dict)
                    and isinstance(((debtor or {}).get("profile") or {}).get("natural"), dict)
                    else None
                ),
            },
            "respuesta_kuenta_status": response.get("status"),
        }
    )


@enrutador.post("/campos-detalle")
async def campos_detalle(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "campos_detalle"
    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    id_debtor, debtor, debtor_error = await _resolver_debtor_desde_payload(payload, accion, token or "")
    if debtor_error:
        return _respuesta_powerbot(debtor_error)

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, response, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    response_data = response.get("data", {}) if isinstance(response.get("data"), dict) else {}
    profiles = _extraer_profiles(response_data)
    detalles = [_detalle_profile_campos(item, response_data) for item in profiles]
    selected_detail = _detalle_profile_campos(profile or {}, response_data)
    id_credito = _resolver_id_credito(payload)
    campos_credito = None
    if id_credito:
        payable_data, _, payable_error = await _consultar_payable(
            id_credito=id_credito,
            id_debtor=str(id_debtor),
            accion=accion,
            token=token or "",
        )
        if payable_error:
            campos_credito = {
                "ok": False,
                "tipo_error": payable_error.get("tipo_error"),
                "mensaje_cliente": payable_error.get("mensaje_cliente"),
                "error": payable_error.get("error"),
            }
        else:
            resumen_payable = _campos_faltantes_desde_payable(payable_data or {})
            _log_campos_faltantes_credito(
                accion=accion,
                id_debtor=str(id_debtor),
                id_credito=id_credito,
                resumen=resumen_payable,
            )
            campos_credito = {
                "ok": True,
                "id_credito": id_credito,
                **resumen_payable,
            }
    return _respuesta_powerbot(
        {
            "ok": True,
            "accion": accion,
            "mensaje_cliente": "Campos del perfil consultados correctamente en Kuenta.",
            "id_debtor": id_debtor,
            "debtor": {
                "id": (debtor or {}).get("ID") or id_debtor,
                "nombre": _nombre_debtor(debtor or {}) or selected_detail.get("nombre_debtor"),
                "telefono": (debtor or {}).get("phone"),
                "identificacion": (debtor or {}).get("idNumber"),
            },
            "perfil_seleccionado": selected_detail,
            "perfiles": detalles,
            "total_perfiles": len(detalles),
            "campos_credito": campos_credito,
        }
    )


@enrutador.post("/actualizar-campo")
async def actualizar_campo(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "actualizar_campo"
    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    id_debtor, debtor, debtor_error = await _resolver_debtor_desde_payload(payload, accion, token or "")
    if debtor_error:
        return _respuesta_powerbot(debtor_error)

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, _, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    natural_id = _resolver_natural_id(payload, profile or {})
    if not natural_id:
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="natural_id_no_encontrado",
                mensaje_cliente="No pudimos identificar el perfil natural del cliente en Kuenta.",
                error={"campo_faltante": "natural_id"},
            )
        )

    field, field_error = _resolver_custom_field_actualizable(payload, profile or {})
    if field_error:
        return _respuesta_powerbot(field_error)

    value_type, value, value_error = _extraer_valor_actualizacion(payload, field or {})
    if value_error:
        return _respuesta_powerbot(value_error)

    custom_field, build_error = _construir_custom_field_actualizacion(
        payload,
        field or {},
        value_type or "value",
        value,
    )
    if build_error:
        return _respuesta_powerbot(build_error)

    current_value = _valor_campo_detalle(field or {})
    updated, update_response = await _actualizar_custom_fields(
        accion=accion,
        id_debtor=str(id_debtor),
        natural_id=natural_id,
        custom_fields=[custom_field or {}],
        token=token or "",
    )
    if not updated:
        data = update_response.get("data", {})
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error=data.get("tipo_error", "error_kuenta") if isinstance(data, dict) else "error_kuenta",
                mensaje_cliente="No pudimos actualizar el campo en Kuenta. Intenta nuevamente.",
                error={"status_kuenta": update_response.get("status"), "respuesta_kuenta": data},
            )
        )

    latest_profile, verify_response, verify_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=natural_id,
    )
    if verify_error:
        return _respuesta_powerbot(verify_error)

    latest_field = _buscar_custom_field(latest_profile or {}, str(custom_field.get("name"))) or _buscar_custom_field_por_id(
        latest_profile or {},
        str(custom_field.get("id")),
    )
    latest_value = _valor_campo_detalle(latest_field or {}) if latest_field else None
    if not latest_field or not _valor_actualizado_verificado(latest_value, value, value_type or "value"):
        return _respuesta_powerbot(
            _error_response(
                accion=accion,
                tipo_error="actualizacion_no_verificada",
                mensaje_cliente="Kuenta recibio la solicitud, pero no pudimos confirmar que el campo quedara actualizado.",
                error={
                    "status_kuenta": verify_response.get("status"),
                    "campo": custom_field.get("name"),
                    "tipo_valor": value_type,
                    "valor_esperado": value,
                    "valor_obtenido": latest_value,
                },
            )
        )

    return _respuesta_powerbot(
        {
            "ok": True,
            "accion": accion,
            "mensaje_cliente": f"Campo {custom_field.get('name')} actualizado correctamente en Kuenta.",
            "id_debtor": id_debtor,
            "debtor": {
                "id": (debtor or {}).get("ID") or id_debtor,
                "nombre": _nombre_debtor(debtor or {}) or _nombre_debtor_desde_profile(latest_profile or {}),
            },
            "natural_id": natural_id,
            "campo_actualizado": {
                "name": custom_field.get("name"),
                "id": custom_field.get("id"),
                "configID": custom_field.get("configID"),
                "tipo_valor": value_type,
                "valor_anterior": current_value,
                "valor_actual": latest_value,
            },
        }
    )


@enrutador.post("/campos-faltantes")
async def campos_faltantes(payload: Dict[str, Any] = Body(default_factory=dict)):
    accion = "campos_faltantes"
    token, token_error = await _obtener_token_para_powerbot(accion)
    if token_error:
        return _respuesta_powerbot(token_error)

    id_debtor, _, debtor_error = await _resolver_debtor_desde_payload(payload, accion, token or "")
    if debtor_error:
        return _respuesta_powerbot(debtor_error)

    id_credito = _resolver_id_credito(payload)
    if id_credito:
        payable_data, _, payable_error = await _consultar_payable(
            id_credito=id_credito,
            id_debtor=str(id_debtor),
            accion=accion,
            token=token or "",
        )
        if payable_error:
            return _respuesta_powerbot(payable_error)
        resumen = _campos_faltantes_desde_payable(payable_data or {})
        _log_campos_faltantes_credito(
            accion=accion,
            id_debtor=str(id_debtor),
            id_credito=id_credito,
            resumen=resumen,
        )
        return _respuesta_powerbot(
            {
                "ok": True,
                "accion": accion,
                "fuente": "payable",
                "id_credito": id_credito,
                "id_debtor": id_debtor,
                "mensaje_cliente": _mensaje_campos_requeridos_credito(
                    resumen.get("campos_requeridos_faltantes", [])
                ),
                **resumen,
            }
        )

    natural_id_payload = _obtener(payload, "natural_id", "naturalID", "naturalId", "natural_ID")
    profile, response, profile_error = await _consultar_perfil(
        str(id_debtor),
        accion,
        token or "",
        natural_id=str(natural_id_payload).strip() if natural_id_payload else None,
    )
    if profile_error:
        return _respuesta_powerbot(profile_error)

    data = response.get("data", {})
    missing = _campos_faltantes_legibles(profile or {}, data if isinstance(data, dict) else {})
    logger.info(
        "Campos requeridos faltantes Kuenta por perfil | accion=%s | id_debtor=%s | natural_id=%s | total=%s | campos=%s",
        accion,
        id_debtor,
        str(natural_id_payload).strip() if natural_id_payload else _natural_id(profile or {}),
        len(missing),
        [str(field.get("label") or field.get("name") or "") for field in missing[:80]],
    )
    return _respuesta_powerbot(
        {
            "ok": True,
            "accion": accion,
            "mensaje_cliente": _mensaje_campos_faltantes(missing),
            "campos_faltantes": missing,
            "total_campos_faltantes": len(missing),
        }
    )
