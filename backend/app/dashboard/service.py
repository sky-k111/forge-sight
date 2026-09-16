"""Dashboard persistence and deterministic governed widget execution."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from app.knowledge.metric_semantics import metric_semantics

from fastapi import HTTPException

from app.agent.query_compiler import compile_query
from app.agent.query_requirements import bind_query_requirements
from app.dashboard.schemas import DashboardConfig, Layout, Widget, WidgetQuery
from app.db.models import (
    DashboardPreference,
    DataSource,
    DataTable,
    KnowledgeMetric,
    MetricFieldBinding,
    MetricSourceBinding,
    MetricTableBinding,
    TableField,
    TableRelation,
)
from app.db.platform import PlatformSession
from app.gateway.connectors import get_adapter
from app.gateway.connectors.base import PhysicalTableRef
from app.gateway.executor import execute_readonly
from app.tenancy.service import ensure_source_access


log = logging.getLogger(__name__)


def _json_list(raw: str | list | None) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _unit(metric: KnowledgeMetric) -> str:
    precision = str(metric.precision or "")
    if "百分比" in precision:
        return "%"
    if "分钟" in precision:
        return "分钟"
    if "小时" in precision:
        return "小时"
    if "数量" in precision or "整数" in precision:
        return "件"
    return ""


def _metric_kinds(physical_formula: str, time_field: str, dimensions: list) -> list[str]:
    """Expose only measures that can obey the dashboard's shared period selector."""

    formula = str(physical_formula or "").upper()
    aggregatable = any(token in formula for token in ("SUM(", "AVG(", "COUNT(", "MIN(", "MAX("))
    if not aggregatable or not str(time_field or "").strip():
        return []
    kinds = ["kpi", "trend"]
    if dimensions:
        kinds.append("rank")
    return kinds


def _ordered_table_bindings(session, binding_id: int) -> list[MetricTableBinding]:
    rows = session.query(MetricTableBinding).filter_by(binding_id=binding_id).order_by(MetricTableBinding.id).all()
    return sorted(rows, key=lambda item: 0 if item.role == "事实表" else 1)


def _dimension_bindings(session, source_id: int, binding_id: int, declared: list) -> dict[str, dict[str, str]]:
    """Resolve dashboard dimensions from governed mappings or unambiguous catalog labels.

    Publishing a metric may predate explicit dimension bindings. In that case we
    accept only one highest-scoring field inside the metric's bound tables; ties
    fail closed instead of guessing.
    """

    table_rows = _ordered_table_bindings(session, binding_id)
    table_names = [item.table_name for item in table_rows]
    table_identities = set(table_names) | {item.rsplit(".", 1)[-1] for item in table_names}
    field_mappings = session.query(MetricFieldBinding).filter_by(binding_id=binding_id).all()
    physical_fields = session.query(TableField).filter_by(source_id=source_id).all()
    physical_fields = [item for item in physical_fields if item.table_name in table_identities or f"{item.schema_name}.{item.table_name}" in table_identities]
    result: dict[str, dict[str, str]] = {}
    for raw_dimension in declared:
        dimension = str(raw_dimension or "").strip()
        if not dimension:
            continue
        explicit = next((item for item in field_mappings if str(item.semantic_field or "").strip() == dimension), None)
        if explicit:
            result[dimension] = {"code": explicit.field_name, "table": explicit.table_name}
            continue
        candidates = []
        for field in physical_fields:
            physical = str(field.name or "").strip()
            semantic = str(field.cn_name or "").strip()
            score = 0
            if physical.lower() == dimension.lower():
                score = 100
            elif semantic == dimension:
                score = 95
            elif semantic.startswith(dimension):
                suffix = semantic[len(dimension):].strip(" ：:-_()（）").lower()
                if suffix in {"id", "编号", "编码", "代码", "名称", "name", "code"}:
                    score = 80
            if score:
                qualified = f"{field.schema_name}.{field.table_name}" if field.schema_name else field.table_name
                if table_names and (
                    qualified == table_names[0]
                    or field.table_name == table_names[0].rsplit(".", 1)[-1]
                ):
                    score += 5
                candidates.append((score, physical, qualified))
        if candidates:
            best = max(item[0] for item in candidates)
            winners = [item for item in candidates if item[0] == best]
            if len(winners) == 1:
                result[dimension] = {"code": winners[0][1], "table": winners[0][2]}
    return result


