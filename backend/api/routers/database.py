import os
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.api.models.database import ConnectRequest, StatusResponse
from backend.api.services import session_state

router = APIRouter()

CONFIG_FILE = Path(os.getenv("OUTPUT_DIR", "output")) / ".db_studio_config.json"





@router.post("/database/connect")
async def connect_database(req: ConnectRequest):
    from game_studio.tools import ValidateDatabaseConnectionTool

    tool = ValidateDatabaseConnectionTool()
    try:
        result = tool._run(
            connection_uri=req.connection_uri,
            database_type=req.database_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    success = "success" in str(result).lower() or "connected" in str(result).lower()

    if success:
        # Check if the URI changed — if so, clear stale schema/data files
        prev_state = session_state.get()
        uri_changed = prev_state.get("connection_uri", "") != req.connection_uri
        if uri_changed:
            for stale_file in [
                Path(req.schema_output_file),
                Path(req.data_output_file),
                Path("output/schema_metadata.json"),
                Path("output/table_data_dump.json"),
                Path("output/metrics.json"),
                Path("output/data_analysis.md"),
                Path("output/final_response.md"),
                Path("output/visual_report.md"),
            ]:
                try:
                    if stale_file.exists():
                        stale_file.unlink()
                except Exception:
                    pass
            # Clear charts folder
            charts_dir = Path("output/charts")
            if charts_dir.exists():
                for chart_file in charts_dir.glob("*.json"):
                    try:
                        chart_file.unlink()
                    except Exception:
                        pass

        session_state.set_connection(
            connection_uri=req.connection_uri,
            database_type=req.database_type,
            schema_output_file=req.schema_output_file,
            data_output_file=req.data_output_file,
            max_rows_per_table=req.max_rows_per_table,
            report_format=req.report_format,
            connected=True,
        )
        # Persist to config file
        try:
            CONFIG_FILE.write_text(
                json.dumps(
                    {
                        "connection_uri": req.connection_uri,
                        "database_type": req.database_type,
                        "schema_output_file": req.schema_output_file,
                        "data_output_file": req.data_output_file,
                        "max_rows_per_table": req.max_rows_per_table,
                        "report_format": req.report_format,
                    },
                    indent=2,
                )
            )
        except Exception:
            pass

        # No longer triggering legacy extraction here

    state = session_state.get()
    return StatusResponse(
        connected=success,
        connection_uri=state.get("connection_uri"),
        database_type=state.get("database_type"),
        schema_output_file=state.get("schema_output_file", "output/schema_metadata.json"),
        data_output_file=state.get("data_output_file", "output/table_data_dump.json"),
        max_rows_per_table=state.get("max_rows_per_table", 1000),
        report_format=state.get("report_format", "report"),
        message=str(result),
        is_extracting=state.get("is_extracting", False),
    )


@router.get("/database/status")
async def database_status() -> StatusResponse:
    state = session_state.get()
    return StatusResponse(
        connected=state.get("connected", False),
        connection_uri=state.get("connection_uri"),
        database_type=state.get("database_type"),
        schema_output_file=state.get("schema_output_file", "output/schema_metadata.json"),
        data_output_file=state.get("data_output_file", "output/table_data_dump.json"),
        max_rows_per_table=state.get("max_rows_per_table", 1000),
        report_format=state.get("report_format", "report"),
        message="",
        is_extracting=state.get("is_extracting", False),
    )
