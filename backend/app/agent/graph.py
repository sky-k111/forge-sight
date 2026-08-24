"""阶段 5 第一版真实 LangGraph：写意图拦截、检索、SQL、校验、执行、解释与有限纠错。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.budgets import BudgetExceeded
from app.agent.intent import build_intent_plan, build_request_route
from app.agent.state import AnalysisState
from app.gateway.executor import ExecutionResult
from app.gateway.sql_validator import SqlCheckResult, validate_dialect_compatibility

WRITE_TERMS = (
    "删除", "修改", "更新", "写入", "插入", "新增记录", "建表", "删表", "清空",
    "delete ", "update ", "insert ", "drop ", "alter ", "truncate ", "create table",
)

RETRIEVAL_QUERY_MAX_LENGTH = 1000
RETRIEVAL_HISTORY_ITEMS = 2


@dataclass(frozen=True)
class GraphDependencies:
    retrieve_context: Callable[[str, int], dict[str, Any]]
    generate_text: Callable[[AnalysisState, str, str, str], str]
    validate_sql: Callable[[str, set[str]], SqlCheckResult]
    execute_sql: Callable[[str, int], ExecutionResult]
    explain_sql: Callable[[str, int], Any] | None = None
    retrieve_skills: Callable[[str, int], dict[str, Any]] | None = None
    find_reusable_models: Callable[[str, int, str], list[dict[str, Any]]] | None = None


# ---- 建模提案：确定性规则（无 LLM 调用），产出 ModelingProposalCard 需要的全部字段 ----

MODELING_TASK_TYPE_LABELS = {
    "classification": "二分类",
    "regression": "回归预测",
    "clustering": "聚类",
    "anomaly": "异常检测",
}
MODELING_METRIC_LABELS = {
    "classification": "AUC / F1",
    "regression": "R² / MAE",
    "clustering": "轮廓系数",
    "anomaly": "异常占比",
}
MODELING_RECOMMENDED_CANDIDATES = {
    "classification": ["logistic", "tree", "forest"],
    "regression": ["linear", "tree", "forest"],
    "clustering": ["kmeans"],
    "anomaly": ["isolation"],
}
_CLASSIFICATION_TARGET_HINTS = ("风险", "是否", "合格", "不良", "缺陷", "异常", "等级", "标记", "标签")
_REGRESSION_TARGET_HINTS = ("时长", "产量", "数量", "金额", "成本", "温度", "值", "费用", "天数")
_MODELING_SKIP_COLUMN_TOKENS = (
    "id", "日期", "时间", "date", "time", "created", "updated", "序号", "备注", "说明",
)
MODELING_SAMPLE_LIMIT = 10000


def _detect_modeling_task_type(question: str) -> str:
    """建模问题 → 任务类型：聚类/异常优先，其次分类判定，兜底回归预测。"""
    if any(term in question for term in ("聚类", "分群", "分组归类")):
        return "clustering"
    if any(term in question for term in ("异常", "离群")):
        return "anomaly"
    if any(term in question for term in ("识别", "判断", "是否", "风险", "合格", "不良", "预警", "分类")):
        return "classification"
    return "regression"


def _pick_modeling_target(question: str, fields: list[dict], task_type: str) -> tuple[str, str]:
    """按问题关键词匹配字段中文名/物理名；返回 (字段名, 中文名)，未命中返回 ('', '')。"""
    hints = _CLASSIFICATION_TARGET_HINTS if task_type == "classification" else _REGRESSION_TARGET_HINTS
    best_name = ""
    best_semantic = ""
    best_hits = 0
    for field in fields:
        name = str(field.get("name") or "")
        semantic = str(field.get("semanticName") or "")
        hits = sum(1 for hint in hints if hint in semantic or hint in name)
        if hits > best_hits:
            best_hits = hits
            best_name = name
            best_semantic = semantic
    return best_name, best_semantic


def _modeling_features(fields: list[dict], target_field: str) -> list[str]:
    """特征列：排除主键/时间/id 类列与目标列，保留建模可用列（上限 20）。"""
    features: list[str] = []
    for field in fields:
        name = str(field.get("name") or "")
        lowered = name.lower()
        if name == target_field or field.get("isPrimary"):
            continue
        if any(token in lowered for token in _MODELING_SKIP_COLUMN_TOKENS):
            continue
        features.append(name)
    return features[:20]


def _safe_identifier(value: str) -> str:
    candidate = str(value or "").strip()
    return candidate if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate) else ""


def _sql_literal(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _diagnose_entity(
    deps: GraphDependencies,
    source_id: int,
    diagnostic: dict[str, Any],
) -> dict[str, Any] | None:
    lookup = diagnostic.get("entityLookup") or {}
    table = _safe_identifier(lookup.get("table", ""))
    name_field = _safe_identifier(lookup.get("nameField", ""))
    code_field = _safe_identifier(lookup.get("codeField", ""))
    id_field = _safe_identifier(lookup.get("idField", ""))
    value = str(lookup.get("value") or "").strip()
    search_fields = [field for field in (name_field, code_field) if field]
    select_fields = list(dict.fromkeys(field for field in (id_field, code_field, name_field) if field))
    if not table or not value or not search_fields or not select_fields:
        return None

    literal = _sql_literal(value)
    exact = " OR ".join(f"{field} = {literal}" for field in search_fields)
    fuzzy = " OR ".join(f"{field} LIKE {_sql_literal(f'%{value}%')}" for field in search_fields)
    sql = (
        f"SELECT {', '.join(select_fields)}, "
        f"CASE WHEN {exact} THEN 'exact' ELSE 'similar' END AS match_type "
        f"FROM {table} WHERE {exact} OR {fuzzy} LIMIT 5"
    )
    validation = deps.validate_sql(sql, {table})
    if not validation.ok:
        return {
            "kind": lookup.get("kind"),
            "label": lookup.get("label"),
            "value": value,
            "checked": False,
            "reason": validation.reason,
        }
    execution = deps.execute_sql(sql, source_id)
    if not execution.ok:
        return {
            "kind": lookup.get("kind"),
            "label": lookup.get("label"),
            "value": value,
            "checked": False,
            "reason": execution.error,
        }
    rows = execution.rows or []
    match_index = execution.columns.index("match_type") if "match_type" in execution.columns else -1
    exact_match = any(
        (row.get("match_type") if isinstance(row, dict) else row[match_index] if match_index >= 0 else "") == "exact"
        for row in rows
    )
    candidates = []
    for row in rows:
        if isinstance(row, dict):
            candidates.append({key: row.get(key) for key in select_fields})
        else:
            candidates.append({
                field: row[index] if index < len(row) else None
                for index, field in enumerate(select_fields)
            })
    return {
        "kind": lookup.get("kind"),
        "label": lookup.get("label"),
        "value": value,
        "checked": True,
        "exists": exact_match,
        "candidates": candidates[:5],
        "query": sql,
    }


def _no_match_error(context: dict[str, Any], entity_check: dict[str, Any] | None) -> dict[str, Any]:
    diagnostic = context.get("diagnostics") or {}
    capability = diagnostic.get("requestedCapability") or {}
    entity_missing = bool(entity_check and entity_check.get("checked") and not entity_check.get("exists"))
    capability_missing = bool(capability and not capability.get("available"))
    entity_value = str((entity_check or {}).get("value") or "")

    if capability_missing and entity_missing:
        message = (
            f"当前数据源未接入{capability.get('label', '所需业务')}数据，无法计算该问题；"
            f"同时，产品主数据中未找到“{entity_value}”。"
        )
        suggestion = "请确认产品名称或编码；如果想查询生产数量，可将“卖出多少”改为“生产了多少”。"
        reason = "capability_and_entity_missing"
    elif capability_missing:
        message = f"当前数据源未接入{capability.get('label', '所需业务')}数据，因此无法计算该问题。"
        suggestion = "可以改为查询当前数据源已支持的生产、质量、设备或库存数据。"
        reason = "capability_missing"
    elif entity_missing:
        message = f"产品主数据中未找到“{entity_value}”，无法继续查询相关记录。"
        candidates = (entity_check or {}).get("candidates") or []
        suggestion = (
            "请检查产品名称或产品编码后重试。"
            if not candidates
            else "请确认是否想查询：" + "、".join(
                str(item.get("product_name") or item.get("product_code") or item.get("product_id"))
                for item in candidates
            )
        )
        reason = "entity_missing"
    else:
        topics = diagnostic.get("availableTopics") or []
        message = "当前数据源没有与该问题匹配的数据资源或已发布指标。"
        suggestion = (
            f"当前可分析范围包括：{'、'.join(topics)}。请换一种业务表述或联系管理员补充数据资源。"
            if topics
            else "请换一种业务表述，或联系管理员补充数据资源。"
        )
        reason = "semantic_resource_missing"

    return {
        "code": "CONTEXT_NO_MATCH",
        "message": "当前数据源没有可用于该问题的数据资源",
        "userSafeTitle": "当前数据暂时无法回答这个问题",
        "userSafeMessage": message,
        "suggestion": suggestion,
        "reason": reason,
        "showCodeToUser": False,
        "diagnostics": {
            "capability": capability,
            "entityCheck": entity_check,
        },
    }


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").lower().split())


def _sql_fingerprint(sql: str) -> str:
    return hashlib.sha256(_normalize_sql(sql).encode("utf-8")).hexdigest()


def _extract_sql(text: str) -> str:
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    candidate = candidate.strip()
    match = re.search(r"(?is)\b(?:WITH|SELECT)\b.*", candidate)
    if not match:
        raise ValueError("模型没有返回可识别的 SELECT SQL")
    return match.group(0).strip().rstrip(";")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", str(text), flags=re.IGNORECASE | re.DOTALL)
    candidate = (fenced.group(1) if fenced else str(text)).strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            decoded = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            return None
    return decoded if isinstance(decoded, dict) else None


def _published_metric_resolution(context: dict[str, Any]) -> dict[str, Any] | None:
    metrics = context.get("metrics") or []
    if not metrics:
        return None
    metric = metrics[0]
    return {
        "source": "published",
        "temporary": False,
        "name": metric.get("name") or "已发布指标",
        "definition": metric.get("definition") or "采用企业知识库中已发布的指标定义。",
        "formula": metric.get("physicalFormula") or metric.get("formula") or "采用已验证的数据源绑定公式",
        "assumptions": [],
        "modelConfidence": 0.96,
        "reason": "指标已发布，并已完成当前数据源的物理绑定验证。",
        "metricId": metric.get("id"),
        "version": metric.get("version"),
    }


def _inferred_query(raw: str, question: str) -> tuple[str, dict[str, Any]]:
    decoded = _extract_json_object(raw)
    payload = decoded.get("derivedMetric", {}) if decoded else {}
    sql_text = decoded.get("sql", "") if decoded else raw
    sql = _extract_sql(str(sql_text))
    try:
        model_confidence = float(payload.get("confidence", 0.55))
    except (TypeError, ValueError):
        model_confidence = 0.55
    model_confidence = min(max(model_confidence, 0.2), 0.65)
    assumptions = payload.get("assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    resolution = {
        "source": "agent_inferred",
        "temporary": True,
        "name": str(payload.get("name") or "临时推导口径").strip(),
        "definition": str(
            payload.get("definition") or f"根据“{question}”和当前数据源字段语义临时推导。"
        ).strip(),
        "formula": str(payload.get("formula") or "具体聚合表达式见本次实际执行 SQL。").strip(),
        "assumptions": [str(item).strip() for item in assumptions if str(item).strip()],
        "modelConfidence": model_confidence,
        "reason": str(
            payload.get("confidenceReason")
            or "当前数据源未命中已发布且已验证的指标口径，可信度已按临时推导规则降级。"
        ).strip(),
        "metricId": None,
        "version": "本次任务",
    }
    return sql, resolution


def _requires_period_comparison(task: dict[str, Any]) -> bool:
    question = str(task.get("normalizedQuestion") or task.get("originalQuestion") or "")
    paired_periods = (
        (("本月", "这个月"), ("上月", "上个月")),
        (("本周", "这周"), ("上周",)),
        (("本季度", "这个季度"), ("上季度",)),
        (("今年", "本年"), ("去年", "上年")),
    )
    return "环比" in question or any(
        any(current in question for current in current_terms)
        and any(previous in question for previous in previous_terms)
        for current_terms, previous_terms in paired_periods
    )


def _result_completeness_assessment(state: AnalysisState) -> tuple[int, list[str], int | None]:
    """Assess whether successful rows are sufficient to answer the requested task.

    The SQL gateway succeeding only proves that rows can be returned.  It does not
    prove that a comparison task contains both sides of the comparison.
    """
    execution = state.get("execution", {})
    if not execution.get("ok"):
        return 0, ["查询尚未成功执行，无法判断结果完整性。"], None
    if execution.get("truncated"):
        return 85, ["结果触发返回行数上限，完整性评分已降低。"], None

    task = state.get("task") or {}
    if not _requires_period_comparison(task):
        return 100, [], None

    columns = [str(item or "").lower() for item in execution.get("columns", [])]
    rows = execution.get("rows", []) or []
    if not rows:
        return 20, ["比较任务没有返回任何周期数据，无法完成比较。"], 55

    # Wide results may put current/previous values in two columns on one row.
    has_current_column = any(
        re.search(r"(?:^|_)(?:current|this)(?:_|$)|本月|本期|当前", column)
        for column in columns
    )
    has_previous_column = any(
        re.search(r"(?:^|_)(?:previous|prev|last)(?:_|$)|上月|上期|前期", column)
        for column in columns
    )
    if has_current_column and has_previous_column:
        return 100, [], None

    time_indexes = [
        index
        for index, column in enumerate(columns)
        if re.search(r"month|date|period|year|week|quarter|月份|日期|周期|年度|周|季度", column)
    ]
    periods = {
        str(row[index])
        for row in rows
        for index in time_indexes
        if index < len(row) and row[index] not in (None, "")
    }
    if len(periods) >= 2:
        return 100, [], None

    if len(periods) == 1:
        period = next(iter(periods))
        return (
            35,
            [f"比较任务要求至少两个周期，但结果仅覆盖 1 个周期（{period}），无法完成完整比较。"],
            60,
        )
    return (
        50,
        ["比较任务的结果中未找到可验证的双周期字段，无法确认两侧数据是否完整。"],
        60,
    )


def _confidence_assessment(state: AnalysisState) -> dict[str, Any]:
    resolution = state.get("metric_resolution") or {}
    inferred = resolution.get("source") == "agent_inferred"
    metric_score = round(float(resolution.get("modelConfidence", 0.55 if inferred else 0.9)) * 100)
    policy = state.get("semantic_context", {}).get("executionPolicy", {})
    schema_score = 95 if policy.get("allowedTables") and policy.get("allowedColumns") else 75
    validation_score = 100 if state.get("validation", {}).get("ok") else 0
    task_score = int(state.get("validation", {}).get("taskComplianceScore", 100 if validation_score else 0))
    execution = state.get("execution", {})
    execution_score, completeness_issues, completeness_cap = _result_completeness_assessment(state)
    score = round(
        metric_score * 0.35
        + schema_score * 0.15
        + validation_score * 0.2
        + task_score * 0.15
        + execution_score * 0.15
    )
    if inferred:
        score = min(score, 78)
    if completeness_cap is not None:
        score = min(score, completeness_cap)
    level = "high" if score >= 85 else "medium" if score >= 65 else "low"
    label = {"high": "高可信度", "medium": "中等可信度", "low": "低可信度"}[level]
    reasons = [
        resolution.get("reason", ""),
        "SQL 已通过只读、表字段白名单和语法校验。" if validation_score else "SQL 尚未通过完整校验。",
        "SQL 已覆盖用户指定的时间、维度和展示约束。" if task_score >= 90 else "SQL 对用户任务约束的覆盖不完整。",
        "查询已成功执行。" if execution.get("ok") else "查询尚未成功执行。",
        *completeness_issues,
    ]
    return {
        "score": score,
        "level": level,
        "label": label,
        "components": {
            "metricDefinition": metric_score,
            "schemaCoverage": schema_score,
            "sqlValidation": validation_score,
            "taskCompliance": task_score,
            "resultCompleteness": execution_score,
        },
        "reasons": [item for item in reasons if item],
        "cappedByInferredMetric": inferred,
        "cappedByIncompleteResult": completeness_cap is not None,
        "completenessIssues": completeness_issues,
    }


def _prompt_context(context: dict[str, Any]) -> str:
    safe = {
        "question": context.get("question"),
        "source": context.get("source"),
        "metrics": context.get("metrics", []),
        "businessRules": context.get("businessRules", []),
        "tables": context.get("tables", []),
        "relations": context.get("relations", []),
        "executionPolicy": context.get("executionPolicy", {}),
        "conversationContext": context.get("conversationContext", []),
        "dependencyResults": context.get("dependencyResults", []),
        "analysisSkills": context.get("analysisSkills", []),
    }
    return json.dumps(safe, ensure_ascii=False, default=str)


def _build_retrieval_question(task_question: str, history: list[dict[str, str]]) -> str:
    """Build a short semantic-retrieval query without leaking full chat history into it.

    Retrieval needs the current task and, for follow-up questions, a small amount of
    prior user intent. Assistant answers and branch checkpoint payloads belong in the
    LLM context, not in the retrieval API's bounded ``question`` field.
    """
    current = str(task_question or "").strip()
    if len(current) >= RETRIEVAL_QUERY_MAX_LENGTH:
        return current[:RETRIEVAL_QUERY_MAX_LENGTH]

    prior_questions = []
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content") or "").strip()
        if content and content != current and content not in prior_questions:
            prior_questions.append(content)
        if len(prior_questions) >= RETRIEVAL_HISTORY_ITEMS:
            break

    if not prior_questions:
        return current

    prefix = "前序问题："
    separator = "；当前问题："
    available = RETRIEVAL_QUERY_MAX_LENGTH - len(prefix) - len(separator) - len(current)
    if available <= 0:
        return current[:RETRIEVAL_QUERY_MAX_LENGTH]

    history_text = "；".join(reversed(prior_questions))[:available]
    return f"{prefix}{history_text}{separator}{current}"


def _requires_history_context(question: str) -> bool:
    """Only bring chat history into retrieval when the current turn is a real follow-up."""
    return bool(
        re.search(
            r"(?:上一步|上述|刚才|前面|继续|再看|基于此|根据这些|其中|"
            r"该对象|它|其(?!他|余|中)|这个(?!月|周|季度|年度|年份|星期|礼拜|天|时段))",
            str(question or ""),
        )
    )


def _time_field_candidates(context: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for metric in context.get("metrics", []):
        name = str(metric.get("timeField") or metric.get("physicalTimeField") or "").strip()
        if name:
            candidates.append(name.split(".")[-1])
    for table in context.get("tables", []):
        for field in table.get("fields", []):
            name = str(field.get("name") or field.get("fieldName") or "").strip()
            description = " ".join(
                str(field.get(key) or "") for key in ("description", "comment", "cnName", "type", "dataType")
            )
            if name and (
                re.search(r"(?:date|time|day|month|year|日期|时间|月份|年度)", name, re.IGNORECASE)
                or re.search(r"(?:日期|时间|月份|年度|date|time)", description, re.IGNORECASE)
            ):
                candidates.append(name)
    return list(dict.fromkeys(candidates))


def _task_constraint_validation(
    sql: str,
    task: dict[str, Any],
    context: dict[str, Any],
) -> tuple[bool, list[str], str, int]:
    """Validate that a safe SQL statement still answers the user's actual task."""
    normalized = _normalize_sql(sql)
    checks: list[str] = []
    failures: list[str] = []
    time_constraint = task.get("timeConstraint") or {}
    requested_chart = task.get("requestedChart")
    group_by = set(task.get("groupBy") or [])
    time_fields = _time_field_candidates(context)
    sql_time_fields = [field for field in time_fields if re.search(rf"\b{re.escape(field.lower())}\b", normalized)]

    if time_constraint and time_fields:
        if " where " not in f" {normalized} ":
            failures.append(f"缺少“{time_constraint.get('label', '指定时间范围')}”的 WHERE 时间过滤")
        if not sql_time_fields:
            failures.append(f"时间过滤未使用已登记时间字段（可用：{', '.join(time_fields[:4])}）")
        kind = time_constraint.get("kind")
        if kind == "current_month":
            month_markers = (
                "curdate(", "current_date", "date_format(", "date_trunc(", "strftime(",
                "extract(month", "month(", "year(",
            )
            if not any(marker in normalized for marker in month_markers):
                failures.append("“本月”未转换为数据库可执行的动态月份边界")
        elif kind == "current_vs_previous_month":
            periods = time_constraint.get("periods") or []
            current = next((item for item in periods if item.get("key") == "current"), {})
            previous = next((item for item in periods if item.get("key") == "previous"), {})
            required_boundaries = [
                previous.get("startInclusive"),
                current.get("endExclusive"),
            ]
            missing_boundaries = [
                boundary for boundary in required_boundaries
                if boundary and boundary.lower() not in normalized
            ]
            if missing_boundaries:
                failures.append(
                    "“本月至今与上月”未覆盖完整双周期边界"
                    f"（必须包含：{', '.join(required_boundaries)}）"
                )
            month_markers = ("date_format(", "date_trunc(", "strftime(", "extract(month", "month(")
            groups_by_period_alias = bool(
                re.search(
                    r"\bgroup\s+by\s+[`\"]?[a-z_]*(?:period|month)[a-z_]*[`\"]?\b",
                    normalized,
                )
            )
            has_month_bucket = any(marker in normalized for marker in month_markers) or groups_by_period_alias
            if "group by" not in normalized or not has_month_bucket:
                failures.append("月度比较必须按月份分组并分别返回本月与上月")
        checks.append(f"时间范围：{time_constraint.get('label', kind)}")
    elif time_constraint:
        checks.append("当前语义上下文未登记时间字段，无法执行时间约束的确定性校验")

    if requested_chart == "line":
        select_clause = normalized.split(" from ", 1)[0]
        has_time_axis = any(field.lower() in select_clause for field in sql_time_fields)
        if time_fields and not has_time_axis:
            failures.append("折线图结果缺少日期/时间横轴字段")
        if time_constraint and time_fields and "group by" not in normalized:
            failures.append("折线图时间序列缺少按日期或时间粒度聚合")
        checks.append("展示方式：折线图")

    if requested_chart == "line" and time_constraint and "产品" not in group_by:
        group_clause = normalized.split(" group by ", 1)[1].split(" order by ", 1)[0] if " group by " in normalized else ""
        if re.search(r"\b(?:product_name|product_code|product_id)\b", group_clause):
            failures.append("用户未要求按产品拆分，SQL 不应额外增加产品分组")

    if group_by:
        checks.append(f"显式分组：{', '.join(sorted(group_by))}")

    score = 100 if not failures else max(0, 100 - 25 * len(failures))
    if failures:
        return False, checks, "；".join(failures), score
    checks.append("任务约束校验通过")
    return True, checks, "", score