def _catalog(session, source_id: int, tenant_id: str) -> list[dict[str, Any]]:
    rows = (
        session.query(KnowledgeMetric, MetricSourceBinding)
        .join(MetricSourceBinding, MetricSourceBinding.metric_id == KnowledgeMetric.id)
        .filter(
            KnowledgeMetric.tenant_id == tenant_id,
            KnowledgeMetric.status == "已发布",
            MetricSourceBinding.source_id == source_id,
            MetricSourceBinding.status == "已验证",
        )
        .order_by(KnowledgeMetric.id)
        .all()
    )
    result = []
    for metric, binding in rows:
        declared_dimensions = _json_list(metric.dimensions_json)
        dimension_bindings = _dimension_bindings(session, source_id, binding.id, declared_dimensions)
        dimensions = [item for item in declared_dimensions if item in dimension_bindings]
        # A dashboard measure must be aggregatable. Row-level Boolean rules remain
        # available to the Agent, but are not misrepresented as scalar KPIs.
        kinds = _metric_kinds(binding.physical_formula, binding.time_field, dimensions)
        result.append({
            "id": metric.id,
            "name": metric.name,
            "topic": metric.topic,
            "definition": metric.definition,
            "dimensions": dimensions,
            "unit": _unit(metric),
            "precision": metric.precision,
            "kinds": kinds,
            "version": metric.version,
            "bindingVersion": binding.version,
            "semantics": metric_semantics(binding.physical_formula, binding.sql_dialect),
        })
    return result


def _default_widgets(catalog: list[dict[str, Any]]) -> list[Widget]:
    supported = [item for item in catalog if "kpi" in item["kinds"]]
    widgets = [
        Widget(
            id=f"kpi-{item['id']}", kind="kpi", metricId=item["id"], title=item["name"],
            layout=Layout(x=index * 3, y=0, w=3, h=2, minW=2, minH=2),
        )
        for index, item in enumerate(supported[:4])
    ]
    trend = next((item for item in supported if "trend" in item["kinds"]), None)
    rank = next((item for item in supported if "rank" in item["kinds"] and item["dimensions"]), None)
    if trend:
        widgets.append(Widget(id="primary-trend", kind="trend", metricId=trend["id"], title=f"{trend['name']}趋势", layout=Layout(x=0, y=2, w=9, h=7, minW=5, minH=5)))
    widgets.append(Widget(id="investigation-context", kind="context", layout=Layout(x=9, y=2, w=3, h=7, minW=3, minH=5)))
    if rank:
        widgets.append(Widget(id="dimension-rank", kind="rank", metricId=rank["id"], dimension=rank["dimensions"][0], title=f"按{rank['dimensions'][0]}对比", layout=Layout(x=0, y=9, w=4, h=6, minW=3, minH=4)))
    widgets.append(Widget(id="recent-investigations", kind="recent", layout=Layout(x=4, y=9, w=5, h=6, minW=4, minH=4)))
    widgets.append(Widget(id="metric-catalog", kind="catalog", layout=Layout(x=9, y=9, w=3, h=6, minW=3, minH=4)))
    return widgets


def _default_config(catalog: list[dict[str, Any]]) -> DashboardConfig:
    return DashboardConfig(widgets=_default_widgets(catalog))


def load_config(source_id: int, tenant_id: str, user_id: str) -> dict[str, Any]:
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        catalog = _catalog(session, source.id, tenant_id)
        row = session.get(DashboardPreference, (tenant_id, user_id, source.id))
        default_config = _default_config(catalog)
        try:
            config = DashboardConfig.model_validate(row.config_json) if row else _default_config(catalog)
        except (TypeError, ValueError):
            log.warning("Invalid stored dashboard config; serving a safe default", extra={"source_id": source.id, "user_id": user_id})
            config = _default_config(catalog)
        if row:
            config.version = row.version
        default_config.version = config.version
        return {"config": config.model_dump(), "defaultConfig": default_config.model_dump(), "catalog": catalog}


def save_config(source_id: int, tenant_id: str, user_id: str, config: DashboardConfig) -> dict[str, Any]:
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        catalog = _catalog(session, source.id, tenant_id)
        by_id = {item["id"]: item for item in catalog}
        for widget in config.widgets:
            if widget.metricId:
                metric = by_id.get(widget.metricId)
                if not metric or widget.kind not in metric["kinds"]:
                    raise HTTPException(status_code=422, detail=f"组件 {widget.id} 使用的指标当前不可用")
                if widget.kind == "rank" and widget.dimension not in metric["dimensions"]:
                    raise HTTPException(status_code=422, detail=f"组件 {widget.id} 使用的维度当前不可用")
                if widget.appearance.chartType in {"donut", "pie", "treemap"} and not metric["semantics"]["compositionAllowed"]:
                    raise HTTPException(status_code=422, detail=f"组件 {widget.id} 的指标不可加总，不能生成构成图")
        row = session.get(DashboardPreference, (tenant_id, user_id, source.id))
        current_version = row.version if row else 0
        if config.version != current_version:
            raise HTTPException(status_code=409, detail="看板已在其他窗口更新，请刷新后重试")
        next_version = current_version + 1
        payload = config.model_dump()
        payload["version"] = next_version
        if row is None:
            row = DashboardPreference(tenant_id=tenant_id, user_id=user_id, source_id=source.id, config_json=payload, version=next_version)
            session.add(row)
        else:
            row.config_json = payload
            row.version = next_version
        session.commit()
        return payload


