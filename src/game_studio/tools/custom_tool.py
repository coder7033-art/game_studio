from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Type, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text


from backend.api.services.clickhouse import get_clickhouse_client


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value


def _normalize_connection_uri(connection_uri: str) -> str:
    uri = connection_uri.strip()
    if uri.startswith("postgresql://") and "://" in uri and "+psycopg" not in uri:
        return uri.replace("postgresql://", "postgresql+psycopg://", 1)
    if uri.startswith("mysql://") and "+pymysql" not in uri:
        return uri.replace("mysql://", "mysql+pymysql://", 1)
    return uri


def _build_engine(connection_uri: str):
    return create_engine(_normalize_connection_uri(connection_uri), pool_pre_ping=True, future=True)


def _ensure_parent(path_value: str) -> Path:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class ValidateDatabaseConnectionInput(BaseModel):
    """Input schema for ValidateDatabaseConnectionTool."""

    database_type: str = Field(
        ...,
        description="Declared database type (for example: sqlite, postgresql, mysql, mssql).",
    )
    connection_uri: str = Field(
        ...,
        description="SQLAlchemy-compatible connection URI.",
    )


class ValidateDatabaseConnectionTool(BaseTool):
    name: str = "validate_database_connection"
    description: str = (
        "Validate database connectivity and return connection status, detected dialect, "
        "and a preview of available tables."
    )
    args_schema: Type[BaseModel] = ValidateDatabaseConnectionInput

    def _run(self, database_type: str, connection_uri: str) -> str:
        try:
            engine = _build_engine(connection_uri)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            response = {
                "status": "success",
                "declared_database_type": database_type,
                "detected_dialect": engine.dialect.name,
                "tables_count": len(table_names),
                "tables_preview": table_names[:25],
            }
            engine.dispose()
            return json.dumps(response, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "status": "error",
                    "declared_database_type": database_type,
                    "connection_uri": connection_uri,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )


class GetClickHouseSchemaInput(BaseModel):
    """Input schema for GetClickHouseSchemaTool."""
    table_names: Optional[List[str]] = Field(
        None, 
        description="Optional list of specific table names to get schema for. If omitted, returns schema for all tables."
    )


