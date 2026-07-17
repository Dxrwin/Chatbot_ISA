"""Reglas por proceso y configuracion dinamica de destinos Bitrix24."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import aiomysql

from utils.config import settings


PROCESOS_SOPORTADOS = frozenset({"renovaciones", "cobranzas"})

LINKS_RENOVACION_POR_UNIVERSIDAD = {
    "cuc": "https://one2credit-app.kuenta.co/product/f1310155-624b-4e6f-9055-b50394b1e457",
    "unibarranquilla": "https://one2credit-app.kuenta.co/product/55b65c52-109d-4507-840e-6eff589b6293",
    "uninorte": "https://one2credit-app.kuenta.co/product/85dee14a-cf4c-4303-910b-458604573411",
    "areandina": "https://www.one2credit.com/solicitar-credito/",
    "america": "https://one2credit-app.kuenta.co/product/8e811fa4-0c54-47d6-8f75-9d26d1c7966c",
    "eafit": "https://one2credit-app.kuenta.co/product/97c29b7d-08a6-4d8b-951b-9bfbb519eda6",
    "ean": "https://one2credit-app.kuenta.co/product/85044138-6a7c-4909-bbca-27614393a30d",
    "rafael_nunez": "https://www.one2credit.com/solicitar-credito/",
    "reformada": "https://one2credit-app.kuenta.co/product/9be0fd2c-50d7-4056-9bb3-904dc1abd7e8",
    "usb_bogota": "https://one2credit-app.kuenta.co/product/7057ee14-1171-453d-81b9-a83c66013205",
    "usb_cali": "https://one2credit-app.kuenta.co/product/afb4d6c7-7e72-4823-bfcf-d96e70b24361",
    "usb_cartagena": "https://one2credit-app.kuenta.co/product/52040a4b-ae61-4b90-b8a6-8ad3989eff10",
    "utb": "https://one2credit-app.kuenta.co/product/38c6fb6f-ea0f-4902-a157-4f2d23a94e55",
}

VARIABLES_SALIDA_POR_PROCESO = {
    "renovaciones": frozenset(
        {
            "objetivo",
            "interes",
            "interes_renovar",
            "intrsrenovarbool",
            "resumen",
            "resumenllamada",
            "resumen_llamada",
            "recording_url",
            "whasapinformado",
            "whatsappinformado",
            "whatsapp_informado",
            "aceptainfocorreo",
            "aceptoinfocorreo",
            "acepta_info_correo",
            "identificacion",
            "cedula",
            "numero_identificacion",
            "documento",
        }
    ),
    "cobranzas": frozenset(
        {
            "objetivo",
            "interes_pagar",
            "intencion_pago",
            "intpago",
            "pago_hoy",
            "gestion_final",
            "tipo_gestion",
            "motivo_principal",
            "altura_mora",
            "valor_confirmado",
            "valor_a_pagar",
            "monto_acordado",
            "monto_pactado",
            "fechacuerdopago",
            "fecha_compromiso_pago",
            "fecha_limite_pago",
            "fecha_acuerdo_pago",
            "detalle_acuerdo",
            "requiere_seguimiento",
            "requseguimiento",
            "dudas",
            "quejas",
            "resumen",
            "resumenllamada",
            "resumen_llamada",
        }
    ),
}

# Cada clave representa un dato de negocio. El codigo UF puede sustituirse en
# MySQL sin modificar el constructor del deal.
MAPEOS_CAMPOS_PREDETERMINADOS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "renovaciones": {
        "interes_lista": {
            "codigo_campo_bitrix": "UF_CRM_1784237027406",
            "tipo_campo": "lista",
            "valor_positivo": "3460",
            "valor_negativo": "3462",
        },
        "resumen_llamada": {
            "codigo_campo_bitrix": "UF_CRM_1784236905493",
            "tipo_campo": "texto",
            "valor_positivo": None,
            "valor_negativo": None,
        },
        "identificacion_cliente": {
            "codigo_campo_bitrix": "UF_CRM_1784237079229",
            "tipo_campo": "texto",
            "valor_positivo": None,
            "valor_negativo": None,
        },
        "interes_texto": {
            "codigo_campo_bitrix": "UF_CRM_1784237652950",
            "tipo_campo": "texto_booleano",
            "valor_positivo": "SI",
            "valor_negativo": "NO",
        },
        "recording_url": {
            "codigo_campo_bitrix": "UF_CRM_1784306887180",
            "tipo_campo": "texto",
            "valor_positivo": None,
            "valor_negativo": None,
        },
        "universidad": {
            "codigo_campo_bitrix": "UF_CRM_1784320678053",
            "tipo_campo": "texto",
            "valor_positivo": None,
            "valor_negativo": None,
        },
        "link_universidad": {
            "codigo_campo_bitrix": "UF_CRM_1784320735188",
            "tipo_campo": "texto",
            "valor_positivo": None,
            "valor_negativo": None,
        },
    },
    "cobranzas": {
        "pagaduria": {"codigo_campo_bitrix": "UF_CRM_1773780818920", "tipo_campo": "texto"},
        "cuotas_mora": {"codigo_campo_bitrix": "UF_CRM_1773781100010", "tipo_campo": "texto"},
        "valor_mora": {"codigo_campo_bitrix": "UF_CRM_1773841728850", "tipo_campo": "numero"},
        "valor_confirmado": {"codigo_campo_bitrix": "UF_CRM_1773841764862", "tipo_campo": "numero"},
        "resumen_llamada": {"codigo_campo_bitrix": "UF_CRM_1773841785299", "tipo_campo": "texto"},
        "detalle_acuerdo": {"codigo_campo_bitrix": "UF_CRM_1773841804045", "tipo_campo": "texto"},
        "gestion_final": {"codigo_campo_bitrix": "UF_CRM_1778864749613", "tipo_campo": "texto"},
        "motivo_principal": {"codigo_campo_bitrix": "UF_CRM_1778864857436", "tipo_campo": "texto"},
        "interes_pagar": {"codigo_campo_bitrix": "UF_CRM_1778864879029", "tipo_campo": "texto"},
        "pago_hoy": {"codigo_campo_bitrix": "UF_CRM_1778864893956", "tipo_campo": "texto"},
        "fecha_acuerdo_pago": {"codigo_campo_bitrix": "UF_CRM_1778864914788", "tipo_campo": "fecha"},
        "id_libranza": {"codigo_campo_bitrix": "UF_CRM_1778865126453", "tipo_campo": "texto"},
        "telefono_cliente": {"codigo_campo_bitrix": "UF_CRM_1778865231524", "tipo_campo": "texto"},
        "identificacion_cliente": {"codigo_campo_bitrix": "UF_CRM_1778865296676", "tipo_campo": "texto"},
        "enlace_pago": {"codigo_campo_bitrix": "UF_CRM_1779835103174", "tipo_campo": "url"},
    },
}


def campos_logicos_soportados(codigo_proceso: str) -> List[str]:
    proceso = normalizar_codigo_proceso(codigo_proceso)
    return sorted(MAPEOS_CAMPOS_PREDETERMINADOS.get(proceso, {}).keys())


def _normalizar_clave(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    texto = re.sub(r"[^a-z0-9_]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _normalizar_texto(valor: Any) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return _normalizar_clave(valor)


def obtener_link_renovacion_universidad(universidad: Any) -> Optional[str]:
    """Resuelve el enlace de renovacion tolerando prefijos, tildes y mayusculas."""
    normalizada = _normalizar_clave(universidad)
    if not normalizada:
        return None

    candidatas = [normalizada]
    for prefijo in ("universidad_de_", "universidad_"):
        if normalizada.startswith(prefijo):
            candidatas.append(normalizada[len(prefijo):])

    for candidata in candidatas:
        enlace = LINKS_RENOVACION_POR_UNIVERSIDAD.get(candidata)
        if enlace:
            return enlace
    return None


def _texto(valor: Any) -> Optional[str]:
    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio or None


def _es_verdadero(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return _normalizar_texto(valor) in {
        "true",
        "si",
        "s",
        "yes",
        "y",
        "1",
        "afirmativo",
        "interesado",
    }


def _obtener(datos: Dict[str, Any], *nombres: str) -> Any:
    normalizados = {_normalizar_clave(k): v for k, v in (datos or {}).items()}
    for nombre in nombres:
        clave = _normalizar_clave(nombre)
        if clave in normalizados:
            return normalizados[clave]
    return None


def _obtener_con_nombre(datos: Dict[str, Any], *nombres: str) -> tuple[str, Any]:
    """Devuelve el alias normalizado que realmente llego junto con su valor."""
    normalizados = {
        _normalizar_clave(clave): valor
        for clave, valor in (datos or {}).items()
    }
    for nombre in nombres:
        clave = _normalizar_clave(nombre)
        if clave in normalizados:
            return clave, normalizados[clave]
    return _normalizar_clave(nombres[0]) if nombres else "", None


def filtrar_variables_salida_proceso(
    codigo_proceso: str,
    variables_salida: Dict[str, Any],
) -> Dict[str, Any]:
    """Construye una vista de trazabilidad sin mezclar variables de procesos."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    permitidas = VARIABLES_SALIDA_POR_PROCESO.get(codigo, frozenset())
    return {
        _normalizar_clave(clave): valor
        for clave, valor in (variables_salida or {}).items()
        if _normalizar_clave(clave) in permitidas
    }


