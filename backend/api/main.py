"""
FastAPI application — replaces the Streamlit ui.py.
All existing CrewAI agent logic (crew.py, tools, config) is untouched.
"""
from __future__ import annotations

import os
import warnings
import json
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from backend.api.services.llm_monitor import get_llm_logs, clear_llm_logs

from backend.api.routers import (
    chat,
    charts,
    database,
    metrics,
    reports,
    schema,
    suggestions,
)
from backend.api.services import session_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure output directories exist
    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    (output_dir / "charts").mkdir(parents=True, exist_ok=True)

    config_file = output_dir / ".db_studio_config.json"

    # 1. Restore from last saved config if it exists
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            session_state.set_connection(
                connection_uri=config.get("connection_uri"),
                database_type=config.get("database_type", "sqlite"),
                max_rows_per_table=config.get("max_rows_per_table", 1000),
                report_format=config.get("report_format", "report"),
                connected=True,
            )
            print(f"Restored connection to {config.get('connection_uri')}")
        except Exception as e:
            print(f"Error restoring config: {e}")

    # 2. Check for manual env var if no config was restored
    if not session_state.get().get("connected"):
        connection_uri = os.getenv("DB_CONNECTION_URI", "")
        if connection_uri:
            session_state.set_connection(
                connection_uri=connection_uri,
                database_type=os.getenv("DB_TYPE", "sqlite"),
                connected=True,
            )
            print("Auto-connected via environment variable.")

    yield


app = FastAPI(title="GameStudio API", version="1.0.0", lifespan=lifespan)

# CORS — allow the Next.js dev server and Docker internal network
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers under /api
app.include_router(chat.router, prefix="/api")
app.include_router(database.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(suggestions.router, prefix="/api")
app.include_router(reports.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/debug/logs")
async def get_debug_logs():
    return get_llm_logs()

@app.post("/api/debug/logs/clear")
async def clear_debug_logs():
    clear_llm_logs()
    return {"status": "cleared"}
