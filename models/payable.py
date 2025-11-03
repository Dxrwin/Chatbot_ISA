from pydantic import BaseModel

class PayableRequest(BaseModel):
    creditLineID: str 
    principal: float 
    time: int 
    disbursementMethod: str 
    initialFee: float 
    paymentFrequency: int 

# class DetalleCuotaRequest(BaseModel):
#     id_cliente: str
#     numero_cuota: int