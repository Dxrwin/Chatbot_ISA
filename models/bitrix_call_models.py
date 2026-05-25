"""
Modelos Pydantic para el flujo Bitrix24 de llamadas automatizadas.

Estos modelos validan el JSON que llega cuando finaliza una llamada automatizada.

Regla funcional:
- input_variables contiene datos de origen/campaña.
- extracted_variables contiene variables extraídas de la llamada.
- WHATSAPP_DISPONIBLE se recibe, pero NO se usa para buscar cliente.
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_INPUT_VARIABLE_KEYS = frozenset(
    {
        "MORA",
        "CUOTA",
        "CEDULA",
        "CORREO",
        "MOTIVO",
        "NOMBRE",
        "CAMPANIA",
        "TELEFONO",
        "PAGADURIA",
        "MORA_TOTAL",
        "ID_LIBRANZA",
        "OBSERVACIONES",
        "TOTAL_CREDITO",
        "CORREO_CARTERA",
        "WHATSAPP_DISPONIBLE",
    }
)

_INPUT_BLOCK_ALIASES = (
    "input_variables",
    "inputVariables",
    "variables_entrada",
    "input",
)

_EXTRACTED_BLOCK_ALIASES = (
    "extracted_variables",
    "extractedVariables",
    "output_variables",
    "outputVariables",
    "variables_salida",
    "extracted",
)


def _normalize_key(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _canonical_input_field(key: str) -> Optional[str]:
    canonical = {
        "mora": "MORA",
        "cuota": "CUOTA",
        "cedula": "CEDULA",
        "correo": "CORREO",
        "motivo": "MOTIVO",
        "nombre": "NOMBRE",
        "campania": "CAMPANIA",
        "telefono": "TELEFONO",
        "pagaduria": "PAGADURIA",
        "mora_total": "MORA_TOTAL",
        "id_libranza": "ID_LIBRANZA",
        "observaciones": "OBSERVACIONES",
        "total_credito": "TOTAL_CREDITO",
        "correo_cartera": "CORREO_CARTERA",
        "whatsapp_disponible": "WHATSAPP_DISPONIBLE",
    }
    return canonical.get(_normalize_key(key))


def _pop_first(data: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data.pop(key)
    return None


def _normalize_extracted_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    name = (
        item.get("name")
        or item.get("Name")
        or item.get("key")
        or item.get("Key")
    )
    if not name or not str(name).strip():
        return None

    if "value" in item:
        value = item.get("value")
    elif "Value" in item:
        value = item.get("Value")
    else:
        value = item.get("val")

    return {
        "name": str(name).strip(),
        "type": item.get("type") or item.get("Type"),
        "value": value,
        "description": item.get("description") or item.get("Description"),
    }


def _extracted_to_list(extracted: Any) -> List[Dict[str, Any]]:
    if extracted is None:
        return []

    if isinstance(extracted, dict):
        return [
            {"name": str(key).strip(), "value": value}
            for key, value in extracted.items()
            if key is not None and str(key).strip()
        ]

    if isinstance(extracted, list):
        normalized: List[Dict[str, Any]] = []
        for item in extracted:
            parsed = _normalize_extracted_item(item)
            if parsed:
                normalized.append(parsed)
        return normalized

    return []


class BitrixCallInputVariables(BaseModel):
    """
    Variables de entrada recibidas desde la campaña o proveedor de llamadas.

    Se usan para consultar Bitrix:
    - CEDULA -> UF_CRM_1697774324
    - TELEFONO -> phone
    - CORREO -> email

    No se usa para buscar cliente:
    - WHATSAPP_DISPONIBLE
    """

    MORA: Optional[Union[int, float, str]] = None
    CUOTA: Optional[Union[int, float, str]] = None

    CEDULA: Union[int, str] = Field(
        ...,
        description="Documento de identidad del cliente. Campo Bitrix: UF_CRM_1697774324.",
    )

    CORREO: Optional[str] = Field(
        None,
        description="Correo del cliente. Campo Bitrix: email.",
    )

    MOTIVO: Optional[str] = None
    NOMBRE: Optional[str] = None
    CAMPANIA: Optional[str] = None

    TELEFONO: Optional[Union[int, str]] = Field(
        None,
        description="Teléfono del cliente. Campo Bitrix: phone.",
    )

    PAGADURIA: Optional[str] = None
    MORA_TOTAL: Optional[Union[int, float, str]] = None
    ID_LIBRANZA: Optional[str] = None
    OBSERVACIONES: Optional[str] = None
    TOTAL_CREDITO: Optional[Union[int, float, str]] = None
    CORREO_CARTERA: Optional[str] = None

    WHATSAPP_DISPONIBLE: Optional[Union[int, str]] = Field(
        None,
        description="Número operativo/canal externo. Se recibe, pero no identifica al cliente.",
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_input_keys(cls, data: Any) -> Any:
        """
        Acepta claves en cualquier capitalización (cedula, Cedula, CEDULA).
        """

        if not isinstance(data, dict):
            return data

        normalized: Dict[str, Any] = {}

        for key, value in data.items():
            canonical = _canonical_input_field(key)
            target_key = canonical if canonical else key
            normalized[target_key] = value

        return normalized

    @field_validator("CEDULA", mode="before")
    @classmethod
    def validate_cedula_required(cls, value: Any) -> Any:
        """
        Valida que CEDULA venga presente y no vacía.
        """

        if value is None or str(value).strip() == "":
            raise ValueError("CEDULA es obligatoria para consultar el cliente en Bitrix.")
        return value

    @field_validator("CORREO", "CORREO_CARTERA", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Optional[str]:
        """
        Normaliza correos a minúscula si vienen presentes.
        """

        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text.lower()

    @field_validator("TELEFONO", mode="before")
    @classmethod
    def normalize_telefono(cls, value: Any) -> Optional[str]:
        """
        Convierte TELEFONO a string limpio si viene presente.
        """

        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text

    @field_validator("WHATSAPP_DISPONIBLE", mode="before")
    @classmethod
    def normalize_whatsapp_disponible(cls, value: Any) -> Optional[str]:
        """
        Convierte WHATSAPP_DISPONIBLE a string limpio.

        No se usa para búsqueda del cliente.
        Se deja disponible para comentarios, auditoría o datos operativos.
        """

        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text


class BitrixExtractedVariable(BaseModel):
    """
    Variable extraída de la llamada.

    Formato esperado:
    {
        "name": "interes_pagar",
        "type": "string",
        "value": "Si",
        "description": "..."
    }

    description se recibe, pero no se procesa.
    """

    name: str = Field(
        ...,
        description="Nombre de la variable extraída.",
        validation_alias=AliasChoices("name", "Name", "key", "Key"),
    )
    type: Optional[str] = Field(
        None,
        description="Tipo declarado por el proveedor.",
        validation_alias=AliasChoices("type", "Type"),
    )
    value: Any = Field(
        None,
        description="Valor de la variable extraída.",
        validation_alias=AliasChoices("value", "Value", "val"),
    )
    description: Optional[str] = Field(
        None,
        description="Descripción informativa; no se procesa.",
        validation_alias=AliasChoices("description", "Description"),
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """
        Valida que el nombre de la variable no venga vacío.
        """

        if not value or not str(value).strip():
            raise ValueError("El campo name de extracted_variables no puede estar vacío.")
        return value.strip()


class BitrixCallCompletedRequest(BaseModel):
    """
    Request principal del endpoint de llamada finalizada.

    Acepta nombres snake_case y camelCase:
    - input_variables / inputVariables
    - extracted_variables / extractedVariables
    """

    input_variables: BitrixCallInputVariables = Field(
        ...,
        validation_alias=AliasChoices(
            "input_variables",
            "inputVariables",
            "variables_entrada",
            "input",
        ),
    )

    extracted_variables: List[BitrixExtractedVariable] = Field(
        ...,
        validation_alias=AliasChoices(
            "extracted_variables",
            "extractedVariables",
            "output_variables",
            "outputVariables",
            "variables_salida",
            "extracted",
        ),
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, data: Any) -> Any:
        """
        Normaliza el JSON del proveedor antes de validar campos estrictos.

        Soporta:
        - input_variables / inputVariables
        - extracted_variables como lista o diccionario
        - variables de entrada en la raíz del JSON (CEDULA, TELEFONO, etc.)
        """

        if not isinstance(data, dict):
            return data

        payload = dict(data)

        input_block = _pop_first(payload, _INPUT_BLOCK_ALIASES)
        hoisted: Dict[str, Any] = {}

        for key in list(payload.keys()):
            if key in _INPUT_VARIABLE_KEYS:
                hoisted[key] = payload.pop(key)
            else:
                canonical = _canonical_input_field(key)
                if canonical:
                    hoisted[canonical] = payload.pop(key)

        if input_block is None:
            input_block = hoisted
        elif isinstance(input_block, dict):
            merged = dict(hoisted)
            merged.update(input_block)
            input_block = merged
        elif hoisted:
            input_block = hoisted

        if input_block is not None:
            payload["input_variables"] = input_block

        extracted = _pop_first(payload, _EXTRACTED_BLOCK_ALIASES)
        payload["extracted_variables"] = _extracted_to_list(extracted)

        return payload


class BitrixDebugSearchClientRequest(BaseModel):
    """
    Request para endpoint de prueba de búsqueda de cliente en Bitrix.

    Este endpoint no valida intención de pago; solo prueba búsqueda del cliente.
    """

    CEDULA: Union[int, str] = Field(
        ...,
        description="Documento del cliente. Campo Bitrix: UF_CRM_1697774324.",
    )

    TELEFONO: Optional[Union[int, str]] = Field(
        None,
        description="Teléfono del cliente. Campo Bitrix: phone.",
    )

    CORREO: Optional[str] = Field(
        None,
        description="Correo del cliente. Campo Bitrix: email.",
    )

    WHATSAPP_DISPONIBLE: Optional[Union[int, str]] = Field(
        None,
        description="Número operativo/canal externo. No se usa para buscar cliente.",
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    @field_validator("CEDULA", mode="before")
    @classmethod
    def validate_cedula_required(cls, value: Any) -> Any:
        """
        Valida que CEDULA venga presente y no vacía.
        """

        if value is None or str(value).strip() == "":
            raise ValueError("CEDULA es obligatoria.")
        return value

    @field_validator("TELEFONO", "WHATSAPP_DISPONIBLE", mode="before")
    @classmethod
    def cast_phone_like_to_str(cls, value: Any) -> Optional[str]:
        """
        Convierte números telefónicos a string para evitar inconsistencias.
        """

        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text

    @field_validator("CORREO", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Optional[str]:
        """
        Normaliza correo a minúscula.
        """

        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text.lower()


class BitrixLookupCriteriaResponse(BaseModel):
    """
    Modelo opcional de salida interna para documentar criterios de búsqueda.

    No es obligatorio usarlo en el endpoint, pero sirve si quieres tipar respuestas.
    """

    cedula: Optional[str] = None
    telefono_variants: List[str] = Field(default_factory=list)
    correo: Optional[str] = None
    nombre: Optional[str] = None
    id_libranza: Optional[str] = None


class BitrixCallCompletedResponse(BaseModel):
    """
    Modelo opcional de respuesta del endpoint principal.

    Puedes usarlo como response_model si quieres documentar la respuesta.
    """

    ok: bool
    action: str
    validation: Dict[str, Any]
    lookup_criteria: Optional[Dict[str, Any]] = None
    ignored_fields: Optional[Dict[str, str]] = None
    client_found: bool
    client: Optional[Dict[str, Any]] = None
    deal_created: bool = False
    deal: Optional[Dict[str, Any]] = None