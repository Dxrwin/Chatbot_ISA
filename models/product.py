from pydantic import BaseModel

class ProductLine(BaseModel):
    id: str
    parent_id: str
    name: str