from fastapi import APIRouter, HTTPException,Request
from services.financing_service import calcular_financiamiento, obtener_estado
from pydantic import BaseModel

router = APIRouter()
# class ClienteRequest(BaseModel):
#     id_cliente: str

class CalcularFinanciamientoRequest(BaseModel):
    # Define the fields required for the calcular_financiamiento endpoint
    principal: float
    porcentaje_cuota: float
    plazo_escogido: int
    paymentFrequency: int
    linea_producto: str

@router.post("/calcular_financiamiento")
async def calcular_financiamiento(payload: dict):
    try:
        result = await calcular_financiamiento(payload)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/obtener-estado/{debtor_id}")
async def obtener_estado(debtor_id:str,request: Request):
    try:
        result = await obtener_estado(debtor_id, request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))