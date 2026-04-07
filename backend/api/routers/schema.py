from fastapi import APIRouter
from backend.api.services.output_reader import read_schema

router = APIRouter()


@router.get("/schema")
async def get_schema():
    return read_schema()  # returns [] if not yet extracted
