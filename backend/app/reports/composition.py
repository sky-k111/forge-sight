"""Governed personal report compositions and deterministic previews."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from app.agent.ids import new_id
from app.agent.query_compiler import compile_query
from app.agent.query_requirements import bind_query_requirements
from app.dashboard.service import _context, _metric_kinds
from app.db.models import PersonalReportTemplate
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly
from app.reports.composition_contracts import ReportComposition
from app.knowledge.metric_semantics import metric_semantics
from app.reports.comparison import align_comparison, period_window
from app.tenancy.service import ensure_source_access


def _public_metric(metric: dict[str, Any]) -> dict[str, Any]:
    dimensions = list(metric.get("dimensions") or [])
    return {
        "id": metric["id"], "name": metric["name"], "topic": metric.get("topic") or "其他",
        "definition": metric.get("definition") or "", "dimensions": dimensions,
        "unit": metric.get("unit") or "", "version": metric.get("version"),
        "bindingVersion": metric.get("bindingVersion"),
        "semantics": metric.get("semantics") or metric_semantics(metric.get("physicalFormula", "")),
        "kinds": _metric_kinds(metric.get("physicalFormula", ""), metric.get("physicalTimeField", ""), dimensions),
        "sourceTables": list(metric.get("sourceTables") or []),
    }


def _block(metric: dict, kind: str, index: int, *, dimension: str | None = None) -> dict:
    chart = "number" if kind == "kpi" else ("line" if kind == "trend" else "bar")
    title = metric["name"] if kind == "kpi" else (
        f"{metric['name']}趋势" if kind == "trend" else f"按{dimension}分析{metric['name']}"
    )
    return {
        "id": f"block-{index}-{metric['id'].lower()}", "kind": kind, "title": title,
        "metricId": metric["id"], "dimension": dimension, "grain": "week",
        "chart": chart, "width": "half" if kind == "kpi" else "full",
        "height": "compact" if kind == "kpi" else "standard", "accent": "default",
        "compare": kind == "kpi", "topN": 12,
    }


def _starter(name: str, description: str, metrics: list[dict]) -> dict:
    blocks = []
    usable = [item for item in metrics if item["kinds"]]
    for metric in usable[:3]:
        blocks.append(_block(metric, "kpi", len(blocks) + 1))
    trend = next((item for item in usable if "trend" in item["kinds"]), None)
    if trend:
        blocks.append(_block(trend, "trend", len(blocks) + 1))
    ranked = next((item for item in usable if "rank" in item["kinds"] and item["dimensions"]), None)
    if ranked:
        blocks.append(_block(ranked, "breakdown", len(blocks) + 1, dimension=ranked["dimensions"][0]))
    return {"id": f"starter-{name}", "name": f"{name}起始模板", "description": description, "blocks": blocks}


def load_catalog(source_id: int, tenant_id: str) -> dict[str, Any]:
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        context = _context(session, source, tenant_id)
    metrics = [_public_metric(item) for item in context["metrics"]]
    by_table: dict[str, list[str]] = {}
    for metric in metrics:
        for table in metric["sourceTables"]:
            by_table.setdefault(table, []).append(metric["id"])
            by_table.setdefault(table.rsplit(".", 1)[-1], []).append(metric["id"])
    tables = [
        {**table, "metricIds": list(dict.fromkeys(by_table.get(table["qualifiedName"], []) + by_table.get(table["name"], [])))}
        for table in context["tables"]
    ]
    starters = [{"id": "starter-blank", "name": "空白周报", "description": "从指标关系图逐项添加内容。", "blocks": []}]
    groups = [
        ("综合", "生产、质量、设备与库存可以自由混搭", metrics),
        ("生产", "产量、产线和工序趋势", [item for item in metrics if item["topic"] == "生产" or "产" in item["name"]]),
        ("质量", "良率、不良与缺陷分布", [item for item in metrics if item["topic"] == "质量" or any(word in item["name"] for word in ("良率", "不良", "缺陷"))]),
        ("设备", "停机、故障与设备影响", [item for item in metrics if item["topic"] == "设备" or any(word in item["name"] for word in ("设备", "停机", "故障"))]),
        ("库存", "库存水平、缺口与风险对象", [item for item in metrics if item["topic"] == "库存" or "库存" in item["name"]]),
    ]
    starters.extend(_starter(name, description, items) for name, description, items in groups if items)
    anchors = []
    for table in tables:
        try:
            if table.get("dataTo"):
                anchors.append(date.fromisoformat(str(table["dataTo"])[:10]))
        except ValueError:
            continue
    suggested_range = None
    if anchors:
        end = max(anchors)
        suggested_range = {"start": (end - timedelta(days=6)).isoformat(), "end": end.isoformat()}
    return {
        "source": {"id": source.id, "name": source.name, "type": source.type},
        "metrics": metrics, "tables": tables, "relations": context["relations"],
        "starters": starters, "suggestedRange": suggested_range,
    }


def validate_composition(composition: ReportComposition, source_id: int, tenant_id: str) -> tuple[dict, dict[str, dict]]:
    catalog = load_catalog(source_id, tenant_id)
    by_id = {item["id"]: item for item in catalog["metrics"]}
    _validate_against_metrics(composition, by_id)
    return catalog, by_id


def validate_against_context(composition: ReportComposition, context: dict) -> dict[str, dict]:
    by_id = {item["id"]: _public_metric(item) for item in context["metrics"]}
    _validate_against_metrics(composition, by_id)
    return by_id


def _validate_against_metrics(composition: ReportComposition, by_id: dict[str, dict]) -> None:
    for block in composition.blocks:
        metric = by_id.get(block.metricId or "")
        required_kind = {"kpi": "kpi", "trend": "trend", "breakdown": "rank"}[block.kind]
        if not metric or required_kind not in metric["kinds"]:
            raise HTTPException(status_code=422, detail=f"{block.title}引用的指标当前不可用于这种展示")
        if block.kind == "breakdown" and block.dimension not in metric["dimensions"]:
            raise HTTPException(status_code=422, detail=f"{block.title}引用的维度未在指标口径中登记")
        semantics = metric.get("semantics") or metric_semantics(metric.get("physicalFormula", ""))
        if block.chart in {"donut", "pareto"} and not semantics["compositionAllowed"]:
            raise HTTPException(status_code=422, detail=f"{block.title}不是可加总指标，不能展示占比或累计贡献")
        if block.chart == "calendar" and block.grain != "day":
            raise HTTPException(status_code=422, detail="日历热力图必须使用按日粒度")


def list_personal(source_id: int, tenant_id: str, user_id: str) -> list[dict]:
    with PlatformSession() as session:
        ensure_source_access(session, source_id, tenant_id)
        rows = session.query(PersonalReportTemplate).filter_by(
            tenant_id=tenant_id, user_id=user_id, source_id=source_id,
        ).order_by(PersonalReportTemplate.updated_at.desc()).all()
        return [_template_dict(row) for row in rows]


def save_personal(source_id: int, tenant_id: str, user_id: str, composition: ReportComposition, template_id: str | None = None, version: int = 0) -> dict:
    validate_composition(composition, source_id, tenant_id)
    with PlatformSession() as session:
        ensure_source_access(session, source_id, tenant_id)
        row = session.get(PersonalReportTemplate, template_id) if template_id else None
        if row and (row.tenant_id, row.user_id, row.source_id) != (tenant_id, user_id, source_id):
            raise HTTPException(status_code=404, detail="周报模板不存在")
        if row and row.version != version:
            raise HTTPException(status_code=409, detail="模板已在其他窗口更新，请刷新后重试")
        if row is None:
            if template_id:
                raise HTTPException(status_code=404, detail="周报模板不存在")
            row = PersonalReportTemplate(id=new_id("rptpl"), tenant_id=tenant_id, user_id=user_id, source_id=source_id, version=1)
            session.add(row)
        else:
            row.version += 1
            row.updated_at = datetime.now()
        row.config_json = composition.model_dump(mode="json")
        session.commit()
        return _template_dict(row)


def delete_personal(template_id: str, source_id: int, tenant_id: str, user_id: str) -> None:
    with PlatformSession() as session:
        ensure_source_access(session, source_id, tenant_id)
        row = session.get(PersonalReportTemplate, template_id)
        if not row or (row.tenant_id, row.user_id, row.source_id) != (tenant_id, user_id, source_id):
            raise HTTPException(status_code=404, detail="周报模板不存在")
        session.delete(row)
        session.commit()


def _template_dict(row: PersonalReportTemplate) -> dict:
    return {"id": row.id, "version": row.version, "sourceId": row.source_id, "composition": row.config_json, "updatedAt": row.updated_at.isoformat()}


def _query_block(source, context: dict, metric: dict, block, start: str, end: str, user_id: str) -> dict:
    group_by = []
    if block.kind == "trend":
        group_by = [{"day": "日期", "week": "周", "month": "月份"}[block.grain]]
    elif block.kind == "breakdown":
        group_by = [block.dimension]
    task = {
        "analysisType": "trend" if block.kind == "trend" else ("rank" if block.kind == "breakdown" else "summary"),
        "groupBy": group_by, "topN": block.topN if block.kind == "breakdown" else None,
        "timeConstraint": {"timeGrain": block.grain, "periods": [{"label": "统计周期", "startInclusive": start, "endExclusive": end}]},
        "semanticIntent": {
            "metrics": [{"id": metric["id"], "semantic": metric["name"]}],
            "dimensions": [{"semantic": value, **({"code": metric["dimensionBindings"][value]["code"]} if value in metric.get("dimensionBindings", {}) else {})} for value in group_by],
        },
    }
    bound = bind_query_requirements(task, context)
    compiled = compile_query(context, bound, dialect=source.type)
    if not compiled:
        return {"state": "unavailable", "message": "指标缺少生成该内容所需的时间或维度绑定"}
    sql, contract = compiled
    result = execute_readonly(sql, action=f"预览周报内容 {block.title}", user_name=user_id, source=source)
    if not result.ok:
        return {"state": "error", "message": "查询暂时失败，请稍后重试"}
    if not result.rows or all(all(value is None for value in row) for row in result.rows):
        return {"state": "empty", "message": "当前统计区间没有匹配记录", "columns": result.columns, "rows": []}
    if getattr(block, "chart", None) in {"donut", "pareto"}:
        values = [row[-1] for row in result.rows]
        if any(value is None or float(value) < 0 for value in values) or sum(float(value) for value in values) <= 0:
            return {"state": "unavailable", "message": "构成图需要非负且非零数据，请改用明细表或条形图"}
    return {"state": "ready", "columns": result.columns, "rows": result.rows, "contract": {"metricVersion": metric.get("version"), "bindingVersion": metric.get("bindingVersion"), "groupBy": contract.get("groupBy")}}


def preview(composition: ReportComposition, source_id: int, tenant_id: str, user_id: str, period_start: datetime, period_end: datetime) -> dict:
    validate_composition(composition, source_id, tenant_id)
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        context = _context(session, source, tenant_id)
    by_id = {item["id"]: item for item in context["metrics"]}
    start = period_start.date().isoformat()
    end_date = period_end.date() + (timedelta(days=1) if period_end.time() != datetime.min.time() else timedelta())
    end = end_date.isoformat()
    items = []
    for block in composition.blocks:
        metric = by_id[block.metricId]
        try:
            result = _query_block(source, context, metric, block, start, end, user_id)
            if block.kind in {"kpi", "trend"} and block.compare and result.get("state") == "ready":
                _, _, previous_start = period_window(period_start, period_end)
                previous = _query_block(source, context, metric, block, previous_start.isoformat(), start, user_id)
                result["previousRows"] = previous.get("rows") if previous.get("state") == "ready" else None
                result["comparisonState"] = previous.get("state", "unavailable")
                if block.kind == "trend":
                    result["comparison"] = align_comparison(result["rows"], result["previousRows"], block.grain, period_start, period_end)
                    if previous.get("state") != "ready":
                        result["comparison"]["state"] = previous.get("state", "unavailable")
        except Exception:
            result = {"state": "error", "message": "内容查询失败，请检查指标绑定后重试"}
        items.append({"block": block.model_dump(mode="json"), "metric": _public_metric(metric), **result})
    return {
        "range": {
            "start": start,
            "end": (end_date - timedelta(days=1)).isoformat(),
            "endExclusive": end,
        },
        "items": items,
    }


def previous_period_result(
    source_id: int, tenant_id: str, user_id: str, block: dict, period_start: datetime, period_end: datetime,
) -> dict:
    """Deterministically fetch the shifted equal-length window for a trend+compare block.

    Reuses the same governed read-only compiler as preview so the frozen report's
    comparison series shares the exact metric口径 with the current period.
    """
    if not block or block.get("kind") != "trend" or not block.get("compare") or not block.get("metricId"):
        return {"state": "unavailable"}
    duration = period_end - period_start
    if duration.total_seconds() <= 0:
        return {"state": "unavailable"}
    with PlatformSession() as session:
        source = ensure_source_access(session, source_id, tenant_id)
        context = _context(session, source, tenant_id)
    metric = next((item for item in context["metrics"] if item["id"] == block["metricId"]), None)
    if not metric:
        return {"state": "unavailable"}
    proxy = SimpleNamespace(
        kind="trend",
        grain=block.get("grain", "day"),
        dimension=block.get("dimension"),
        topN=block.get("topN", 12),
        title=block.get("title", "趋势"),
    )
    _, _, shifted_start = period_window(period_start, period_end)
    previous_start = shifted_start.isoformat()
    previous_end = period_start.date().isoformat()
    result = _query_block(source, context, metric, proxy, previous_start, previous_end, user_id)
    return result


def previous_period_rows(source_id, tenant_id, user_id, block, period_start, period_end):
    result = previous_period_result(source_id, tenant_id, user_id, block, period_start, period_end)
    return result.get("rows") if result.get("state") == "ready" else None
