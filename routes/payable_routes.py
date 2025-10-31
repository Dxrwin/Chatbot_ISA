from fastapi import APIRouter, HTTPException
from services.payable_service import create_payable
from services.financing_service import calcular_financiamiento
from fastapi import Request

router = APIRouter()

@router.post("/payable/{client_id}")
async def payable(client_id: str, payload: dict):
    try:
        return await create_payable(client_id, payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calcular_financiamiento")
async def calcular_financiamiento_endpoint(payload: dict):
    try:
        return await calcular_financiamiento(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

