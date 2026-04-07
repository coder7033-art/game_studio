from fastapi import APIRouter
from backend.api.services.output_reader import read_metrics

router = APIRouter()


@router.get("/metrics")
async def get_metrics():
    return read_metrics()