def reset_config(source_id: int, tenant_id: str, user_id: str) -> dict[str, Any]:
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        row = session.get(DashboardPreference, (tenant_id, user_id, source.id))
        if row:
            session.delete(row)
            session.commit()
    return load_config(source_id, tenant_id, user_id)


def period_bounds(preset: str, anchor: date) -> tuple[str | None, str | None]:
    if preset == "all":
        return None, None
    days = 7 if preset == "last7" else 30
    return (anchor - timedelta(days=days - 1)).isoformat(), (anchor + timedelta(days=1)).isoformat()


def _context(session, source: DataSource, tenant_id: str) -> dict[str, Any]:
    metrics = []
    for metric, binding in (
        session.query(KnowledgeMetric, MetricSourceBinding)
        .join(MetricSourceBinding, MetricSourceBinding.metric_id == KnowledgeMetric.id)
        .filter(KnowledgeMetric.tenant_id == tenant_id, KnowledgeMetric.status == "已发布", MetricSourceBinding.source_id == source.id, MetricSourceBinding.status == "已验证")
        .all()
    ):
        table_rows = _ordered_table_bindings(session, binding.id)
        field_rows = session.query(MetricFieldBinding).filter_by(binding_id=binding.id).all()
        declared_dimensions = _json_list(metric.dimensions_json)
        dimension_bindings = _dimension_bindings(session, source.id, binding.id, declared_dimensions)
        metrics.append({
            "id": metric.id, "name": metric.name, "topic": metric.topic,
            "definition": metric.definition, "physicalFormula": binding.physical_formula,
            "filterExpression": binding.filter_expression, "physicalTimeField": binding.time_field,
            "sourceTables": [item.table_name for item in table_rows], "factTable": table_rows[0].table_name if table_rows else None,
            "dimensions": [item for item in declared_dimensions if item in dimension_bindings],
            "dimensionBindings": dimension_bindings,
            "unit": _unit(metric), "version": metric.version, "bindingVersion": binding.version,
            "semantics": metric_semantics(binding.physical_formula, binding.sql_dialect),
            "fieldMappings": [{"semanticField": item.semantic_field, "tableName": item.table_name, "fieldName": item.field_name} for item in field_rows],
        })
    tables = []
    for table in session.query(DataTable).filter_by(source_id=source.id, registered=True).all():
        fields = session.query(TableField).filter_by(source_id=source.id, schema_name=table.schema_name, table_name=table.name).order_by(TableField.ordinal).all()
        tables.append({
            "name": table.name, "schemaName": table.schema_name,
            "qualifiedName": f"{table.schema_name}.{table.name}" if table.schema_name else table.name,
            "objectType": table.object_type, "cnName": table.cn_name,
            "topic": table.topic, "rowCount": table.row_count, "dataTo": table.data_updated,
            "fields": [{"name": item.name, "semanticName": item.cn_name, "cnName": item.cn_name, "sampleValue": item.sample_value, "isPrimary": item.is_primary} for item in fields],
        })
    relations = [{
        "fromTable": item.from_table, "fromQualifiedTable": f"{item.from_schema}.{item.from_table}" if item.from_schema else item.from_table,
        "fromField": item.from_field, "toTable": item.to_table,
        "toQualifiedTable": f"{item.to_schema}.{item.to_table}" if item.to_schema else item.to_table, "toField": item.to_field,
    } for item in session.query(TableRelation).filter_by(source_id=source.id).all()]
    return {"source": {"id": source.id, "type": source.type}, "metrics": metrics, "tables": tables, "relations": relations}