def normalizar_codigo_proceso(valor: Any) -> str:
    """Normaliza el objetivo de la llamada a un codigo de proceso estable."""
    normalizado = _normalizar_texto(valor)
    alias = {
        "renovacion": "renovaciones",
        "renovaciones": "renovaciones",
        "renovar": "renovaciones",
        "renovacion_credito": "renovaciones",
        "renovacion_de_credito": "renovaciones",
        "cobranza": "cobranzas",
        "cobranzas": "cobranzas",
        "cobro": "cobranzas",
        "cobros": "cobranzas",
    }
    return alias.get(normalizado, normalizado)


def resolver_codigo_proceso(
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
) -> str:
    """Resuelve el proceso respetando la precedencia definida por el webhook."""
    valor = _obtener(variables_salida, "objetivo")
    if valor is None:
        valor = _obtener(variables_entrada, "OBJETIVO")
    if valor is None:
        valor = _obtener(variables_entrada, "SEGMENTO_CAMPANA")
    if valor is None:
        valor = _obtener(variables_entrada, "LINEA_CREDITO")
    return normalizar_codigo_proceso(valor)


def _resultado_interes(
    *,
    codigo_proceso: str,
    interes: Any,
    campo_interes: str,
) -> Dict[str, Any]:
    aprobado = _es_verdadero(interes)
    bloqueadores = [] if aprobado else [f"{campo_interes} no contiene interes explicito."]
    senales = [f"{campo_interes}=true"] if aprobado else []
    return {
        "aprobado": aprobado,
        "approved": aprobado,
        "codigo_proceso": codigo_proceso,
        "interes_detectado": aprobado,
        "campo_interes": campo_interes,
        "valor_interes": interes,
        "senales_positivas": senales,
        "positive_signals": senales,
        "bloqueadores": bloqueadores,
        "blockers": bloqueadores,
        "advertencias": [],
        "warnings": [],
    }


