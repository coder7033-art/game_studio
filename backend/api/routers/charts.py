from fastapi import APIRouter
from backend.api.services.output_reader import read_charts

router = APIRouter()


@router.get("/charts")
async def get_charts():
    return read_charts()