class GetClickHouseSchemaTool(BaseTool):
    name: str = "get_clickhouse_schema"
    description: str = (
        "Retrieve the schema metadata (tables and columns) from ClickHouse Cloud. "
        "Allows targeting specific tables to reduce data volume and improve speed."
    )
    args_schema: Type[BaseModel] = GetClickHouseSchemaInput

    def _run(self, table_names: Optional[List[str]] = None) -> str:
        try:
            client = get_clickhouse_client()
            
            # Filter by table names if provided
            table_filter = ""
            if table_names:
                formatted_names = ", ".join([f"'{name}'" for name in table_names])
                table_filter = f"AND table IN ({formatted_names})"

            query = f"""
                SELECT 
                    table, 
                    name, 
                    type 
                FROM system.columns 
                WHERE database = currentDatabase() 
                  AND table NOT LIKE 'system%'
                  {table_filter}
                ORDER BY table, position
            """
            result = client.query(query)
            
            schema = {}
            for row in result.result_rows:
                table_name, col_name, col_type = row
                if table_name not in schema:
                    schema[table_name] = []
                schema[table_name].append({
                    "column": col_name,
                    "type": col_type
                })
            
            return json.dumps({
                "status": "success",
                "tables_found": len(schema),
                "schema": schema
            }, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


class GetClickHouseTableNamesInput(BaseModel):
    """Input schema for GetClickHouseTableNamesTool."""
    dummy: str = Field("", description="Dummy arg.")


class GetClickHouseTableNamesTool(BaseTool):
    name: str = "get_clickhouse_table_names"
    description: str = (
        "Get a simple list of all available table names in the ClickHouse Data Warehouse. "
        "Use this first to identify relevant tables before requesting detailed schemas."
    )
    args_schema: Type[BaseModel] = GetClickHouseTableNamesInput

    def _run(self, dummy: str = "") -> str:
        try:
            client = get_clickhouse_client()
            query = "SELECT name FROM system.tables WHERE database = currentDatabase() AND name NOT LIKE 'system%'"
            result = client.query(query)
            tables = [row[0] for row in result.result_rows]
            return json.dumps({"status": "success", "tables": tables}, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)



class ChartSeriesItem(BaseModel):
    name: str = Field(..., description="Name of the series (e.g. 'Revenue' or 'Film Count')")
    values: List[float] = Field(..., description="Numeric values for this series, sharing the same x-axis labels.")
    type: str = Field(default="bar", description="Chart type for this specific series: 'bar', 'line', or 'area'")

class SaveChartDataInput(BaseModel):
    """Input schema for SaveChartDataTool."""

    chart_id: str = Field(..., description="Unique short identifier for this chart, e.g. 'revenue_by_category' or 'top_customers'. Used as filename.")
    labels: List[str] = Field(..., description="List of category/group labels.")
    series: List[ChartSeriesItem] = Field(default=[], description="List of series items. Each must have 'name', 'values' (numeric list matching labels), and 'type' ('bar' or 'line'). Put multiple items here for combo charts.")
    chart_type: str = Field(..., description="Default chart type: 'bar', 'pie', 'line', 'combo', 'area', or 'gauge'.")
    title: str = Field(..., description="Chart title.")
    xlabel: str = Field(..., description="X-axis label.")
    ylabel: str = Field(..., description="Y-axis label.")


class SaveChartDataTool(BaseTool):
    name: str = "save_chart_data"
    description: str = (
        "Save chart data as a JSON file so the UI renders an interactive Plotly chart. "
        "Call this once per chart — you can call it multiple times to produce multiple charts. "
        "Each call needs a unique chart_id. Never write matplotlib or plotly code yourself."
    )
    args_schema: Type[BaseModel] = SaveChartDataInput

    def _run(
        self,
        chart_id: str,
        labels: List[str],
        chart_type: str,
        title: str,
        xlabel: str,
        ylabel: str,
        series: List[Any] = None,
    ) -> str:
        try:
            # Convert ChartSeriesItem objects if passed as Pydantic models
            cleaned_series = []
            for s in (series or []):
                if hasattr(s, "model_dump"):
                    cleaned_series.append(s.model_dump())
                elif hasattr(s, "dict"):
                    cleaned_series.append(s.dict())
                else:
                    cleaned_series.append(dict(s) if hasattr(s, "keys") else s)

            payload = {
                "chart_type": chart_type,
                "title": title,
                "xlabel": xlabel,
                "ylabel": ylabel,
                "labels": labels,
                "values": [],
                "series": cleaned_series,
            }
            charts_dir = Path("output/charts")
            charts_dir.mkdir(parents=True, exist_ok=True)
            dest = charts_dir / f"{chart_id}.json"
            dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return json.dumps(
                {"status": "success", "chart_file": str(dest), "data_points": len(labels)},
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})


class SaveMetricsInput(BaseModel):
    """Input schema for SaveMetricsTool — single JSON string to avoid provider schema issues."""

    metrics: str = Field(
        ...,
        description=(
            "A JSON array encoded as a STRING. Each element is a metric object. "
            "Required keys per object: \"label\" (string), \"value\" (string). "
            "Optional keys: \"color\" (one of: green, red, orange, blue), "
            "\"sub_value\" (string), \"target\" (string), "
            "\"delta\" (string, e.g. \"+5.1%\" or \"-3.2%\"), \"delta_up\" (boolean), "
            "\"sparkline\" (array of 6-10 numbers representing a trend). "
            "Example value: "
            "'[{\"label\": \"Total Revenue\", \"value\": \"$4,200\", \"color\": \"green\", "
            "\"delta\": \"+5.1%\", \"delta_up\": true}]'"
        ),
    )