def evaluar_interes_renovaciones(variables_salida: Dict[str, Any]) -> Dict[str, Any]:
    """Evalua solo el interes propio del proceso de renovaciones."""
    campo_interes, interes = _obtener_con_nombre(
        variables_salida,
        "interes",
        "interes_renovar",
        "intrsrenovarbool",
    )
    return _resultado_interes(
        codigo_proceso="renovaciones",
        interes=interes,
        campo_interes=campo_interes,
    )


def evaluar_interes_cobranzas(variables_salida: Dict[str, Any]) -> Dict[str, Any]:
    """Evalua solo el interes de pago propio del proceso de cobranzas."""
    campo_interes, interes = _obtener_con_nombre(
        variables_salida,
        "interes_pagar",
        "intencion_pago",
        "intpago",
    )
    return _resultado_interes(
        codigo_proceso="cobranzas",
        interes=interes,
        campo_interes=campo_interes,
    )


def evaluar_interes_proceso(
    codigo_proceso: str,
    variables_salida: Dict[str, Any],
) -> Dict[str, Any]:
    if codigo_proceso == "renovaciones":
        return evaluar_interes_renovaciones(variables_salida)
    if codigo_proceso == "cobranzas":
        return evaluar_interes_cobranzas(variables_salida)
    raise ValueError(f"Proceso no soportado: {codigo_proceso or 'vacio'}")


