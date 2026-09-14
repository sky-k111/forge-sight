"""sqlglot 校验器单元测试，不依赖数据库。"""

from app.gateway.sql_validator import validate_dialect_compatibility, validate_sql

TABLES = {"mes_process_output", "eqp_downtime_record", "dim_process"}


def test_plain_select_ok():
    result = validate_sql("SELECT line_id, SUM(good_qty) FROM mes_process_output GROUP BY line_id", TABLES)
    assert result.ok
    assert result.tables == ["mes_process_output"]


def test_select_with_join_and_cte_ok():
    sql = """
    WITH x AS (SELECT process_id, good_qty, input_qty FROM mes_process_output)
    SELECT p.process_name, SUM(x.good_qty) / SUM(x.input_qty)
    FROM x JOIN dim_process p ON x.process_id = p.process_id
    GROUP BY p.process_name
    """
    assert validate_sql(sql, TABLES).ok


def test_delete_blocked():
    result = validate_sql("DELETE FROM mes_process_output", TABLES)
    assert not result.ok
    assert "SELECT" in result.reason


def test_multi_statement_blocked():
    assert not validate_sql("SELECT 1; SELECT 2", TABLES).ok


def test_select_into_blocked():
    assert not validate_sql("SELECT * INTO OUTFILE '/tmp/x' FROM mes_process_output", TABLES).ok


def test_dangerous_function_blocked():
    assert not validate_sql("SELECT SLEEP(10) FROM mes_process_output", TABLES).ok


def test_unknown_table_blocked():
    result = validate_sql("SELECT * FROM user_passwords", TABLES)
    assert not result.ok
    assert "user_passwords" in result.reason


def test_syntax_error_reported():
    assert not validate_sql("SELCT !!! FROM", TABLES).ok


def test_mysql_full_outer_join_is_rejected_before_database_explain():
    result = validate_dialect_compatibility(
        "SELECT * FROM a FULL OUTER JOIN b ON a.id = b.id",
        "mysql",
    )
    assert not result.ok
    assert "MySQL" in result.reason
    assert "FULL OUTER JOIN" in result.reason


def test_postgresql_allows_full_outer_join():
    result = validate_dialect_compatibility(
        "SELECT * FROM a FULL OUTER JOIN b ON a.id = b.id",
        "postgresql",
    )
    assert result.ok


def test_schema_qualified_table_is_checked_as_physical_identity():
    result = validate_sql(
        'SELECT "order_id" FROM "sales"."orders"',
        {"sales.orders"},
        "postgres",
    )
    assert result.ok
    assert result.tables == ["sales.orders"]


def test_same_bare_table_in_wrong_schema_is_rejected():
    result = validate_sql(
        'SELECT * FROM "archive"."orders"',
        {"sales.orders"},
        "postgres",
    )
    assert not result.ok
    assert "archive.orders" in result.reason


def test_sqlserver_limit_is_rejected_before_execution():
    result = validate_dialect_compatibility("SELECT * FROM orders LIMIT 10", "sqlserver")
    assert not result.ok
    assert "TOP" in result.reason


def test_locked_select_is_rejected():
    result = validate_sql("SELECT * FROM mes_process_output FOR UPDATE", TABLES)
    assert not result.ok
    assert "锁" in result.reason


def test_mysql_strict_group_by_checks_each_select_scope_independently():
    sql = """
    WITH latest AS (
      SELECT product_id, MAX(snapshot_date) AS snapshot_date
      FROM inventory
      GROUP BY product_id
    )
    SELECT p.product_code, p.product_name, l.snapshot_date
    FROM latest l JOIN product p ON p.product_id = l.product_id
    """
    result = validate_dialect_compatibility(sql, "mysql")
    assert result.ok


def test_mysql_strict_group_by_still_rejects_partial_projection():
    sql = "SELECT product_id, product_name, SUM(qty) FROM inventory GROUP BY product_id"
    result = validate_dialect_compatibility(sql, "mysql")
    assert not result.ok
    assert "only_full_group_by" in result.reason


def test_mysql_unsigned_arithmetic_error_is_classified_as_repairable_query():
    from sqlalchemy.exc import OperationalError
    from app.gateway.executor import _execution_error_category

    error = OperationalError("SELECT available_qty - safety_stock_qty", {}, Exception(1690, "unsigned out of range"))
    assert _execution_error_category(error) == "query_invalid"