def _clarification_answers(response: Any, fields: list[dict[str, Any]]) -> dict[str, str]:
    raw = response.get("answer") if isinstance(response, dict) and "answer" in response else response
    if isinstance(raw, dict):
        return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}
    text = str(raw or "").strip()
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return {str(key): str(value).strip() for key, value in decoded.items() if str(value).strip()}
        except json.JSONDecodeError:
            pass
    return {fields[0]["id"]: text} if len(fields) == 1 and text else {}


def _model_route_decision(
    raw: str, unknown_segments: list[dict[str, Any]]
) -> tuple[dict[int, str], dict[int, str]]:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", str(raw), flags=re.IGNORECASE | re.DOTALL)
    candidate = (fenced.group(1) if fenced else str(raw)).strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError:
        return {item["index"]: "conversation" for item in unknown_segments}, {}
    items = decoded.get("segments", decoded) if isinstance(decoded, dict) else decoded
    if not isinstance(items, list):
        return {item["index"]: "conversation" for item in unknown_segments}, {}
    allowed_indices = {item["index"] for item in unknown_segments}
    labels: dict[int, str] = {}
    responses: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind", "")).lower()
        if index in allowed_indices and kind in {"analysis", "conversation", "report"}:
            labels[index] = kind
            response = str(item.get("response") or "").strip()
            if kind == "conversation" and response:
                responses[index] = response[:800]
    for item in unknown_segments:
        labels.setdefault(item["index"], "conversation")
    return labels, responses


