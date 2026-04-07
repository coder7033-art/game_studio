"""
In-process, thread-safe config store replacing st.session_state.
For the single-user Docker use case a module-level singleton is sufficient.
"""
import threading
import os
from pathlib import Path

_lock = threading.Lock()

_state: dict = {}


def _defaults() -> dict:
    return {
        "connection_uri": os.getenv("DB_CONNECTION_URI", ""),
        "database_type": os.getenv("DB_TYPE", "sqlite"),
        "schema_output_file": os.getenv("SCHEMA_OUTPUT_FILE", "output/schema_metadata.json"),
        "data_output_file": os.getenv("DATA_OUTPUT_FILE", "output/table_data_dump.json"),
        "max_rows_per_table": int(os.getenv("MAX_ROWS_PER_TABLE", "1000")),
        "report_format": os.getenv("REPORT_FORMAT", "report"),
        "session_id": os.getenv("SESSION_ID", "dev"),
        "connected": False,
        "is_extracting": False,
    }


def get() -> dict:
    with _lock:
        if not _state:
            _state.update(_defaults())
        return dict(_state)


def update(data: dict) -> None:
    with _lock:
        if not _state:
            _state.update(_defaults())
        _state.update(data)


def set_connection(
    connection_uri: str,
    database_type: str = "sqlite",
    schema_output_file: str = "output/schema_metadata.json",
    data_output_file: str = "output/table_data_dump.json",
    max_rows_per_table: int = 1000,
    report_format: str = "report",
    session_id: str = "dev",
    connected: bool = True,
) -> None:
    with _lock:
        _state.update(
            {
                "connection_uri": connection_uri,
                "database_type": database_type,
                "schema_output_file": schema_output_file,
                "data_output_file": data_output_file,
                "max_rows_per_table": max_rows_per_table,
                "report_format": report_format,
                "session_id": session_id,
                "connected": connected,
            }
        )
