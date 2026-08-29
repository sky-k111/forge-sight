"""Built-in database connector registration."""

from app.gateway.connectors.enterprise import OracleAdapter, SqlServerAdapter
from app.gateway.connectors.mysql import MariaDbAdapter, MySqlAdapter
from app.gateway.connectors.postgresql import PostgreSqlAdapter
from app.gateway.connectors.registry import (
    get_adapter,
    register_adapter,
    supported_types,
    type_descriptors,
)

for _adapter in (
    MySqlAdapter(),
    PostgreSqlAdapter(),
    MariaDbAdapter(),
    SqlServerAdapter(),
    OracleAdapter(),
):
    register_adapter(_adapter)

__all__ = ["get_adapter", "supported_types", "type_descriptors"]