def _anchor_date(source: DataSource, metric: dict[str, Any], context: dict[str, Any], user_id: str) -> tuple[date | None, str]:
    table_name = str(metric.get("factTable") or next(iter(metric.get("sourceTables") or []), ""))
    table = next((item for item in context["tables"] if table_name in {item["name"], item["qualifiedName"]}), None)
    time_field = metric.get("physicalTimeField")
    if not table or not time_field:
        return None, "unavailable"
    adapter = get_adapter(source.type)
    fq = adapter.qualify_table(PhysicalTableRef(schema=table.get("schemaName") or "", name=table["name"]))
    sql = f"SELECT MAX({adapter.quote_identifier(time_field)}) AS latest FROM {fq}"
    try:
        result = execute_readonly(sql, action="刷新看板时间范围", user_name=user_id, source=source)
    except Exception:  # connector failures are rendered as a widget state, never a page-level 500
        log.exception("Dashboard anchor query failed", extra={"source_id": source.id, "metric_id": metric.get("id")})
        return None, "error"
    if not result.ok:
        return None, "error"
    if not result.rows or result.rows[0][0] is None:
        return None, "empty"
    value = result.rows[0][0]
    if isinstance(value, datetime):
        return value.date(), "ready"
    if isinstance(value, date):
        return value, "ready"
    try:
        return date.fromisoformat(str(value)[:10]), "ready"
    except ValueError:
        return None, "empty"


def query_widget(request: WidgetQuery, tenant_id: str, user_id: str) -> dict[str, Any]:
    with PlatformSession() as session:
        source = ensure_source_access(session, request.sourceId, tenant_id)
        context = _context(session, source, tenant_id)
    metric = next((item for item in context["metrics"] if item["id"] == request.metricId), None)
    if not metric:
        return {"state": "unavailable", "message": "指标未发布或当前数据源尚未验证物理绑定"}
    allowed_kinds = _metric_kinds(metric.get("physicalFormula", ""), metric.get("physicalTimeField", ""), metric.get("dimensions") or [])
    if request.kind not in allowed_kinds:
        return {"state": "unavailable", "message": "该指标不支持当前看板展示方式"}
    if request.kind == "rank" and request.dimension not in (metric.get("dimensions") or []):
        return {"state": "unavailable", "message": "该维度未在指标口径中登记"}
    anchor, anchor_state = _anchor_date(source, metric, context, user_id)
    if anchor is None:
        if anchor_state == "error":
            return {"state": "error", "message": "查询暂时失败，请稍后刷新该组件"}
        if anchor_state == "unavailable":
            return {"state": "unavailable", "message": "该指标缺少看板所需的时间绑定"}
        return {"state": "empty", "message": "当前指标尚未识别到可用的数据日期"}
    start, end = period_bounds(request.period, anchor)
    periods = [] if start is None else [{"label": "统计周期", "startInclusive": start, "endExclusive": end}]
    group_by = ["日期"] if request.kind == "trend" else ([request.dimension] if request.kind == "rank" else [])
    task = {
        "analysisType": "trend" if request.kind == "trend" else ("rank" if request.kind == "rank" else "summary"),
        "groupBy": group_by, "topN": 8 if request.kind == "rank" else None,
        "timeConstraint": {"timeGrain": "day", "periods": periods},
        "semanticIntent": {
            "metrics": [{"id": metric["id"], "semantic": metric["name"]}],
            "dimensions": [
                {"semantic": value, **({"code": metric["dimensionBindings"][value]["code"]} if value in (metric.get("dimensionBindings") or {}) else {})}
                for value in group_by
            ],
        },
    }
    try:
        bound = bind_query_requirements(task, context)
        compiled = compile_query(context, bound, dialect=source.type)
    except Exception:
        log.exception("Dashboard governed query compilation failed", extra={"source_id": source.id, "metric_id": metric.get("id")})
        return {"state": "error", "message": "组件查询暂时无法生成，请联系管理员检查指标绑定"}
    if not compiled:
        return {"state": "unavailable", "message": "该指标缺少看板所需的时间或维度绑定"}
    sql, contract = compiled
    try:
        execution = execute_readonly(sql, action=f"刷新看板组件 {metric['name']}", user_name=user_id, source=source)
    except Exception:
        log.exception("Dashboard widget query failed", extra={"source_id": source.id, "metric_id": metric.get("id")})
        return {"state": "error", "message": "查询暂时失败，请稍后刷新该组件"}
    if not execution.ok:
        return {"state": "error", "message": "查询暂时失败，请稍后刷新该组件"}
    if not execution.rows or all(all(value is None for value in row) for row in execution.rows):
        return {"state": "empty", "message": "当前筛选范围没有匹配记录", "columns": execution.columns, "rows": []}
    return {
        "state": "ready", "metric": {"id": metric["id"], "name": metric["name"], "unit": metric["unit"]},
        "columns": execution.columns, "rows": execution.rows,
        "range": {"preset": request.period, "start": start, "endExclusive": end, "anchor": anchor.isoformat(), "basis": "source_latest"},
        "contract": {"metricVersion": metric.get("version"), "bindingVersion": metric.get("bindingVersion"), "binding": contract.get("factTable"), "groupBy": contract.get("groupBy")},
    }