async def _conectar_mysql() -> aiomysql.Connection:
    return await aiomysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD_RENOVACION,
        db=settings.DB_NAME_RENOVACION,
    )


async def obtener_destino_bitrix_proceso(
    codigo_proceso: str,
    *,
    solo_activo: bool = True,
) -> Optional[Dict[str, Any]]:
    codigo = normalizar_codigo_proceso(codigo_proceso)
    query = """
        SELECT id, codigo_proceso, nombre_proceso, category_id, stage_id,
               nombre_etapa, activo, creado_en, actualizado_en
        FROM bitrix_configuracion_procesos
        WHERE codigo_proceso = %s
    """
    params: List[Any] = [codigo]
    if solo_activo:
        query += " AND activo = 1"
    query += " LIMIT 1"

    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, params)
            return await cursor.fetchone()
    finally:
        conexion.close()


async def listar_destinos_bitrix_procesos(
    activo: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    query = """
        SELECT id, codigo_proceso, nombre_proceso, category_id, stage_id,
               nombre_etapa, activo, creado_en, actualizado_en
        FROM bitrix_configuracion_procesos
    """
    params: List[Any] = []
    if activo is not None:
        query += " WHERE activo = %s"
        params.append(1 if activo else 0)
    query += " ORDER BY codigo_proceso ASC"

    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, params)
            filas = await cursor.fetchall()
            return list(filas or [])
    finally:
        conexion.close()


async def asignar_destino_bitrix_proceso(
    *,
    codigo_proceso: str,
    nombre_proceso: str,
    category_id: int,
    stage_id: str,
    nombre_etapa: Optional[str],
    activo: bool,
) -> Dict[str, Any]:
    codigo = normalizar_codigo_proceso(codigo_proceso)
    query = """
        INSERT INTO bitrix_configuracion_procesos (
            codigo_proceso, nombre_proceso, category_id, stage_id,
            nombre_etapa, activo
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            nombre_proceso = VALUES(nombre_proceso),
            category_id = VALUES(category_id),
            stage_id = VALUES(stage_id),
            nombre_etapa = VALUES(nombre_etapa),
            activo = VALUES(activo),
            actualizado_en = CURRENT_TIMESTAMP
    """
    params = (
        codigo,
        nombre_proceso.strip(),
        int(category_id),
        stage_id.strip(),
        _texto(nombre_etapa),
        1 if activo else 0,
    )

    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor() as cursor:
            await cursor.execute(query, params)
        await conexion.commit()
    except Exception:
        await conexion.rollback()
        raise
    finally:
        conexion.close()

    destino = await obtener_destino_bitrix_proceso(codigo, solo_activo=False)
    if destino is None:
        raise RuntimeError("La configuracion Bitrix no pudo recuperarse despues de guardarla.")
    return destino