class SaveMetricsTool(BaseTool):
    name: str = "save_metrics"
    description: str = (
        "Save KPI metrics as cards in the UI. "
        "Pass a JSON array STRING in the 'metrics' argument — "
        "each object needs 'label' and 'value'. "
        "Optional per object: 'color' (green/red/orange/blue), 'sub_value', 'target', "
        "'delta' (e.g. '+5.1%'), 'delta_up' (true/false), 'sparkline' (list of numbers). "
        "Only call this when the answer has clear numeric KPIs worth highlighting."
    )
    args_schema: Type[BaseModel] = SaveMetricsInput

    def _run(self, metrics: str) -> str:
        try:
            parsed = json.loads(metrics) if isinstance(metrics, str) else metrics
            if not isinstance(parsed, list):
                return json.dumps({"error": "metrics must be a JSON array"})
            
            valid = []
            for m in parsed:
                if isinstance(m, dict) and m.get("label") and m.get("value"):
                    # Basic hallucination check: value should usually contain a number
                    val_str = str(m["value"])
                    if not any(char.isdigit() for char in val_str):
                        # Log but don't necessarily fail, or we can choose to be strict
                        # For now, let's keep it but add a warning if it's purely non-numeric
                        pass
                    valid.append(m)
            
            if not valid:
                return json.dumps({"error": "No valid metrics found (each needs 'label' and 'value')"})
            
            dest = Path("output/metrics.json")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(valid, ensure_ascii=False, indent=2), encoding="utf-8")
            return json.dumps({"status": "success", "metrics_count": len(valid)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})


class ExecuteSQLQueryInput(BaseModel):
    """Input schema for ExecuteSQLQueryTool."""

    connection_uri: str = Field(..., description="SQLAlchemy-compatible connection URI.")
    sql_query: str = Field(
        ...,
        description=(
            "A read-only SELECT SQL query to execute. "
            "Do NOT use INSERT, UPDATE, DELETE, DROP, or any write operations."
        ),
    )
    max_rows: int = Field(
        default=500,
        description="Maximum number of rows to return (default 500).",
        ge=1,
        le=5000,
    )


class ExecuteSQLQueryTool(BaseTool):
    name: str = "execute_sql_query"
    description: str = (
        "Execute a read-only SELECT SQL query directly against the database and return "
        "the results as JSON. Use this when you need multi-table JOINs or aggregations "
        "that are too complex for compute_group_by or compute_join_group_by. "
        "Only SELECT statements are allowed."
    )
    args_schema: Type[BaseModel] = ExecuteSQLQueryInput

    def _run(self, connection_uri: str, sql_query: str, max_rows: int = 500) -> str:
        stripped = sql_query.strip().upper()
        if not stripped.startswith("SELECT"):
            return json.dumps({"error": "Only SELECT queries are allowed."})
        try:
            engine = _build_engine(connection_uri)
            with engine.connect() as conn:
                result = conn.execute(text(sql_query))
                columns = list(result.keys())
                rows = result.fetchmany(max_rows)
                serialized = [_json_safe(dict(zip(columns, row))) for row in rows]
            engine.dispose()
            return json.dumps(
                {
                    "status": "success",
                    "columns": columns,
                    "rows_returned": len(serialized),
                    "results": serialized,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})



class SyncToClickHouseInput(BaseModel):
    """Input schema for SyncToClickHouseTool."""
    connection_uri: str = Field(..., description="SQLAlchemy-compatible connection URI.")
    session_id: str = Field("dev", description="A unique identifier for the user session to namespace ClickHouse tables (default 'dev').")
    max_rows_per_table: int = Field(1000, description="Max rows to sync per table.", ge=0)


