from fastapi import HTTPException, logger
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
import asyncio
from utils.auth import obtener_token
from utils.notify_error import error_notify
from utils.config import ORG_ID, API_URL
import httpx
from typing import Dict, Any

# --- Sistema de Caché en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

class ClienteRequest(BaseModel):
    id_cliente: str
    
class FinancingRequest(BaseModel):
    # Define the fields required for financing calculations
    principal: float
    percentage_fee: float
    payment_frequency: int

async def calcular_financiamiento(payload: FinancingRequest):
    """
    Calcula el financiamiento basado en:
    1. Cuota inicial = principal * porcentaje_cuota
    2. Plazo en días = plazo_escogido * paymentFrequency
    3. Consulta a la API Kuenta para obtener el porcentaje de Aval
    4. Calcula desembolso, deducciones y valor a solicitar
    """
    method_name = "calcular_financiamiento"
    linea_producto_notify_error = f"línea_producto={payload.get('linea_producto')}"

    try:
        logger.info(f"###--- Payload recibido: ###---  \n {payload} \n")

        # --- VALIDACIONES DE ENTRADA ---
        linea_producto = payload.get("linea_producto")
        logger.info(f"linea_producto recibido: {linea_producto} \n")
        if not linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'linea_producto' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'linea_producto' en el payload")
        
        # --- PROCESAR SEMESTRE, el dato entra como una palabra ejemplo "segundo semestre" y debe devolver el numero del semestre ---
        semestre_texto = str(payload.get("semestre_renovación_menu", "")).strip().lower()
        semestres_map = {
            "primer semestre": 1,
            "segundo semestre": 2,
            "tercer semestre": 3,
            "cuarto semestre": 4,
            "quinto semestre": 5,
            "sexto semestre": 6,
            "séptimo semestre": 7,
            #"septimo semestre": 7,   # variante sin tilde
            "octavo semestre": 8,
            "noveno semestre": 9,
            "décimo semestre": 10
            #"decimo semestre": 10    # variante sin tilde
        }
        
        if not semestre_texto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'semestre' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'semestre' en el payload")
        
        numero_semestre = semestres_map.get(semestre_texto)
        if numero_semestre is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de semestre '{semestre_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El semestre '{semestre_texto}' no es válido. Use: primer semestre, segundo semestre, etc.")

        # --- PROCESAR PLAZO_VALOR_PAGAR, el dato entra en string y debe devolverse como un numero ---
        plazo_texto = str(payload.get("plazo_valor_pagar")).strip().lower()
        plazo_map = {
            "a un mes": 1,
            "a dos meses": 2,
            "a tres meses": 3,
            "a cuatro meses": 4,
            "a cinco meses": 5,
            "a seis meses": 6
        }

        if not plazo_texto:
            await error_notify(method_name, linea_producto_notify_error, "Falta 'plazo_valor_pagar' en el payload")
            raise HTTPException(status_code=400, detail="Debe incluir 'plazo_valor_pagar' en el payload")

        plazo_valor = plazo_map.get(plazo_texto)
        if plazo_valor is None:
            await error_notify(method_name, linea_producto_notify_error, f"Valor de plazo '{plazo_texto}' no reconocido")
            raise HTTPException(status_code=400, detail=f"El plazo '{plazo_texto}' no es válido. Use: a un mes, a dos meses, etc.")
        
        logger.info(f"plazo_valor procesado: {plazo_valor} \n")
        logger.info (f"numero de semestre procesado: {numero_semestre} \n")

        # Limpieza y conversión del valor principal
        raw_principal = str(payload.get("principal", "0")).strip()
        # Eliminar todos los puntos y comas del número
        cleaned_principal = raw_principal.replace(",", "").replace(".", "")
        
        if not cleaned_principal.isdigit():
            await error_notify(method_name, linea_producto_notify_error, f"El valor principal '{raw_principal}' no es numérico válido")
            raise ValueError(f"El valor principal '{raw_principal}' no es numérico válido")

        principal = float(cleaned_principal)

        # Porcentaje de cuota (sin símbolo %)
        porcentaje_str = str(payload.get("porcentaje_cuota", "0")).replace("%", "").strip()
        porcentaje_cuota = float(porcentaje_str) / 100

        # Plazo y frecuencia
        #plazo_escogido = int(payload.get("plazo_escogido", 0))
        #el plazo procesado de tipo string y transformado a numero es plazo_valor y se le asigna a plazo_escogido para los calculos
        plazo_escogido = plazo_valor
        payment_frequency = int(payload.get("paymentFrequency", 30))
        
        #logger.info (f"plazo escogido para realizar los calculos: {plazo_escogido} \n")
        

        # --- CÁLCULOS INICIALES ---
        valor_cuota_inicial = principal * porcentaje_cuota
        dias_totales = plazo_escogido * payment_frequency

        # --- CONSULTA A API KUENTA ---
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await obtener_token(client)
            if not token:
                await error_notify(method_name, linea_producto_notify_error, "No se pudo obtener token de autenticación")
                raise HTTPException(status_code=401, detail="No se pudo obtener token de autenticación")

            KUENTA_URL = f"https://api.kuenta.co/v1/products/{linea_producto}"
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": ORG_ID,
                "Authorization": token
            }

            try:
                resp = await client.get(KUENTA_URL, headers=headers)
                resp.raise_for_status()
                product_data = resp.json().get("data", {}).get("product", {})
            except httpx.RequestError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de conexión con la API de Kuenta: {e}")
                raise HTTPException(status_code=502, detail=f"Error de conexión con la API de Kuenta: {e}")
            except httpx.HTTPStatusError as e:
                await error_notify(method_name, linea_producto_notify_error, f"Error de respuesta de Kuenta: {e.response.text}")
                raise HTTPException(status_code=e.response.status_code, detail=f"Error de respuesta de Kuenta: {e.response.text}")

        # --- VALIDAR RESPUESTA ---
        if product_data.get("ID") != linea_producto:
            await error_notify(method_name, linea_producto_notify_error, "El ID del producto no coincide")
            raise HTTPException(status_code=404, detail="El ID del producto no coincide")
        
        logger.info(f"ID  del producto obtenido: {product_data.get("ID")} \n")

        aval_porcentaje = next(
            (float(str(c.get("percentage", 0))) for c in product_data.get("costs", []) if c.get("label") == "Aval"),
            None
        )
        if aval_porcentaje is None:
            await error_notify(method_name, linea_producto_notify_error, "No se encontró porcentaje de Aval en el producto")
            raise HTTPException(status_code=404, detail="No se encontró porcentaje de Aval en el producto")
        logger.info(f"Porcentaje de Aval obtenido de la linea: {aval_porcentaje}% \n")
        # --- CÁLCULOS FINALES ---
        valor_desembolsar = principal - valor_cuota_inicial
        if (1 - aval_porcentaje) == 0:
            await error_notify(method_name, linea_producto_notify_error, "El porcentaje de aval no puede ser 100%")
            raise ValueError("El porcentaje de aval no puede ser 100%.")

        valor_solicitar = valor_desembolsar / (1 - aval_porcentaje)
        deducciones_anticipadas = valor_solicitar * aval_porcentaje
        
        # --- FORMATEO PARA DEMOSTRACIÓN ---
        demostracion_valor_producto = f"${principal:,.0f}"
        demostracion_cuota_inicial = f"${valor_cuota_inicial:,.0f}"
        demostracion_valor_desembolsar = f"${valor_desembolsar:,.0f}"
        demostracion_deducciones = f"${deducciones_anticipadas:,.0f}"
        demostracion_valor_solicitar = f"${valor_solicitar:,.0f}"
        
        logger.info (f"numero de semestre procesado: {numero_semestre} semestre \n")
        logger.info(f"plazo_valor_pagar procesado: {plazo_valor} meses \n")
        
        logger.info(f"----- Resumen de cálculos realizados ----- \n")
        logger.info(f"Valor del producto (principal): {demostracion_valor_producto} \n")
        logger.info(f"Cuota inicial (valor_cuota_inicial): {demostracion_cuota_inicial} \n")
        logger.info(f"Valor a desembolsar (valor_desembolsar): {demostracion_valor_desembolsar} \n")
        logger.info(f"Deducciones anticipadas (deducciones_anticipadas): {demostracion_deducciones} \n")
        logger.info(f"Valor a solicitar (valor_solicitar): {demostracion_valor_solicitar} \n")
        logger.info(f"Aval aplicado porcentaje (aval_porcentaje): {aval_porcentaje} \n")
        logger.info(f"Plazo en días (plazo_dias): {dias_totales} \n")
        logger.info(f"Porcentaje escogido (porcentaje_str): {porcentaje_str}% \n")
        
        logger.info("Cálculo completado correctamente. \n")
        logger.info("-------------fin de la ejecución------------------ \n")
        return {
            "valor_producto": principal,
            "cuota_inicial": valor_cuota_inicial,
            "valor_desembolsar": valor_desembolsar,
            "deducciones_anticipadas": deducciones_anticipadas,
            "valor_solicitado": valor_solicitar,
            "aval_aplicado_porcentaje": aval_porcentaje,
            "plazo_dias": dias_totales,
            "porcentaje_escogido": porcentaje_str,
            "numero_semestre": numero_semestre,
            "plazo_valor_pagar_meses": plazo_valor,
            "plazo_escogido_meses": plazo_escogido,
            
            # Agregar valores formateados para demostración
            "valor_producto_demostracion": demostracion_valor_producto,
            "cuota_inicial_demostracion": demostracion_cuota_inicial,
            "valor_desembolsar_demostracion": demostracion_valor_desembolsar,
            "deducciones_anticipadas_demostracion": demostracion_deducciones,
            "valor_solicitado_demostracion": demostracion_valor_solicitar
        }

    except ValueError as e:
        logger.error(f"Error de datos: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error de datos: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except HTTPException as e:
        await error_notify(method_name, linea_producto_notify_error, e.detail)
        raise

    except Exception as e:
        logger.error(f"Error interno inesperado: {e}")
        await error_notify(method_name, linea_producto_notify_error, f"Error interno: {e}")
        raise HTTPException(status_code=500, detail="Error interno en el cálculo de financiamiento")



async def obtener_estado(debtor_id:str,request: Request):
    """
    Recibe un JSON con creditid, installmentid y orderid.
    Realiza hasta 3 peticiones a la API externa con intervalos de 15 segundos.
    Termina si el status es diferente de 'pending' o si se agotan los intentos.
    """
    method_name = "obtener_estado"
    try:
        
        body = await request.json()
        creditid = body.get("creditid")
        installmentid = body.get("installmentid")
        orderid = body.get("orderid")
        debtor_id_notify_error = f"debtor_id_cliente =  {debtor_id} y creditid = {creditid}"
        logger.info(f"+++++ Parámetros recibidos en el body: creditid= ++++++++, \n {creditid} \n")
        logger.info(f"+++++ Parámetros recibidos en el body: installmentid= ++++++++, \n {installmentid} \n")
        logger.info(f"+++++ Parámetros recibidos en el body: orderid= ++++++++, \n {orderid} \n")
        

        if not creditid or not installmentid or not orderid:
            raise HTTPException(status_code=400, detail="Faltan parámetros obligatorios: creditid, installmentid, orderid")

        url = f"https://api.kuenta.co/v1/payable/{creditid}/installment/0/order/list/{orderid}"
        intentos = 3
        intervalo_segundos = 10
        intento = 0

        logger.info(f"Parámetros recibidos: creditid={creditid}, installmentid={installmentid}, orderid={orderid}")

        async with httpx.AsyncClient() as client:
            # Obtener token una sola vez

            access_token = await obtener_token(client)
            logger.info(f"Token obtenido: {access_token}")

            if not access_token:
                raise HTTPException(status_code=401, detail="No se pudo obtener el token de acceso")

            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": debtor_id,
                "Authorization": access_token
            }
            
            while intento < intentos:
                intento += 1
                try:
                    
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    status = data.get("status")
                    logger.info(f"Intento {intento}: status del pago = {status}")
                    
                    if status != "pending":
                        logger.info(f"Estado final obtenido: {status} en el intento {intento} \n")
                        logger.info(f"Respuesta completa: {data} \n")
                        return data
                    
                except Exception as e:
                    logger.error(f"Error en intento {intento}: {str(e)}")
                    await error_notify(method_name, debtor_id_notify_error, f"Error en intento: {intento} {str(e)}")
                    
                if intento < intentos:
                    await asyncio.sleep(intervalo_segundos)
        return {"mensaje": "No se obtuvo un estado diferente a 'pending' tras 3 intentos"}
    
    except Exception as e:
        logger.error(f"Error en el proceso: {str(e)}")
        await error_notify(method_name, debtor_id_notify_error, f"Error en el proceso: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en el proceso: {str(e)}")