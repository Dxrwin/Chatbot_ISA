from fastapi import HTTPException, logger
from utils.auth import obtener_token
from utils.notify_error import error_notify
from pydantic import BaseModel
from typing import Dict, Any
from utils.config import ORG_ID, PAYABLE_URL
import asyncio
import httpx


class PayableRequest(BaseModel):
    creditLineID: str 
    principal: float 
    time: int 
    disbursementMethod: str 
    initialFee: float 
    paymentFrequency: int
    

# --- Sistema de Caché en Memoria ---
cuotas_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = 5

async def create_payable(client_id: str, payload: PayableRequest):
    """
    Endpoint para crear un nuevo payable:
    1. Recibe el ID del cliente como parámetro
    2. Transforma los campos principal y initialFee de str a int
    3. Extrae el token de autorización del payload
    4. Realiza la petición POST al endpoint de payable
    
    """
    method_name = "create_payable"
    try:

        async with httpx.AsyncClient() as client:
            
            logger.info(f"+++++ Parámetros recibidos: client_id= ++++++++, \n {client_id} \n")
            
            logger.info(f"#####--- Payload entrante ----#### \n {payload} \n")
            
            principal = payload.principal
            initial_fee = payload.initialFee
            
            token = await obtener_token(client)
            #token = "v2.public.eyJjbGllbnQiOiIwNDY4Mzc1Yi0wYzAyLTQxNDMtYmY2NS03Njc0NDk3MTA0NmYiLCJjdHgiOiJ1c2VyIiwiZXhwIjoiMjAyNS0xMC0yOVQxNjo0MjowOS0wNTowMCIsImp0aSI6IjRhODExMmJlLWRmOWEtNDBmNC1iYTU1LTU1Y2ZiYzFmZGUzNyIsInNlc3Npb24iOiI3MDhjZGYzNi1kZmI5LTQ4M2MtODRiMy1iYTlhZTNiZGM2MDIiLCJ0eXBlIjoiYXV0aCIsInVzZXIiOiJlODI5YTY3NC04MDE3LTRhYWEtYTJlNy00MjI1YWEyOGFjZTMiLCJ2ZXJpZmllZCI6InRydWUifdxXSkJr8ChbO7Hoea_4eCj0OEuNMOvLj6Xqt2gfX92iqdwJsjhAK-cznEbl8i0jon-F1nLf8WNtGzBCQa6ucQQ.bnVsbA"
            
            logger.info(f"Token de autorización extraído: {token} \n")
            
            new_payload = {
                "creditLineID": payload.creditLineID,
                #"creditLineID": "97c6a459-a86c-4e01-bfa1-7b3e21acddf3",
                "principal": principal,
                "time": payload.time,
                "disbursementMethod": payload.disbursementMethod,
                "initialFee": initial_fee,
                "paymentFrequency": payload.paymentFrequency
            }
            logger.info(f"Payload para saliente para el post a kuenta: \n {new_payload} \n")
            headers = {
                "Config-Organization-ID": ORG_ID,
                "Organization-ID": client_id,
                #"Config-Organization-ID":"c269cfcc-0c9c-43e3-bef0-9e95d42ca309",
                #"Organization-ID":"c8c90e3e-f2a6-4caa-8254-a1403b4416d3",
                "Authorization": token
            }
            logger.info(f"Iniciando petición POST a {PAYABLE_URL}")
            logger.info(f"Payload transformado para enviar a kuenta: {new_payload}")

            max_retries = 3
            for attempt in range(max_retries):
                
                try:
                    response = await client.post(
                        PAYABLE_URL,
                        #f"https://demo-api.kuenta.co/v1/payable",
                        json=new_payload,
                        headers=headers
                    )
                    status_code = response.status_code
                    logger.info(f"Intento {attempt+1}: status_code={status_code}")
                    if status_code == 201:
                        #logger.info("Petición completada exitosamente")
                        # Si hay contenido, retorna el JSON, si no retorna vacío
                            logger.info("Procesando respuesta de Kuenta")
                            response_data = response.json()
                            credit = response_data.get("data", {}).get("credit", {})
                            
                            # ID credito
                            response_credit_id = credit.get("ID")
                            logger.info(f"ID del crédito creado: {response_credit_id} \n")
                            #return response_data
                        
                    try:
                            # url en demo
                            #url = GET_PAYABLE_URL + response_credit_id
                            #print("url de la simulacion es: ", url)
                            
                            #url en produccion
                            url_prod = f"https://api.kuenta.co/v1/payable/{response_credit_id}"
                            # Realizar la consulta GET
                            response_get_simulacion = await client.get(url_prod, headers=headers)
                            status_code_simulacion = response_get_simulacion.status_code
                            
                            logger.info(f"Status code de la simulación: {status_code_simulacion}")
                            
                            
                            if status_code_simulacion == 200 or status_code_simulacion == 201:
                                simulacion_data = response_get_simulacion.json()
                                installments = simulacion_data.get("data", {}).get("credit", {}).get("installments", [])
                                cuota_inicial = simulacion_data.get("data", {}).get("credit", {}).get("initialFee")

                                if installments:
                                    # Tomar el primer installment
                                    first_installment = installments[0]
                
                                    # Extraer y redondear valores
                                    payment = round(float(first_installment.get("payment", 0)))
                                    capital = round(float(first_installment.get("capital", 0)))
                                    interest = round(float(first_installment.get("interest", 0)))
                                    costs = round(float(first_installment.get("costs", 0)))
                                    taxes = round(float(first_installment.get("taxes", 0)))
                                    
                                    # cuota inicial redondeada
                                    cuota_inicial_rounded = round(float(cuota_inicial))

                                    # Formatear valores para lectura humana
                                    formatted_values = {
                                        "payment_formatted": f"${payment:,}",
                                        "capital_formatted": f"${capital:,}",
                                        "interest_formatted": f"${interest:,}",
                                        "costs_formatted": f"${costs:,}",
                                        "taxes_formatted": f"${taxes:,}",
                                        "cuota_inicial_formatted": f"${cuota_inicial_rounded:,}"
                                    }

                                    # Agregar valores originales y formateados a la respuesta
                                    response_data.update({
                                        "ID del crédito creado": response_credit_id,
                                        "valores_originales": {
                                            "payment": payment,
                                            "capital": capital,
                                            "interest": interest,
                                            "costs": costs,
                                            "taxes": taxes
                                        },
                                        "valores_formateados": {
                                            "payment_formatted": formatted_values["payment_formatted"],
                                            "capital_formatted": formatted_values["capital_formatted"],
                                            "interest_formatted": formatted_values["interest_formatted"],
                                            "costs_formatted": formatted_values["costs_formatted"],
                                            "taxes_formatted": formatted_values["taxes_formatted"],
                                            "cuota_inicial_formatted": formatted_values["cuota_inicial_formatted"]
                                        }
                                    })

                                    logger.info("Valores extraídos y formateados exitosamente")
                                    logger.info(f"Valores formateados: {formatted_values}")
                
                                    return response_data
                                else:
                                    logger.error("No se encontraron installments en la respuesta")
                                    raise HTTPException(status_code=404, detail="No se encontraron cuotas en la simulación")
                
                            else:
                                logger.error(f"Error en la consulta de simulación: {status_code_simulacion}")
                                raise HTTPException(status_code=status_code_simulacion, 
                                                detail="Error al consultar la simulación")
                            
                    except httpx.HTTPStatusError as e:
                            await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {str(e)}")
                            logger.error(f"Intento {attempt+1}: Error en la respuesta de la API externa kuenta: {e.response.status_code}")
                    else:
                        logger.error(f"status error : {response.status_code}")
                        await error_notify(method_name, client_id, f"Error en la respuesta de la API externa kuenta: {response.status_code}")
                        
                except httpx.HTTPStatusError as e:
                        logger.error(f"Intento {attempt+1}: Error en API externa: {e.response.status_code}")
                        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # espera exponencial
            # Si no se logró en los reintentos
            await error_notify(method_name, client_id, f"Error de conexión tras: {max_retries} intentos o respuesta no válida")
            raise HTTPException(status_code=502, detail=f"Error de conexión tras {max_retries} intentos o respuesta no válida")
        
    except ValueError as e:
        logger.error(f"Error de conversión de datos: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conversión de datos: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail="Error en la conversión de datos. Asegúrate que principal y initialFee sean números válidos"
        )
        
    except httpx.RequestError as e:
        logger.error(f"Error de conexión: {str(e)}")
        await error_notify(method_name, client_id, f"Error de conexión: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Error de conexión: {str(e)}")
    
    except httpx.HTTPStatusError as e:
        logger.error(f"Error en API externa: {e.response}")
        await error_notify(method_name, client_id, f"Error en API externa: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Error en API externa: {e.response.text}")
    except Exception as e:
        logger.error(f"Error interno: {str(e)}")
        await error_notify(method_name, client_id, f"Error interno: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")