"""
Reads output files produced by CrewAI agents.
"""
import json
from pathlib import Path
from typing import Any


def get_output_dir() -> Path:
    import os
    return Path(os.getenv("OUTPUT_DIR", "output"))


def read_json(file_path: str | Path) -> Any:
    p = Path(file_path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def read_text(file_path: str | Path) -> str | None:
    p = Path(file_path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def read_schema() -> list[dict]:
    """
    Returns a normalized list of table dicts with consistent 'table_name' and 'columns' keys,
    regardless of how the CrewAI tool originally structured the JSON.
    """
    from backend.api.services import session_state
    state = session_state.get()
    data = read_json(state.get("schema_output_file", "output/schema_metadata.json"))
    if data is None:
        return []

    # Extract the tables list from the raw schema object
    if isinstance(data, dict):
        tables = data.get("tables", [])
    elif isinstance(data, list):
        tables = data
    else:
        return []

    normalized = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        # Normalize the table name field
        table_name = t.get("table_name") or t.get("name") or t.get("table") or ""
        # Normalize columns
        raw_cols = t.get("columns") or t.get("fields") or []
        columns = []
        for c in raw_cols:
            if isinstance(c, dict):
                columns.append({
                    "name": c.get("name") or c.get("column_name") or "",
                    "type": str(c.get("type") or c.get("data_type") or ""),
                    "nullable": c.get("nullable"),
                    "primary_key": c.get("primary_key") or (c.get("name") in (t.get("primary_key") or [])),
                    "foreign_key": c.get("foreign_key"),
                })
        normalized.append({
            "table_name": table_name,
            "columns": columns,
            "row_count": t.get("row_count"),
        })
    return normalized


def read_metrics() -> list[dict]:
    output_dir = get_output_dir()
    data = read_json(output_dir / "metrics.json")
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def read_charts() -> list[dict]:
    output_dir = get_output_dir()
    charts_dir = output_dir / "charts"
    if not charts_dir.exists():
        return []
    charts = []
    for f in sorted(charts_dir.glob("*.json")):
        data = read_json(f)
        if data is not None:
            if isinstance(data, list):
                charts.extend(data)
            else:
                charts.append(data)
    return charts


import re

def read_final_answer() -> tuple[str, list[str]]:
    output_dir = get_output_dir()
    text = read_text(output_dir / "final_response.md")
    if not text:
        return "", []
    
    # regex to find [[SUGGESTIONS: q1 | q2 | q3 ]]
    pattern = r"\[\[SUGGESTIONS:\s*(.*?)\s*\]\]"
    match = re.search(pattern, text, re.DOTALL)
    
    suggestions = []
    clean_text = text
    
    if match:
        raw_suggestions = match.group(1)
        # Split by | and clean whitespace
        suggestions = [s.strip() for s in raw_suggestions.split("|") if s.strip()]
        # Remove the suggestion block from the main text
        clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
        
    return clean_text, suggestions


def list_reports() -> list[dict]:
    output_dir = get_output_dir()
    reports = []
    for f in output_dir.glob("*.md"):
        reports.append(
            {
                "id": f.stem,
                "name": f.stem.replace("_", " ").title(),
                "path": str(f),
                "size": f.stat().st_size,
            }
        )
    return sorted(reports, key=lambda r: r["name"])


def read_suggestions_json() -> list[str]:
    output_dir = get_output_dir()
    data = read_json(output_dir / "suggestions.json")
    if data is None:
        return []
    if isinstance(data, list):
        return [str(s) for s in data]
    return []


def read_report(report_id: str) -> str | None:
    output_dir = get_output_dir()
    f = output_dir / f"{report_id}.md"
    return read_text(f)