class SyncToClickHouseTool(BaseTool):
    name: str = "sync_to_clickhouse"
    description: str = (
        "Extract tables from the user's database and sync them to ClickHouse Cloud. "
        "Returns the synced ClickHouse table names which should be used for analytical queries."
    )
    args_schema: Type[BaseModel] = SyncToClickHouseInput

    def _run(self, connection_uri: str, session_id: str = "dev", max_rows_per_table: int = 1000) -> str:
        try:
            client = get_clickhouse_client()

            engine = _build_engine(connection_uri)
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            metadata = MetaData()
            
            synced_tables = []
            
            with engine.connect() as connection:
                for table_name in table_names:
                    ch_table_name = f"{session_id}_{table_name}"
                    
                    table_obj = Table(table_name, metadata, autoload_with=engine)
                    columns = inspector.get_columns(table_name)
                    
                    # Build CREATE TABLE
                    cols_def = []
                    ch_types_map = {}
                    for col in columns:
                        type_str = str(col["type"]).upper()
                        ch_type = "String"
                        if "INT" in type_str: ch_type = "Nullable(Int64)"
                        elif "REAL" in type_str or "FLOAT" in type_str or "NUMERIC" in type_str or "DECIMAL" in type_str: ch_type = "Nullable(Float64)"
                        elif "BOOL" in type_str: ch_type = "Nullable(UInt8)"
                        elif "DATETIME" in type_str or "TIMESTAMP" in type_str: ch_type = "Nullable(DateTime64(3))"
                        elif "DATE" in type_str: ch_type = "Nullable(Date)"
                        else: ch_type = "Nullable(String)"
                        cols_def.append(f"`{col['name']}` {ch_type}")
                        ch_types_map[col["name"]] = ch_type
                    
                    # Drop table to ensure it matches current schema
                    client.command(f"DROP TABLE IF EXISTS {ch_table_name}")
                    
                    create_stmt = f"CREATE TABLE IF NOT EXISTS {ch_table_name} ({', '.join(cols_def)}) ENGINE = MergeTree() ORDER BY tuple()"
                    client.command(create_stmt)
                    
                    # Fetch data
                    query = select(table_obj)
                    if max_rows_per_table > 0:
                        query = query.limit(max_rows_per_table)
                    
                    rows = connection.execute(query).mappings().all()
                    
                    if rows:
                        col_names = [col["name"] for col in columns]
                        data_matrix = []
                        for row in rows:
                            # Safely extract row data
                            row_data = []
                            for c in col_names:
                                val = row.get(c)
                                ch_type = ch_types_map[c]
                                if val is not None:
                                    if "String" in ch_type:
                                        val = str(val)
                                    elif "Int" in ch_type:
                                        val = int(val)
                                    elif "Float" in ch_type:
                                        val = float(val)
                                    elif "UInt8" in ch_type: # Boolean case
                                        val = 1 if val else 0
                                row_data.append(val)
                            data_matrix.append(row_data)
                        
                        client.insert(ch_table_name, data_matrix, column_names=col_names)
                    
                    synced_tables.append({
                        "original": table_name,
                        "clickhouse_table": ch_table_name,
                        "rows_synced": len(rows)
                    })
                    
            engine.dispose()
            return json.dumps({
                "status": "success",
                "message": "Data synced to ClickHouse Data Warehouse.",
                "session_namespace": session_id,
                "tables": synced_tables
            }, ensure_ascii=False, indent=2)
            
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


class ClickHouseQueryInput(BaseModel):
    """Input schema for ClickHouseQueryTool."""
    sql_query: str = Field(..., description="The highly-optimized SELECT query to run against ClickHouse Cloud.")


class ClickHouseQueryTool(BaseTool):
    name: str = "clickhouse_query_tool"
    description: str = "Run analytical SELECT queries directly against the ClickHouse Data Warehouse."
    args_schema: Type[BaseModel] = ClickHouseQueryInput

    def _run(self, sql_query: str) -> str:
        try:
            if not sql_query.strip().upper().startswith("SELECT"):
                return json.dumps({"error": "Only SELECT queries are allowed."})
                
            client = get_clickhouse_client()
            
            result = client.query(sql_query)
            
            rows = []
            for row in result.result_rows:
                rows.append(dict(zip(result.column_names, row)))
                
            return json.dumps({
                "status": "success",
                "rows_returned": len(rows),
                "data": _json_safe(rows)
            }, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)
