"""SQL 静态校验（sqlglot AST）。

安全红线：LLM 生成的 SQL 必须过这里才能执行，无例外路径。
规则对应需求文档 10.1：只读、单语句、表白名单、禁 SELECT INTO、禁危险函数。
"""

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# 根节点只允许这些类型（SELECT / UNION / WITH 包裹的 SELECT）
_ALLOWED_ROOTS = (exp.Select, exp.SetOperation)

# 任何位置都不允许出现的节点
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Create,
    exp.Grant,
    exp.Command,
)

# 危险函数（可写文件、执行命令、拖慢数据库）
_FORBIDDEN_FUNCTIONS = {
    "LOAD_FILE", "INTO_OUTFILE", "SLEEP", "BENCHMARK", "PG_READ_FILE",
    "PG_READ_BINARY_FILE", "PG_LS_DIR", "XP_CMDSHELL", "UTL_FILE",
}

_DIALECTS = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "sqlserver": "tsql",
    "mssql": "tsql",
    "oracle": "oracle",
}


def _normalize_expr(expression: exp.Expression) -> str:
    """规范化表达式用于比较：去掉表名限定、多余空白并小写。"""
    text = expression.sql().strip().rstrip(";")
    text = re.sub(r"\b[a-zA-Z_][a-zA-Z0-9_]*\.", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def mysql_strict_group_by_violation(sql: str) -> str | None:
    """检测 MySQL only_full_group_by 下会执行失败的 SELECT（sqlglot 静态分析）。

    触发：查询有 GROUP BY，但某个非聚合的查询列引用了未被 GROUP BY 覆盖的列。
    例如 SELECT CONCAT(YEAR(d),...) 而 GROUP BY YEAR(d), WEEK(d) —— MySQL 仍会拒绝，
    虽然各子函数都在 GROUP BY 中。返回违规的查询表达式 SQL，否则返回 None。
    """
    try:
        statement = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return None
    # GROUP BY belongs to one SELECT scope. Pairing a GROUP BY inside a CTE
    # with the outer SELECT projections rejects valid queries.
    for select in statement.find_all(exp.Select):
        group = select.args.get("group")
        if group is None:
            continue
        group_set = {_normalize_expr(e) for e in group.expressions}
        for projection in select.expressions:
            # 聚合或含聚合的表达式本身可脱离 GROUP BY（如 ROUND(SUM(...))）。
            if list(projection.find_all(exp.AggFunc)):
                continue
            # 去掉 SELECT 列别名，取核心表达式本身比较（避免 alias 导致误报）。
            expr = projection.this if isinstance(projection, exp.Alias) else projection
            normalized = _normalize_expr(expr)
            if normalized in group_set:
                continue
            # 未引用任何列（常量/子查询等）也不属于只依赖 GROUP BY 列的情况。
            if not list(expr.find_all(exp.Column)):
                continue
            return normalized
    return None




@dataclass
class SqlCheckResult:
    ok: bool
    tables: list[str] = field(default_factory=list)
    reason: str = ""
    checks: list[str] = field(default_factory=list)


def validate_dialect_compatibility(sql: str, dialect: str) -> SqlCheckResult:
    """Reject common cross-dialect constructs before asking the database to EXPLAIN."""
    normalized_dialect = (dialect or "mysql").strip().lower()
    upper_sql = sql.upper()
    if normalized_dialect in {"mysql", "mariadb"}:
        unsupported = [
            (r"\bFULL\s+(?:OUTER\s+)?JOIN\b", "MySQL 不支持 FULL OUTER JOIN，请改用 LEFT JOIN/UNION 组合"),
            (r"\bILIKE\b", "MySQL 不支持 ILIKE，请使用 LIKE"),
            (r"\bDATE_TRUNC\s*\(", "MySQL 不支持 DATE_TRUNC，请使用 DATE_FORMAT 或日期函数"),
            (r"::\s*[A-Z][A-Z0-9_]*", "MySQL 不支持 PostgreSQL 的 :: 类型转换语法"),
        ]
        group_by_bad = mysql_strict_group_by_violation(sql)
        if group_by_bad:
            return SqlCheckResult(
                ok=False,
                reason=(
                    "SQL 违反 MySQL 严格分组（only_full_group_by）：查询列 "
                    f"「{group_by_bad}」未完整出现在 GROUP BY 中。为保证执行稳定，"
                    "请让 SELECT 中的派生表达式（如 CONCAT(YEAR(d),...) 或 YEARWEEK(d)）"
                    "在 GROUP BY 中整体出现，或改回仅使用被分组的列与聚合函数。"
                ),
            )
    elif normalized_dialect in {"postgres", "postgresql"}:
        unsupported = [
            (r"`[^`]+`", "PostgreSQL 不支持 MySQL 反引号标识符"),
            (r"\bDATE_FORMAT\s*\(", "PostgreSQL 不支持 DATE_FORMAT，请使用 TO_CHAR 或 DATE_TRUNC"),
            (r"\bIFNULL\s*\(", "PostgreSQL 不支持 IFNULL，请使用 COALESCE"),
        ]
    elif normalized_dialect in {"sqlserver", "mssql", "tsql"}:
        unsupported = [
            (r"`[^`]+`", "SQL Server 不支持 MySQL 反引号标识符"),
            (r"\bLIMIT\s+\d+", "SQL Server 不支持 LIMIT，请使用 TOP 或 OFFSET/FETCH"),
            (r"\bILIKE\b", "SQL Server 不支持 ILIKE，请使用 LIKE"),
        ]
    elif normalized_dialect == "oracle":
        unsupported = [
            (r"`[^`]+`", "Oracle 不支持 MySQL 反引号标识符"),
            (r"\bLIMIT\s+\d+", "Oracle 不支持 LIMIT，请使用 FETCH FIRST 或 ROWNUM"),
            (r"\bILIKE\b", "Oracle 不支持 ILIKE，请使用 LIKE"),
        ]
    else:
        return SqlCheckResult(ok=False, reason=f"未注册的数据库方言：{dialect or 'unknown'}")

    for pattern, reason in unsupported:
        if re.search(pattern, upper_sql, flags=re.IGNORECASE):
            return SqlCheckResult(ok=False, reason=reason)
    label = {
        "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
        "mysql": "MySQL", "mariadb": "MariaDB",
        "sqlserver": "SQL Server", "mssql": "SQL Server", "tsql": "SQL Server",
        "oracle": "Oracle",
    }.get(normalized_dialect, normalized_dialect)
    return SqlCheckResult(ok=True, checks=[f"数据库方言：{label}"])


def validate_sql(sql: str, allowed_tables: set[str], dialect: str = "mysql") -> SqlCheckResult:
    """校验 SQL 是否允许在业务库执行。allowed_tables 来自已登记元数据。"""
    sql = sql.strip().rstrip(";")
    if not sql:
        return SqlCheckResult(ok=False, reason="SQL 为空")

    try:
        statements = sqlglot.parse(sql, read=_DIALECTS.get(dialect.lower(), dialect.lower()))
    except sqlglot.errors.ParseError as exc:
        return SqlCheckResult(ok=False, reason=f"SQL 语法错误：{exc}")

    statements = [stmt for stmt in statements if stmt is not None]
    if len(statements) != 1:
        return SqlCheckResult(ok=False, reason="只允许单条 SQL 语句")

    root = statements[0]
    if not isinstance(root, _ALLOWED_ROOTS):
        return SqlCheckResult(ok=False, reason=f"只允许只读查询（SELECT），检测到 {root.key.upper()}")

    checks = ["只读通过"]

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return SqlCheckResult(ok=False, reason=f"检测到禁止的操作：{type(node).__name__}")

    if root.args.get("into") is not None:
        return SqlCheckResult(ok=False, reason="禁止 SELECT INTO 写文件/写表")
    if re.search(r"\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b", sql, flags=re.IGNORECASE):
        return SqlCheckResult(ok=False, reason="禁止带锁查询")

    for func in root.find_all(exp.Func):
        # 已知函数 sql_name() 直接可用；未识别函数被解析为 Anonymous，名字在 .name
        func_name = func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
        if func_name in _FORBIDDEN_FUNCTIONS:
            return SqlCheckResult(ok=False, reason=f"检测到危险函数：{func_name}")

    cte_names = {cte.alias.lower() for cte in root.find_all(exp.CTE)}
    table_nodes = [table for table in root.find_all(exp.Table) if table.name.lower() not in cte_names]
    tables = sorted(
        {
            f"{table.db}.{table.name}" if table.db else table.name
            for table in table_nodes
        }
    )
    allowed = {name.lower() for name in allowed_tables}
    unknown = [name for name in tables if name.lower() not in allowed]
    if unknown:
        return SqlCheckResult(
            ok=False,
            tables=tables,
            reason=f"使用了未登记的数据表：{', '.join(unknown)}",
        )
    checks.append("表白名单通过")

    return SqlCheckResult(ok=True, tables=tables, checks=checks)
