from fastapi import APIRouter, HTTPException
from backend.api.services.output_reader import list_reports, read_report

router = APIRouter()


@router.get("/reports")
async def get_reports():
    return list_reports()


@router.get("/reports/{report_id}")
async def get_report(report_id: str):
    content = read_report(report_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"id": report_id, "content": content}
