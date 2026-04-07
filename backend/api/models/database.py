from pydantic import BaseModel
from typing import Optional


class ConnectRequest(BaseModel):
    connection_uri: str
    database_type: str = "sqlite"
    schema_output_file: str = "output/schema_metadata.json"
    data_output_file: str = "output/table_data_dump.json"
    max_rows_per_table: int = 1000
    report_format: str = "report"


class StatusResponse(BaseModel):
    connected: bool
    connection_uri: Optional[str] = None
    database_type: Optional[str] = None
    schema_output_file: str = "output/schema_metadata.json"
    data_output_file: str = "output/table_data_dump.json"
    max_rows_per_table: int = 1000
    report_format: str = "report"
    message: str = ""
    is_extracting: bool = False