def _route_model_prompt(
    unknown_segments: list[dict[str, Any]],
    conversation_context: list[dict[str, str]],
) -> tuple[str, str]:
    system = (
        "你是 ForgeSight，面向制造业数据底座的智能问析 Agent。你的角色是帮助企业用户理解生产、"
        "质量、设备和库存数据；涉及企业数据时只能走只读分析，不编造数据，也不执行写操作。"
        "现在结合最近对话，对每个片段判断用户真正想做的事情。"
        "analysis=需要查询或解释企业生产、质量、设备、库存、指标、表字段；"
        "report=用户想新建、生成、准备、整理、产出或发送一份周期性/专题业务报告，"
        "即使没有出现“报告”或“周报”这些字样，只要语义是在请求一份可交付的经营总结，也归为 report。"
        "report 只表示创建新报告，不包括查看、解释或总结已有报告；创建报告时先让用户选择报告类型，"
        "不要在路由阶段查询数据。"
        "conversation=不需要企业数据的正常对话。对 conversation 应以 ForgeSight 身份自然、简洁地回应，"
        "可以正常交流，但不得假装已经查询过企业数据；必要时再引导用户提出业务问题。"
        "仅输出 JSON：{\"segments\":[{\"index\":0,\"kind\":\"analysis|conversation|report\","
        "\"response\":\"仅 conversation 填写的中文回答\"}]}。"
    )
    user = json.dumps(
        {
            "recentConversation": conversation_context[-4:],
            "segments": [
                {"index": item["index"], "text": item["text"]}
                for item in unknown_segments
            ],
        },
        ensure_ascii=False,
    )
    return system, user


