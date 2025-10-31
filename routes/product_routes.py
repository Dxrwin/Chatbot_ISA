from fastapi import APIRouter, HTTPException
from services.product_service import get_product_lines

router = APIRouter()

@router.get("/product-lines/{parent_id}")
async def webhook_product_lines(parent_id: str):
    try:
        product_lines = await get_product_lines(parent_id)
        return product_lines
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))