async def desactivar_destino_bitrix_proceso(codigo_proceso: str) -> bool:
    codigo = normalizar_codigo_proceso(codigo_proceso)
    query = """
        UPDATE bitrix_configuracion_procesos
        SET activo = 0, actualizado_en = CURRENT_TIMESTAMP
        WHERE codigo_proceso = %s
    """
    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor() as cursor:
            await cursor.execute(query, (codigo,))
            actualizado = cursor.rowcount > 0
        await conexion.commit()
        return actualizado
    except Exception:
        await conexion.rollback()
        raise
    finally:
        conexion.close()


async def listar_mapeos_campos_configurados(
    codigo_proceso: str,
    activo: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Lista las sustituciones de campos UF guardadas para un proceso."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    query = """
        SELECT id, codigo_proceso, campo_logico, codigo_campo_bitrix,
               tipo_campo, valor_positivo, valor_negativo, activo,
               creado_en, actualizado_en
        FROM bitrix_campos_proceso
        WHERE codigo_proceso = %s
    """
    params: List[Any] = [codigo]
    if activo is not None:
        query += " AND activo = %s"
        params.append(1 if activo else 0)
    query += " ORDER BY campo_logico ASC"

    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, params)
            filas = await cursor.fetchall()
            return list(filas or [])
    finally:
        conexion.close()


async def obtener_mapeo_campo_configurado(
    codigo_proceso: str,
    campo_logico: str,
) -> Optional[Dict[str, Any]]:
    """Obtiene el registro persistido de un campo, aunque este inactivo."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = _normalizar_clave(campo_logico)
    query = """
        SELECT id, codigo_proceso, campo_logico, codigo_campo_bitrix,
               tipo_campo, valor_positivo, valor_negativo, activo,
               creado_en, actualizado_en
        FROM bitrix_campos_proceso
        WHERE codigo_proceso = %s AND campo_logico = %s
        LIMIT 1
    """
    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, (codigo, campo))
            return await cursor.fetchone()
    finally:
        conexion.close()