def build_analysis_graph(deps: GraphDependencies, checkpointer=None):
    def route_request(state: AnalysisState) -> dict:
        preclassified_labels = {
            int(index): kind
            for index, kind in (state.get("preclassified_route_labels") or {}).items()
        }
        preclassified_responses = {
            int(index): response
            for index, response in (state.get("preclassified_route_responses") or {}).items()
        }
        route = build_request_route(
            state["user_question"],
            preclassified_labels,
            conversation_context=state.get("conversation_context") or [],
        )
        model_responses = [
            preclassified_responses[item["index"]]
            for item in route["segments"]
            if item["kind"] == "conversation" and item["index"] in preclassified_responses
        ]
        if model_responses:
            route["conversationResponse"] = "\n\n".join(model_responses)
        unknown = route["unknownSegments"]
        if unknown:
            system, user = _route_model_prompt(
                unknown,
                state.get("conversation_context") or [],
            )
            try:
                labels, responses = _model_route_decision(
                    deps.generate_text(state, "intent.route", system, user),
                    unknown,
                )
            except Exception:  # noqa: BLE001
                # When the router model is unavailable, unknown requests stay out of
                # SQL rather than being silently promoted to an executable analysis.
                labels = {item["index"]: "conversation" for item in unknown}
                responses = {}
            route = build_request_route(
                state["user_question"],
                labels,
                state.get("conversation_context") or [],
            )
            model_responses = [
                responses[item["index"]]
                for item in route["segments"]
                if item["kind"] == "conversation" and item["index"] in responses
            ]
            if model_responses:
                route["conversationResponse"] = "\n\n".join(model_responses)
        return {
            "intent_kind": route["kind"],
            "analysis_question": route["analysisQuestion"],
            "direct_response": route["conversationResponse"],
            "request_segments": route["segments"],
            "blocked": False,
            "error": None,
        }

    def intent_guard(state: AnalysisState) -> dict:
        lowered = state.get("analysis_question", state["user_question"]).lower()
        if any(term in lowered for term in WRITE_TERMS):
            return {
                "blocked": True,
                "error": {
                    "code": "WRITE_INTENT_BLOCKED",
                    "category": "security",
                    "userSafeMessage": "当前系统仅支持只读分析，本次没有生成或执行写入语句。",
                },
            }
        return {"blocked": False, "error": None}

    def plan_tasks(state: AnalysisState) -> dict:
        planning = build_intent_plan(
            state.get("analysis_question") or state["user_question"],
            state.get("conversation_context", []),
        )
        prompt = planning.get("clarification")
        clarification_response = None
        if prompt:
            response = interrupt(prompt)
            clarification_response = _clarification_answers(response, prompt.get("fields", []))
            planning = build_intent_plan(
                state.get("analysis_question") or state["user_question"],
                state.get("conversation_context", []),
                clarification_response,
            )
        return {
            "tasks": planning["tasks"],
            "task_index": 0,
            "task_results": [],
            "clarification_response": clarification_response,
        }

    def activate_task(state: AnalysisState) -> dict:
        task = state["tasks"][state.get("task_index", 0)]
        return {
            "task": task,
            "retry_count": 0,
            "attempted_sqls": [],
            "semantic_context": {},
            "skill_context": {},
            "metric_resolution": {},
            "confidence": {},
            "sql": "",
            "validation": {},
            "execution": {},
            "conclusion": "",
            "clarification_response": None,
            "waiting_user": False,
            "error": None,
        }

    def retrieve_skill(state: AnalysisState) -> dict:
        if not state.get("skill_enhancement_enabled") or deps.retrieve_skills is None:
            return {"skill_context": {"enabled": False, "variant": "off", "skills": [], "reason": "feature_disabled"}}
        try:
            question = state.get("task", {}).get("normalizedQuestion") or state["user_question"]
            return {"skill_context": deps.retrieve_skills(question, state["source_id"])}
        except Exception as exc:  # 增强能力必须可降级，不能阻断基础分析链路
            return {"skill_context": {"enabled": True, "variant": "on", "skills": [], "reason": "retrieval_degraded", "warning": str(exc)}}

    def retrieve_context(state: AnalysisState) -> dict:
        try:
            task_question = state.get("task", {}).get("normalizedQuestion") or state["user_question"]
            history = (
                state.get("conversation_context", [])[-6:]
                if _requires_history_context(task_question)
                else []
            )
            dependency_ids = set(state.get("task", {}).get("dependsOn", []))
            dependency_results = [
                item for item in state.get("task_results", [])
                if (item.get("task") or {}).get("id") in dependency_ids
            ]
            if dependency_results:
                dependency_questions = "；".join(
                    (item.get("task") or {}).get("normalizedQuestion", "")
                    for item in dependency_results
                )
                task_question = f"{dependency_questions}；基于上一步结果，{task_question}"
            retrieval_question = _build_retrieval_question(task_question, history)
            context = deps.retrieve_context(retrieval_question, state["source_id"])
            context["analysisSkills"] = (state.get("skill_context") or {}).get("skills", [])
            context["conversationContext"] = history
            context["dependencyResults"] = [
                {
                    "task": item.get("task", {}),
                    "execution": {
                        **(item.get("execution") or {}),
                        "rows": (item.get("execution") or {}).get("rows", [])[:20],
                    },
                    "conclusion": item.get("conclusion", ""),
                }
                for item in dependency_results
            ]
        except Exception as exc:  # noqa: BLE001 - 节点必须转结构化错误
            return {"error": {"code": "CONTEXT_RETRIEVAL_FAILED", "message": str(exc)}}
        coverage = context.get("coverage", {})
        if coverage.get("status") == "needs_clarification":
            metrics = context.get("metrics", [])
            response = interrupt(
                {
                    "kind": "clarification",
                    "question": "检测到多个可能的指标口径，请确认本次要使用哪一个。",
                    "options": [
                        {"label": item.get("name", item.get("id", "未命名指标")), "value": item.get("id") or item.get("name")}
                        for item in metrics[:5]
                    ],
                    "allowCustom": True,
                    "taskId": state.get("task", {}).get("id"),
                }
            )
            answer = response.get("answer") if isinstance(response, dict) else str(response)
            selected = [item for item in metrics if answer in {item.get("id"), item.get("name")}]
            if selected:
                context["metrics"] = selected
            context["coverage"] = {
                **coverage,
                "status": "ready",
                "clarifiedByUser": answer,
                "metricCount": len(context.get("metrics", [])),
            }
            resolution = _published_metric_resolution(context)
            return {
                "semantic_context": context,
                "metric_resolution": resolution or {},
                "waiting_user": False,
                "clarification_response": {"answer": answer},
            }
        if coverage.get("status") == "no_match":
            entity_check = _diagnose_entity(
                deps,
                state["source_id"],
                context.get("diagnostics") or {},
            )
            if entity_check:
                context.setdefault("diagnostics", {})["entityCheck"] = entity_check
            return {
                "semantic_context": context,
                "error": _no_match_error(context, entity_check),
            }
        resolution = _published_metric_resolution(context)
        if resolution:
            context["metricResolution"] = resolution
        return {
            "semantic_context": context,
            "metric_resolution": resolution or {},
            "waiting_user": False,
            "error": None,
        }

    def propose_modeling(state: AnalysisState) -> dict:
        """确定性建模提案：任务类型/目标列/特征/候选算法 + 白名单取数 SQL。

        无 LLM 调用：SQL 由已登记字段拼接并过 validate_sql 兜底，COUNT 只读统计样本量。
        """
        context = state.get("semantic_context") or {}
        question = (state.get("task") or {}).get("normalizedQuestion") or state["user_question"]
        tables = context.get("tables") or []
        if not tables:
            return {"error": {"code": "MODELING_CONTEXT_EMPTY", "message": "未定位到可用于建模的数据表"}}
        allowed = set(context.get("executionPolicy", {}).get("allowedTables", []))
        table = tables[0]
        task_type = _detect_modeling_task_type(question)
        fields = table.get("fields") or []
        target_field, target_label = _pick_modeling_target(question, fields, task_type)
        if task_type in ("classification", "regression") and not target_field:
            return {
                "error": {
                    "code": "MODELING_TARGET_MISSING",
                    "message": "未能从已登记字段中识别预测目标列，可前往智能建模人工补充目标字段后创建任务",
                }
            }
        features = _modeling_features(fields, target_field)
        if not features:
            return {"error": {"code": "MODELING_NO_FEATURES", "message": "该表没有可用于建模的特征列"}}
        select_columns = [*features, target_field] if target_field else features
        quoted = ", ".join(f"`{column}`" for column in select_columns)
        sql = f"SELECT {quoted} FROM `{table['name']}` LIMIT {MODELING_SAMPLE_LIMIT}"
        check = deps.validate_sql(sql, allowed)
        if not check.ok:
            return {"error": {"code": "MODELING_SQL_REJECTED", "message": f"取数 SQL 未通过白名单校验：{check.reason}"}}
        sample_count = None
        count_result = deps.execute_sql(
            f"SELECT COUNT(*) AS n FROM `{table['name']}`", state["source_id"]
        )
        if count_result.ok and count_result.rows:
            try:
                sample_count = int(count_result.rows[0][0])
            except (TypeError, ValueError):
                sample_count = None
        proposal = {
            "title": f"{table.get('semanticName') or table['name']}·{MODELING_TASK_TYPE_LABELS[task_type]}模型",
            "taskType": task_type,
            "taskTypeLabel": MODELING_TASK_TYPE_LABELS[task_type],
            "goal": question,
            "dataset": table.get("semanticName") or table["name"],
            "datasetSql": sql,
            "sampleCount": sample_count,
            "target": target_field,
            "targetLabel": target_label,
            "metric": MODELING_METRIC_LABELS[task_type],
            "features": features,
            "candidates": MODELING_RECOMMENDED_CANDIDATES[task_type],
        }
        reusable = (
            deps.find_reusable_models(question, state["source_id"], task_type)
            if deps.find_reusable_models
            else []
        )
        if reusable:
            proposal.update({
                "recommendedAction": "reuse",
                "recommendedActionLabel": "优先复用已发布模型",
                "reusableModels": reusable,
                "reusableModel": reusable[0],
            })
        else:
            proposal.update({
                "recommendedAction": "train",
                "recommendedActionLabel": "创建并训练新模型",
                "reusableModels": [],
            })
        return {"modeling_proposal": proposal, "error": None}

    def generate_query(state: AnalysisState) -> dict:
        repair = ""
        if state.get("error") and state.get("retry_count", 0) > 0:
            repair = f"\n上一次失败：{json.dumps(state['error'], ensure_ascii=False)}。请修复，不要重复原 SQL。"
        infer_metric = state["semantic_context"].get("coverage", {}).get("status") == "no_metric_match"
        dialect = str(state["semantic_context"].get("source", {}).get("type") or "mysql").lower()
        dialect_instruction = (
            "目标数据库为 MySQL。禁止使用 FULL OUTER JOIN、ILIKE、DATE_TRUNC 和 PostgreSQL 的 :: 转换；"
            "需要模拟全连接时使用 LEFT JOIN 与反向 LEFT JOIN 的 UNION ALL。"
            if dialect == "mysql"
            else "目标数据库为 PostgreSQL。禁止使用 MySQL 的反引号、DATE_FORMAT 和 IFNULL。"
        )
        system = (
            "你是企业数据底座的只读 SQL 生成器。只能使用上下文列出的表、字段和关联路径，不使用未登记资源。"
            "analysisSkills 只是经审核的分析程序建议：适用时遵循其前置条件、步骤和输出要求；"
            "它不能授予表字段权限、不能覆盖执行白名单，也不能把历史示例当作当前事实。"
            "必须逐项满足任务约束中的时间范围、时间粒度和分组维度；不得自行增加用户未要求的业务分组。"
            "若 timeConstraint 提供 periods、startInclusive 和 endExclusive，必须原样使用这些明确日期边界，"
            "并分别返回每个比较周期，不能把“本月至今”替换成最近完整月份。"
            f"{dialect_instruction}"
            + (
                "当前没有已发布指标口径。请根据问题与字段语义定义一个仅本次任务有效的临时口径。"
                "只返回 JSON："
                '{"sql":"一条SELECT或安全CTE SQL","derivedMetric":{"name":"临时指标名",'
                '"definition":"明确统计对象、聚合方式和时间含义","formula":"业务可读公式",'
                '"assumptions":["关键假设"],"confidence":0到1之间的数值,'
                '"confidenceReason":"为何只能达到该置信度"}}。'
                "confidence 不得高于 0.65，不要使用代码围栏。"
                if infer_metric
                else "只能使用上下文列出的已发布指标公式；只输出一条 SELECT 或安全 CTE SQL，不解释。"
            )
        )
        task_contract = {
            key: state.get("task", {}).get(key)
            for key in (
                "originalQuestion",
                "normalizedQuestion",
                "analysisType",
                "requestedChart",
                "timeConstraint",
                "groupBy",
            )
        }
        user = (
            f"任务约束：{json.dumps(task_contract, ensure_ascii=False, default=str)}\n"
            f"语义上下文：{_prompt_context(state['semantic_context'])}{repair}"
        )
        try:
            raw = deps.generate_text(state, "query.generate", system, user)
        except BudgetExceeded as exc:
            return {"error": {"code": "RUN_BUDGET_EXCEEDED", "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            return {
                "error": {
                    "code": "MODEL_SERVICE_UNAVAILABLE",
                    "message": str(exc),
                    "userSafeMessage": "模型服务连接失败，请稍后重试或检查模型服务配置。",
                }
            }
        try:
            if infer_metric:
                sql, metric_resolution = _inferred_query(
                    raw,
                    state.get("task", {}).get("normalizedQuestion") or state["user_question"],
                )
            else:
                sql = _extract_sql(raw)
                metric_resolution = state.get("metric_resolution") or _published_metric_resolution(
                    state["semantic_context"]
                ) or {}
        except Exception as exc:  # noqa: BLE001
            return {
                "error": {
                    "code": "SQL_GENERATION_FAILED",
                    "message": str(exc),
                    "userSafeMessage": "模型未能生成有效查询，请调整问题后重试。",
                }
            }
        fingerprint = _sql_fingerprint(sql)
        if fingerprint in state.get("attempted_sqls", []):
            return {
                "sql": sql,
                "error": {"code": "RETRY_NO_PROGRESS", "message": "连续生成相同失败 SQL，已停止自动修复"},
            }
        semantic_context = dict(state["semantic_context"])
        semantic_context["metricResolution"] = metric_resolution
        return {
            "sql": sql,
            "metric_resolution": metric_resolution,
            "semantic_context": semantic_context,
            "error": None,
        }

    def validate_query(state: AnalysisState) -> dict:
        if state.get("error") or not state.get("sql"):
            return {}
        allowed = set(state["semantic_context"].get("executionPolicy", {}).get("allowedTables", []))
        result = deps.validate_sql(state["sql"], allowed)
        payload = asdict(result)
        if not result.ok:
            return {
                "validation": payload,
                "error": {"code": "SQL_VALIDATION_FAILED", "message": result.reason, "retryable": True},
            }
        dialect = str(state["semantic_context"].get("source", {}).get("type") or "mysql")
        dialect_check = validate_dialect_compatibility(state["sql"], dialect)
        payload["checks"] = [*payload.get("checks", []), *dialect_check.checks]
        if not dialect_check.ok:
            return {
                "validation": payload,
                "error": {
                    "code": "SQL_DIALECT_MISMATCH",
                    "message": dialect_check.reason,
                    "retryable": True,
                    "userSafeTitle": "SQL 与当前数据库方言不兼容",
                    "userSafeMessage": dialect_check.reason,
                    "suggestion": "系统会自动重新生成兼容当前数据库的查询，达到重试上限后再提示人工调整。",
                },
            }
        task_ok, task_checks, task_reason, task_score = _task_constraint_validation(
            state["sql"],
            state.get("task", {}),
            state["semantic_context"],
        )
        payload["taskChecks"] = task_checks
        payload["taskComplianceScore"] = task_score
        payload["checks"] = [*payload.get("checks", []), *task_checks]
        if not task_ok:
            period_comparison = (state.get("task", {}).get("timeConstraint") or {}).get("kind") == "current_vs_previous_month"
            return {
                "validation": payload,
                "error": {
                    "code": "TASK_CONSTRAINT_MISMATCH",
                    "message": task_reason,
                    "retryable": True,
                    "userSafeTitle": "分析条件未能完整落实",
                    "userSafeMessage": (
                        "系统生成的查询没有同时按周期返回本月与上月数据，已停止执行以避免给出不完整的比较结论。"
                        if period_comparison
                        else "系统生成的查询未完整覆盖你指定的时间、维度或展示条件。"
                    ),
                    "suggestion": "系统会先自动修正查询；达到重试上限后，可重新发起问题。",
                },
            }
        if deps.explain_sql is not None:
            cost_check = deps.explain_sql(state["sql"], state["source_id"])
            cost_payload = asdict(cost_check)
            payload["costGate"] = cost_payload
            payload["checks"] = [*payload.get("checks", []), *cost_payload.get("checks", [])]
            if not cost_check.ok:
                return {
                    "validation": payload,
                    "error": {
                        "code": "QUERY_EXPLAIN_FAILED",
                        "message": cost_check.reason,
                        "retryable": cost_check.retryable,
                        "userSafeTitle": "数据库无法解析生成的查询",
                        "userSafeMessage": cost_check.reason,
                        "suggestion": (
                            "系统将自动尝试生成兼容当前数据库的 SQL。"
                            if cost_check.retryable
                            else "请稍后重试；若持续出现，请检查数据源连接与数据库权限。"
                        ),
                    },
                }
            if not cost_check.allowed:
                return {
                    "validation": payload,
                    "error": {
                        "code": "QUERY_COST_EXCEEDED",
                        "message": cost_check.reason,
                        "retryable": True,
                    },
                }
        return {"validation": payload, "error": None}

    def execute_query(state: AnalysisState) -> dict:
        result = deps.execute_sql(state["sql"], state["source_id"])
        payload = asdict(result)
        if result.ok:
            return {"execution": payload, "error": None}
        lowered = (result.error or "").lower()
        retryable = any(term in lowered for term in ("unknown column", "不存在", "syntax", "语法", "column"))
        return {
            "execution": payload,
            "error": {"code": "SQL_EXECUTION_FAILED", "message": result.error, "retryable": retryable},
        }

    def prepare_retry(state: AnalysisState) -> dict:
        attempted = list(state.get("attempted_sqls", []))
        if state.get("sql"):
            attempted.append(_sql_fingerprint(state["sql"]))
        return {"attempted_sqls": attempted, "retry_count": state.get("retry_count", 0) + 1}

    def explain_result(state: AnalysisState) -> dict:
        execution = state["execution"]
        preview = {
            "columns": execution.get("columns", []),
            "rows": execution.get("rows", [])[:20],
            "rowCount": execution.get("row_count", 0),
            "truncated": execution.get("truncated", False),
        }
        system = (
            "你是制造业数据分析助手。仅根据查询结果和已确认指标口径给出简洁结论；"
            "不得编造结果中没有的数字，空结果应明确说明无数据。"
        )
        user = (
            f"问题：{state.get('task', {}).get('normalizedQuestion') or state['user_question']}\n"
            f"指标：{json.dumps(state['semantic_context'].get('metrics', []), ensure_ascii=False)}\n"
            f"本次口径解析：{json.dumps(state.get('metric_resolution', {}), ensure_ascii=False)}\n"
            f"结果：{json.dumps(preview, ensure_ascii=False, default=str)}"
        )
        try:
            conclusion = deps.generate_text(state, "result.explain", system, user)
            confidence = _confidence_assessment(state)
            resolution = state.get("metric_resolution") or {}
            disclosure = ""
            if resolution.get("source") == "agent_inferred":
                assumptions = "；".join(resolution.get("assumptions") or []) or "未提供额外业务约束"
                disclosure = (
                    f"口径说明：当前数据源未命中已发布指标，本次由 Agent 临时推导“{resolution.get('name')}”。"
                    f"定义：{resolution.get('definition')}；公式：{resolution.get('formula')}；"
                    f"假设：{assumptions}。该口径仅对本次分析有效。"
                )
            text = "\n\n".join(item for item in (disclosure, conclusion.strip()) if item)
            return {
                "conclusion": text,
                "confidence": confidence,
                "metric_resolution": resolution,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            code = "RUN_BUDGET_EXCEEDED" if isinstance(exc, BudgetExceeded) else "RESULT_EXPLANATION_FAILED"
            return {"error": {"code": code, "message": str(exc)}}

    def advance_task(state: AnalysisState) -> dict:
        results = list(state.get("task_results", []))
        results.append(
            {
                "task": state.get("task", {}),
                "context": state.get("semantic_context", {}),
                "sql": state.get("sql"),
                "validation": state.get("validation", {}),
                "execution": state.get("execution", {}),
                "conclusion": state.get("conclusion", ""),
                "confidence": state.get("confidence", {}),
                "metricResolution": state.get("metric_resolution", {}),
            }
        )
        return {"task_results": results, "task_index": state.get("task_index", 0) + 1}

    def after_route(state: AnalysisState) -> str:
        return END if not state.get("analysis_question") else "intent_guard"

    def after_guard(state: AnalysisState) -> str:
        return END if state.get("blocked") else "plan_tasks"

    def after_context(state: AnalysisState) -> str:
        if state.get("error"):
            return END
        if (state.get("task") or {}).get("analysisType") == "modeling":
            return "propose_modeling"
        return "generate_query"

    def after_generate(state: AnalysisState) -> str:
        return END if state.get("error") else "validate_query"

    def after_validate(state: AnalysisState) -> str:
        if not state.get("error"):
            return "execute_query"
        if state["error"].get("retryable") and state.get("retry_count", 0) < 2:
            return "prepare_retry"
        return END

    def after_execute(state: AnalysisState) -> str:
        if not state.get("error"):
            return "explain_result"
        if state["error"].get("retryable") and state.get("retry_count", 0) < 2:
            return "prepare_retry"
        return END

    def after_advance(state: AnalysisState) -> str:
        return "activate_task" if state.get("task_index", 0) < len(state.get("tasks", [])) else END

    graph = StateGraph(AnalysisState)
    graph.add_node("route_request", route_request)
    graph.add_node("intent_guard", intent_guard)
    graph.add_node("plan_tasks", plan_tasks)
    graph.add_node("activate_task", activate_task)
    graph.add_node("retrieve_skill", retrieve_skill)
    graph.add_node("retrieve_context", retrieve_context)
    graph.add_node("propose_modeling", propose_modeling)
    graph.add_node("generate_query", generate_query)
    graph.add_node("validate_query", validate_query)
    graph.add_node("execute_query", execute_query)
    graph.add_node("prepare_retry", prepare_retry)
    graph.add_node("explain_result", explain_result)
    graph.add_node("advance_task", advance_task)
    graph.add_edge(START, "route_request")
    graph.add_conditional_edges("route_request", after_route)
    graph.add_conditional_edges("intent_guard", after_guard)
    graph.add_edge("plan_tasks", "activate_task")
    graph.add_edge("activate_task", "retrieve_skill")
    graph.add_edge("retrieve_skill", "retrieve_context")
    graph.add_conditional_edges("retrieve_context", after_context)
    graph.add_conditional_edges("generate_query", after_generate)
    graph.add_conditional_edges("validate_query", after_validate)
    graph.add_conditional_edges("execute_query", after_execute)
    graph.add_edge("prepare_retry", "generate_query")
    graph.add_edge("explain_result", "advance_task")
    graph.add_conditional_edges("advance_task", after_advance)
    return graph.compile(checkpointer=checkpointer)
