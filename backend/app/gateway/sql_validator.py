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
_FORBIDDEN_FUNCTIONS = {"LOAD_FILE", "INTO_OUTFILE", "SLEEP", "BENCHMARK"}


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
    if normalized_dialect == "mysql":
        unsupported = [
            (r"\bFULL\s+(?:OUTER\s+)?JOIN\b", "MySQL 不支持 FULL OUTER JOIN，请改用 LEFT JOIN/UNION 组合"),
            (r"\bILIKE\b", "MySQL 不支持 ILIKE，请使用 LIKE"),
            (r"\bDATE_TRUNC\s*\(", "MySQL 不支持 DATE_TRUNC，请使用 DATE_FORMAT 或日期函数"),
            (r"::\s*[A-Z][A-Z0-9_]*", "MySQL 不支持 PostgreSQL 的 :: 类型转换语法"),
        ]
    elif normalized_dialect in {"postgres", "postgresql"}:
        unsupported = [
            (r"`[^`]+`", "PostgreSQL 不支持 MySQL 反引号标识符"),
            (r"\bDATE_FORMAT\s*\(", "PostgreSQL 不支持 DATE_FORMAT，请使用 TO_CHAR 或 DATE_TRUNC"),
            (r"\bIFNULL\s*\(", "PostgreSQL 不支持 IFNULL，请使用 COALESCE"),
        ]
    else:
        return SqlCheckResult(ok=True, checks=[f"数据库方言：{dialect or 'unknown'}"])

    for pattern, reason in unsupported:
        if re.search(pattern, upper_sql, flags=re.IGNORECASE):
            return SqlCheckResult(ok=False, reason=reason)
    label = "PostgreSQL" if normalized_dialect in {"postgres", "postgresql"} else "MySQL"
    return SqlCheckResult(ok=True, checks=[f"数据库方言：{label}"])


def validate_sql(sql: str, allowed_tables: set[str]) -> SqlCheckResult:
    """校验 SQL 是否允许在业务库执行。allowed_tables 来自已登记元数据。"""
    sql = sql.strip().rstrip(";")
    if not sql:
        return SqlCheckResult(ok=False, reason="SQL 为空")

    try:
        statements = sqlglot.parse(sql, read="mysql")
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

    for func in root.find_all(exp.Func):
        # 已知函数 sql_name() 直接可用；未识别函数被解析为 Anonymous，名字在 .name
        func_name = func.name.upper() if isinstance(func, exp.Anonymous) else func.sql_name().upper()
        if func_name in _FORBIDDEN_FUNCTIONS:
            return SqlCheckResult(ok=False, reason=f"检测到危险函数：{func_name}")

    cte_names = {cte.alias for cte in root.find_all(exp.CTE)}
    tables = sorted({table.name for table in root.find_all(exp.Table)} - cte_names)
    unknown = [name for name in tables if name not in allowed_tables]
    if unknown:
        return SqlCheckResult(
            ok=False,
            tables=tables,
            reason=f"使用了未登记的数据表：{', '.join(unknown)}",
        )
    checks.append("表白名单通过")

    return SqlCheckResult(ok=True, tables=tables, checks=checks)
