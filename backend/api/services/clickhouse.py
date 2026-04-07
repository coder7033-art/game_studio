import os
import clickhouse_connect

def get_clickhouse_client():
    """
    Returns a configured ClickHouse client instance.
    Uses environment variables if available, otherwise falls back to the default credentials.
    """
    host = os.environ.get("CLICKHOUSE_HOST", "dps1mubzaj.eu-central-1.aws.clickhouse.cloud")
    user = os.environ.get("CLICKHOUSE_USER", "default")
    password = os.environ.get("CLICKHOUSE_PASSWORD", "ndbHC8MQ.YBUi")
    
    return clickhouse_connect.get_client(
        host=host,
        user=user,
        password=password,
        secure=True
    )