async def asignar_mapeo_campo_proceso(
    *,
    codigo_proceso: str,
    campo_logico: str,
    codigo_campo_bitrix: str,
    tipo_campo: str,
    valor_positivo: Optional[str] = None,
    valor_negativo: Optional[str] = None,
    activo: bool = True,
) -> Dict[str, Any]:
    """Crea o reemplaza atomicamente el codigo UF de un dato de negocio."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = _normalizar_clave(campo_logico)
    query = """
        INSERT INTO bitrix_campos_proceso (
            codigo_proceso, campo_logico, codigo_campo_bitrix, tipo_campo,
            valor_positivo, valor_negativo, activo
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            codigo_campo_bitrix = VALUES(codigo_campo_bitrix),
            tipo_campo = VALUES(tipo_campo),
            valor_positivo = VALUES(valor_positivo),
            valor_negativo = VALUES(valor_negativo),
            activo = VALUES(activo),
            actualizado_en = CURRENT_TIMESTAMP
    """
    params = (
        codigo,
        campo,
        codigo_campo_bitrix.strip().upper(),
        _normalizar_clave(tipo_campo),
        _texto(valor_positivo),
        _texto(valor_negativo),
        1 if activo else 0,
    )
    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor() as cursor:
            await cursor.execute(query, params)
        await conexion.commit()
    except Exception:
        await conexion.rollback()
        raise
    finally:
        conexion.close()

    mapeo = await obtener_mapeo_campo_configurado(codigo, campo)
    if mapeo is None:
        raise RuntimeError("El mapeo no pudo recuperarse despues de guardarlo.")
    return mapeo


async def desactivar_mapeo_campo_proceso(
    codigo_proceso: str,
    campo_logico: str,
) -> bool:
    """Desactiva el campo y evita que reaparezca el valor predeterminado."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    campo = _normalizar_clave(campo_logico)
    query = """
        UPDATE bitrix_campos_proceso
        SET activo = 0, actualizado_en = CURRENT_TIMESTAMP
        WHERE codigo_proceso = %s AND campo_logico = %s
    """
    conexion = await _conectar_mysql()
    try:
        async with conexion.cursor() as cursor:
            await cursor.execute(query, (codigo, campo))
            actualizado = cursor.rowcount > 0
        await conexion.commit()
        return actualizado
    except Exception:
        await conexion.rollback()
        raise
    finally:
        conexion.close()


async def resolver_mapeos_campos_proceso(
    codigo_proceso: str,
) -> Dict[str, Dict[str, Any]]:
    """Combina los campos predeterminados con las sustituciones de MySQL."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    resueltos: Dict[str, Dict[str, Any]] = {
        campo: {**configuracion, "activo": True, "origen": "predeterminado"}
        for campo, configuracion in MAPEOS_CAMPOS_PREDETERMINADOS.get(codigo, {}).items()
    }
    for fila in await listar_mapeos_campos_configurados(codigo):
        campo = _normalizar_clave(fila.get("campo_logico"))
        if not bool(fila.get("activo")):
            resueltos.pop(campo, None)
            continue
        resueltos[campo] = {
            "codigo_campo_bitrix": fila.get("codigo_campo_bitrix"),
            "tipo_campo": fila.get("tipo_campo"),
            "valor_positivo": fila.get("valor_positivo"),
            "valor_negativo": fila.get("valor_negativo"),
            "activo": True,
            "origen": "base_datos",
        }
    return resueltos


def _entero_o_none(valor: Any) -> Optional[int]:
    texto = _texto(valor)
    if not texto:
        return None
    texto = re.sub(r"[^\d,.\-]", "", texto)
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".") else texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        texto = texto.replace(",", ".") if len(partes[-1]) <= 2 else texto.replace(",", "")
    elif "." in texto and len(texto.split(".")[-1]) > 2:
        texto = texto.replace(".", "")
    try:
        return int(Decimal(texto).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def _campos_comunes(
    *,
    variables_entrada: Dict[str, Any],
    id_contacto: int,
    category_id: int,
    stage_id: str,
    codigo_proceso: str,
    comments: Optional[str],
    assigned_by_id: Optional[int],
) -> Dict[str, Any]:
    nombre = _texto(_obtener(variables_entrada, "NOMBRE", "NOMBRE_TITULAR", "NOMBRE_ESTUDIANTE")) or "Cliente"
    cedula = _texto(_obtener(variables_entrada, "CEDULA", "CEDULA_TITULAR"))
    titulo = f"Resultado llamada IA - {codigo_proceso} - {nombre}"
    if cedula:
        titulo += f" - CC {cedula}"
    campos: Dict[str, Any] = {
        "title": titulo,
        "categoryId": int(category_id),
        "stageId": stage_id,
        "comments": comments,
        "contactId": int(id_contacto),
        "contactIds": [int(id_contacto)],
    }
    if assigned_by_id:
        campos["assignedById"] = int(assigned_by_id)
    return campos


def _si_no(valor: Any) -> str:
    if valor is None or str(valor).strip() == "":
        return "No informado"
    return "Si" if _es_verdadero(valor) else "No"


def _convertir_valor_configurado(valor: Any, tipo_campo: Optional[str]) -> Any:
    """Convierte IDs de listas a entero y conserva los demas tipos."""
    if valor is None:
        return None
    if _normalizar_clave(tipo_campo) == "lista":
        texto = str(valor).strip()
        return int(texto) if texto.isdigit() else valor
    return valor


def _aplicar_campo_configurado(
    campos: Dict[str, Any],
    mapeos: Dict[str, Dict[str, Any]],
    campo_logico: str,
    valor: Any,
    *,
    booleano: bool = False,
) -> None:
    configuracion = mapeos.get(campo_logico)
    if not configuracion:
        return
    codigo_campo = _texto(configuracion.get("codigo_campo_bitrix"))
    if not codigo_campo:
        return
    if booleano:
        clave_valor = "valor_positivo" if _es_verdadero(valor) else "valor_negativo"
        valor = configuracion.get(clave_valor)
    campos[codigo_campo] = _convertir_valor_configurado(
        valor,
        configuracion.get("tipo_campo"),
    )


def construir_campos_deal_renovaciones(
    *,
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    id_contacto: int,
    category_id: int,
    stage_id: str,
    assigned_by_id: Optional[int] = None,
    mapeos_campos: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    interes = _obtener(variables_salida, "interes", "interes_renovar", "intrsrenovarbool")
    resumen = _texto(_obtener(variables_salida, "resumen", "resumenllamada", "resumen_llamada")) or "No informado"
    whatsapp = _obtener(variables_salida, "whasapinformado", "whatsappinformado", "whatsapp_informado")
    acepta_correo = _obtener(variables_salida, "aceptainfocorreo", "aceptoinfocorreo", "acepta_info_correo")
    recording_url = _texto(_obtener(variables_salida, "recording_url"))
    universidad = _texto(_obtener(variables_entrada, "UNIVERSIDAD"))
    link_universidad = obtener_link_renovacion_universidad(universidad)
    comentarios = "\n".join(
        [
            "PROCESO: renovaciones",
            f"INTERES EN RENOVAR: {_si_no(interes)}",
            f"RESUMEN: {resumen}",
            f"WHATSAPP INFORMADO: {_si_no(whatsapp)}",
            f"ACEPTA INFORMACION POR CORREO: {_si_no(acepta_correo)}",
        ]
    )[:4000]
    identificacion = _texto(
        _obtener(
            variables_entrada,
            "CEDULA",
            "CEDULA_TITULAR",
            "NUMERO_IDENTIFICACION",
            "DOCUMENTO",
        )
        or _obtener(
            variables_salida,
            "identificacion",
            "cedula",
            "numero_identificacion",
            "documento",
        )
    )
    campos = _campos_comunes(
        variables_entrada=variables_entrada,
        id_contacto=id_contacto,
        category_id=category_id,
        stage_id=stage_id,
        codigo_proceso="renovaciones",
        comments=comentarios,
        assigned_by_id=assigned_by_id,
    )
    mapeos = (
        mapeos_campos
        if mapeos_campos is not None
        else MAPEOS_CAMPOS_PREDETERMINADOS["renovaciones"]
    )
    _aplicar_campo_configurado(campos, mapeos, "interes_lista", interes, booleano=True)
    _aplicar_campo_configurado(campos, mapeos, "resumen_llamada", resumen)
    _aplicar_campo_configurado(campos, mapeos, "identificacion_cliente", identificacion)
    _aplicar_campo_configurado(campos, mapeos, "interes_texto", interes, booleano=True)
    _aplicar_campo_configurado(campos, mapeos, "recording_url", recording_url)
    _aplicar_campo_configurado(campos, mapeos, "universidad", universidad)
    _aplicar_campo_configurado(campos, mapeos, "link_universidad", link_universidad)
    return campos


def construir_campos_deal_cobranzas(
    *,
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    id_contacto: int,
    category_id: int,
    stage_id: str,
    enlace_pago: Optional[str] = None,
    assigned_by_id: Optional[int] = None,
    mapeos_campos: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    pagaduria = _texto(_obtener(variables_entrada, "PAGADURIA"))
    cuotas_mora = _texto(_obtener(variables_entrada, "CUOTAS_EN_MORA", "CUOTA"))
    valor_mora = _entero_o_none(_obtener(variables_entrada, "VALOR_TOTAL_MORA", "MORA_TOTAL", "MORA", "VALOR_MORA"))
    valor_confirmado = _entero_o_none(
        _obtener(variables_salida, "valor_confirmado", "valor_a_pagar", "monto_acordado", "monto_pactado")
        or _obtener(variables_entrada, "VALOR_CONFIRMADO")
    )
    resumen = _texto(_obtener(variables_salida, "resumenllamada", "resumen_llamada", "resumen_gestion"))
    detalle = _texto(_obtener(variables_salida, "detalle_acuerdo"))
    gestion = _texto(_obtener(variables_salida, "gestion_final"))
    motivo = _texto(_obtener(variables_salida, "motivo_principal", "tipo_gestion", "altura_mora"))
    interes = _texto(_obtener(variables_salida, "interes_pagar", "intencion_pago", "intpago"))
    pago_hoy = _texto(_obtener(variables_salida, "pago_hoy"))
    fecha = _texto(_obtener(variables_salida, "fechacuerdopago", "fecha_compromiso_pago", "fecha_limite_pago", "fecha_acuerdo_pago"))
    id_libranza = _texto(_obtener(variables_entrada, "ID_LIBRANZA"))
    telefono = _texto(_obtener(variables_entrada, "TELEFONO"))
    cedula = _texto(_obtener(variables_entrada, "CEDULA"))
    dudas = _texto(_obtener(variables_salida, "dudas"))
    quejas = _texto(_obtener(variables_salida, "quejas"))
    seguimiento = _texto(_obtener(variables_salida, "requiere_seguimiento", "requseguimiento"))
    comentarios = "\n".join(
        linea for linea in [
            "PROCESO: cobranzas",
            f"RESUMEN: {resumen}" if resumen else None,
            f"DETALLE ACUERDO: {detalle}" if detalle else None,
            f"GESTION FINAL: {gestion}" if gestion else None,
            f"DUDAS: {dudas}" if dudas else None,
            f"QUEJAS: {quejas}" if quejas else None,
            f"SEGUIMIENTO: {seguimiento}" if seguimiento else None,
        ] if linea
    )[:4000]
    campos = _campos_comunes(
        variables_entrada=variables_entrada,
        id_contacto=id_contacto,
        category_id=category_id,
        stage_id=stage_id,
        codigo_proceso="cobranzas",
        comments=comentarios or None,
        assigned_by_id=assigned_by_id,
    )
    mapeos = (
        mapeos_campos
        if mapeos_campos is not None
        else MAPEOS_CAMPOS_PREDETERMINADOS["cobranzas"]
    )
    valores_campos = {
        "pagaduria": pagaduria,
        "cuotas_mora": cuotas_mora,
        "valor_mora": valor_mora,
        "valor_confirmado": valor_confirmado,
        "resumen_llamada": resumen,
        "detalle_acuerdo": detalle,
        "gestion_final": gestion,
        "motivo_principal": motivo,
        "interes_pagar": interes,
        "pago_hoy": pago_hoy,
        "fecha_acuerdo_pago": fecha,
        "id_libranza": id_libranza,
        "telefono_cliente": telefono,
        "identificacion_cliente": cedula,
        "enlace_pago": _texto(enlace_pago),
    }
    for campo_logico, valor in valores_campos.items():
        _aplicar_campo_configurado(campos, mapeos, campo_logico, valor)
    if valor_mora is not None:
        campos["opportunity"] = valor_mora
        campos["currencyId"] = "COP"
    return campos


def construir_campos_deal_proceso(
    *,
    codigo_proceso: str,
    variables_entrada: Dict[str, Any],
    variables_salida: Dict[str, Any],
    id_contacto: int,
    category_id: int,
    stage_id: str,
    enlace_pago: Optional[str] = None,
    assigned_by_id: Optional[int] = None,
    mapeos_campos: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Selecciona el constructor exclusivo del proceso resuelto."""
    codigo = normalizar_codigo_proceso(codigo_proceso)
    argumentos = {
        "variables_entrada": variables_entrada,
        "variables_salida": variables_salida,
        "id_contacto": id_contacto,
        "category_id": category_id,
        "stage_id": stage_id,
        "assigned_by_id": assigned_by_id,
        "mapeos_campos": mapeos_campos,
    }
    if codigo == "renovaciones":
        return construir_campos_deal_renovaciones(**argumentos)
    if codigo == "cobranzas":
        return construir_campos_deal_cobranzas(
            **argumentos,
            enlace_pago=enlace_pago,
        )
    raise ValueError(f"Proceso no soportado: {codigo or 'vacio'}")
