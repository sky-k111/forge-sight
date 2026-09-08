"""分析成果的图表/KPI 语义契约（Interpretation Blueprint）。

为什么存在：
    历史上图表选轴、单位、维度由前端 chartBuilder 通过“列名 + 问题文本”二次猜测，
    与后端的指标/意图解析脱节，导致“占比题把产量画上去”“比率图不带 %”“排名图用
    产品ID 而不是名称”等横纵轴问题。本模块把“用户问的是什么、应该怎么展示”的判定
    收敛到后端一处：基于已解析的 task 意图、metric_resolution 口径和 execution 结果列，
    一次性产出 chart + kpis 契约。前端优先消费该契约，仅在缺失时才回退到本地启发式。

    该模块为纯函数，只依赖输入字典，可独立单测，不碰 DB / LLM。
"""

from __future__ import annotations

import re
from typing import Any

INTERPRETATION_KIND = "interpretation"
INTERPRETATION_VERSION = 1

_RATE_KEY_RE = re.compile(r"(?:率|占比|比例|份额|构成|百分比|percent|rate|ratio|share)|\b%", re.IGNORECASE)
_DIMENSION_NAME_RE = re.compile(r"(?:名称|名字|name|label|描述|desc)", re.IGNORECASE)
_DIMENSION_CODE_RE = re.compile(r"(?:代码|编码|code|id|编号|序号)$", re.IGNORECASE)
_SHARE_QUESTION_RE = re.compile(r"(?:占比|构成|比例|份额|分布为|分布情况|占多少)")
_TREND_QUESTION_RE = re.compile(r"(?:趋势|变化|走势|折线|逐月|每月|随时间)")
_RANK_QUESTION_RE = re.compile(r"(?:最高|最低|排名|排行|Top|top|前[0-9一二三四五六七八九十]+)")
_COMPARE_QUESTION_RE = re.compile(r"(?:对比|比较|与.*相比|同时|差异|和.*的?关系)")
_HEATMAP_INDEXED = ("heatmap", "matrix", "热力图", "矩阵图")


def _finite_number(value: Any) -> bool:
    try:
        return float(str(value or "")) not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def _numeric_columns(columns: list[str], rows: list[list[Any]]) -> list[str]:
    if not columns or not rows:
        return []
    result: list[str] = []
    for col in columns:
        idx = columns.index(col)
        if any(idx < len(row) and row[idx] not in (None, "") and _finite_number(row[idx]) for row in rows):
            result.append(col)
    return result


def _is_rate_key(key: str) -> bool:
    return bool(_RATE_KEY_RE.search(str(key)))


def _pick_value_column(numeric: list[str], metric_name: str, question: str, share_requested: bool) -> str | None:
    """优先选择与“用户要的指标”语义一致的数值列。

    规则：
      1. 若用户要的是占比/构成（share_requested），优先用名称匹配占比/率的列；
      2. 否则优先用与 metric_name 同名/同义的列（去掉 (…) 后缀）；
      3. 否则取第一个数值列。
    """
    if not numeric:
        return None

    def normalize(name: str) -> str:
        return re.sub(r"[()（）%·\-\s]", "", str(name)).lower()

    metric_norm = normalize(metric_name)

    if share_requested:
        share_cols = [c for c in numeric if _is_rate_key(c)]
        if share_cols:
            # 占比/构成题：占比列优先；若有多个，挑和指标名一致的那个。
            for c in share_cols:
                if metric_norm and metric_norm in normalize(c):
                    return c
            return share_cols[0]

    if metric_norm:
        for c in numeric:
            if metric_norm in normalize(c) or normalize(c) in metric_norm:
                return c
    return numeric[0]


def _pick_dimension_column(columns: list[str], numeric: list[str], group_by: list[str]) -> str | None:
    """挑选可读维度的 x 轴列。

    规则：
      1. 优先选和任务 groupBy 关键词相匹配的列；
      2. 排名/对照场景下，优先“名称”类列，其次“代码/id”类列；
      3. 否则取第一个非数值列。
    """
    categorical = [c for c in columns if c not in numeric]
    if not categorical:
        return columns[0] if columns else None

    if group_by:
        group_hits = [
            c for c in categorical
            if any(term in str(group_by) and term in c for term in group_by)
        ]
        if group_hits:
            name_hits = [c for c in group_hits if _DIMENSION_NAME_RE.search(c)]
            return name_hits[0] if name_hits else group_hits[0]

    # 名称优先于代码/id
    name_cols = [c for c in categorical if _DIMENSION_NAME_RE.search(c)]
    if name_cols:
        return name_cols[0]
    code_cols = [c for c in categorical if _DIMENSION_CODE_RE.search(c)]
    if code_cols and len(categorical) > 1:
        return next((c for c in categorical if c not in code_cols), categorical[0])
    return categorical[0]


def _axis_step(raw: float) -> float:
    if not raw or raw <= 0:
        return 1.0
    exp = int(__import__("math").floor(__import__("math").log10(raw)))
    mag = 10 ** exp
    frac = raw / mag
    nice = 1 if frac <= 1 else 2 if frac <= 2 else 5 if frac <= 5 else 10
    return nice * mag


def _auto_y_domain(data: list[dict], value_key: str, unit: str) -> dict[str, Any]:
    values = [float(row[value_key]) for row in data if _finite_number(row[value_key])]
    if not values:
        return {}
    minimum, maximum = min(values), max(values)
    rate_scale = unit == "%" and 0 <= minimum <= 100
    rng = maximum - minimum
    padding = max(rng * 0.18, rate_scale and 0.1 or 0) if rng > 0 else max(rate_scale and 0.1 or 1, abs(maximum) * 0.05)
    raw_min = max(0, minimum - padding) if rate_scale else minimum - padding
    raw_max = min(100, maximum + padding) if rate_scale else maximum + padding
    step = _axis_step(max(raw_max - raw_min, 1e-9) / 4)
    dom_min = __import__("math").floor(raw_min / step) * step
    dom_max = __import__("math").ceil(raw_max / step) * step
    if rate_scale:
        dom_min = max(0, dom_min)
        dom_max = min(100, dom_max)
    if dom_min == dom_max:
        dom_max = dom_min + step
    precision = 3 if step < 0.01 else 2 if step < 0.1 else 1 if step < 1 else 0
    return {
        "yDomain": [round(dom_min, precision + 1), round(dom_max, precision + 1)],
        "unit": unit,
        "yPrecision": precision,
    }


def decide_chart(
    *,
    analysis_type: str,
    requested_chart: str | None,
    question: str,
    group_by: list[str],
    metric_name: str,
    columns: list[str],
    rows: list[list[Any]],
    row_count: int,
    truncated: bool,
    metric_key: str | None = None,
) -> dict[str, Any]:
    """基于已解析意图 + 指标口径 + 结果列，决定图表类型与轴。"""
    numeric = _numeric_columns(columns, rows)
    share = requested_chart == "donut" or (
        not requested_chart and bool(_SHARE_QUESTION_RE.search(question))
    )
    trend = requested_chart == "line" or (
        not requested_chart and (
            analysis_type == "trend" or bool(_TREND_QUESTION_RE.search(question))
        )
    )
    rank = requested_chart == "rank" or (
        not requested_chart and (analysis_type == "ranking" or bool(_RANK_QUESTION_RE.search(question)))
    )
    heatmap = requested_chart in ("heatmap", "matrix") or any(k in question for k in _HEATMAP_INDEXED)
    corr = requested_chart == "scatter" or analysis_type == "correlation"
    compare = not (share or trend or rank or heatmap or corr) and (
        analysis_type == "comparison" or bool(_COMPARE_QUESTION_RE.search(question))
    )

    x_key = _pick_dimension_column(columns, numeric, group_by)
    value_key = metric_key or _pick_value_column(numeric, metric_name, question, share)
    unit = "%" if value_key and _is_rate_key(value_key) else ""
    display_limit = 36 if trend else 12
    numeric_set = set(numeric)

    def _to_row(entry: list[Any]) -> dict[str, Any]:
        d = dict(zip(columns, entry))
        for key in numeric_set:
            try:
                d[key] = float(d[key])
            except (TypeError, ValueError):
                pass
        return d

    chart_rows = [_to_row(row) for row in rows[:display_limit]]

    if corr:
        first, second = numeric[0], numeric[1] if len(numeric) > 1 else None
        return {
            "type": "scatter",
            "kicker": "相关性分析 · 实时生成",
            "data": chart_rows,
            "xNumericKey": first,
            "yNumericKey": second,
            "xName": first,
            "yName": second,
            "legend": [{"label": f"{first} × {second}"}],
            "note": _note(rows, display_limit),
        }

    if heatmap and len(categorical_cols(columns, numeric)) >= 2:
        cats = categorical_cols(columns, numeric)
        row_key, col_key = cats[0], cats[1]
        vkey = value_key or (numeric[0] if numeric else None)
        return {
            "type": "heatmap",
            "kicker": "二维交叉分析 · 实时生成",
            "rows": _distinct(chart_rows, row_key),
            "columns": _distinct(chart_rows, col_key),
            "cells": _heatmap_cells(chart_rows, row_key, col_key, vkey),
            "rowName": row_key,
            "columnName": col_key,
            "valueName": vkey,
            "valueKey": vkey,
            "legend": [],
            "note": _note(rows, display_limit),
        }

    if share:
        vkey = value_key or (numeric[0] if numeric else None)
        donut_data = [{"name": str(row.get(x_key, "未命名")), "value": float(row[vkey])} for row in chart_rows if vkey]
        total = sum(item["value"] for item in donut_data)
        return {
            "type": "donut",
            "kicker": "结构占比 · 实时生成",
            "data": donut_data,
            "centerTitle": "合计",
            "centerValue": _fmt_total(total),
            "legend": [{"label": item["name"]} for item in donut_data],
            "unit": unit,
            "note": _note(rows, display_limit),
        }

    if compare and len(numeric) >= 2 and not (value_key and _is_rate_key(value_key)):
        # 多指标对照（柱线组合）：率类作为右侧折线，量类作为左侧柱
        rate_cols = [c for c in numeric if _is_rate_key(c)]
        abs_cols = [c for c in numeric if not _is_rate_key(c)]
        bars = [{"key": c, "name": c} for c in (abs_cols or numeric[:1])[:2]]
        lines = [{"key": c, "name": c} for c in (rate_cols or numeric)[:2]]
        return {
            "type": "combo",
            "kicker": "多指标对照 · 实时生成",
            "data": chart_rows,
            "xKey": x_key,
            "bars": bars,
            "lines": lines,
            "leftUnit": "",
            "rightUnit": "%" if lines and all(_is_rate_key(item["key"]) for item in lines) else "",
            "valueKey": (bars[0]["key"] if bars else value_key),
            "note": _note(rows, display_limit),
        }

    if trend:
        series_keys = numeric[:4]
        y = _auto_y_domain(chart_rows, value_key, unit) if value_key else {}
        return {
            "type": "line",
            "kicker": "趋势分析 · 实时生成",
            "data": chart_rows,
            "xKey": x_key,
            "series": [{"key": c, "name": c} for c in series_keys],
            "valueKey": value_key,
            **y,
            "note": _note(rows, display_limit),
        }

    # 默认柱状
    bars = [{"key": c, "name": c} for c in numeric[:3]]
    y = _auto_y_domain(chart_rows, value_key, unit) if value_key else {}
    return {
        "type": "bar",
        "kicker": "查询结果 · 实时生成",
        "data": chart_rows,
        "xKey": x_key,
        "bars": bars,
        "valueKey": value_key,
        "unit": unit,
        "note": _note(rows, display_limit),
    }


def categorical_cols(columns: list[str], numeric: list[str]) -> list[str]:
    return [c for c in columns if c not in numeric]


def _distinct(rows: list[dict], key: str) -> list[str]:
    seen: list[str] = []
    for row in rows:
        val = str(row.get(key, "未命名"))
        if val not in seen:
            seen.append(val)
    return seen[:12]


def _heatmap_cells(rows: list[dict], row_key: str, col_key: str, value_key: str) -> list[dict]:
    cells: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        r, c = str(row.get(row_key, "未命名")), str(row.get(col_key, "未命名"))
        if (r, c) in seen:
            continue
        seen.add((r, c))
        try:
            val = float(row.get(value_key, 0))
        except (TypeError, ValueError):
            val = 0.0
        cells.append({"row": r, "column": c, "value": val})
    return cells



def _note(rows: list[list[Any]], display_limit: int) -> str:
    total = len(rows)
    if total > display_limit:
        return f"图表展示前 {display_limit} 项，共返回 {total} 条记录；完整结果可导出。"
    return f"图表展示全部 {total} 条返回记录。"


def _fmt_total(value: float) -> str:
    try:
        return f"{value:,.2f}" if abs(value) < 1000 else f"{value:,.0f}"
    except (TypeError, ValueError):
        return str(value)


def build_interpretation(
    task: dict[str, Any],
    metric_resolution: dict[str, Any] | None,
    execution: dict[str, Any],
) -> dict[str, Any]:
    """产出图表/KPI 契约。execution 为 result artifact content（{columns,rows,row_count,truncated}）。"""
    columns = list(execution.get("columns") or [])
    rows = list(execution.get("rows") or [])
    row_count = int(execution.get("row_count") or len(rows) or 0)
    truncated = bool(execution.get("truncated") or False)
    metric = metric_resolution or {}
    task = task or {}
    chart = decide_chart(
        analysis_type=str(task.get("analysisType") or "query"),
        requested_chart=task.get("requestedChart") or None,
        question=str(task.get("normalizedQuestion") or task.get("originalQuestion") or ""),
        group_by=list(task.get("groupBy") or []),
        metric_name=str(metric.get("name") or ""),
        columns=columns,
        rows=rows,
        row_count=row_count,
        truncated=truncated,
        metric_key=(metric.get("chartValueKey") or None),
    )
    return {
        "kind": INTERPRETATION_KIND,
        "version": INTERPRETATION_VERSION,
        "chart": chart,
        "kpis": _build_kpis(columns, rows, metric, chart),
    }


def _build_kpis(columns: list[str], rows: list[list[Any]], metric: dict[str, Any], chart: dict[str, Any]) -> list[dict[str, Any]]:
    if not columns or not rows:
        return []
    numeric = _numeric_columns(columns, rows)
    if not numeric:
        return []
    # 单行：KPI 卡片展示前 3 个数值列；多行：只展示总合/首列聚合
    def row_dict(row: list[Any]) -> dict[str, Any]:
        return dict(zip(columns, row))
    if len(rows) == 1:
        d = row_dict(rows[0])
        cat = [c for c in columns if c not in numeric]
        dim = cat[0] if cat else None
        caption = str(d.get(dim, "当前查询结果")) if dim else "当前查询结果"
        return [
            {"label": c, "value": _fmt_kpi(d.get(c), c), "caption": caption}
            for c in numeric[:3]
        ]
    # 多行：对非率数值列求和展示总合
    total_row = row_dict(rows[0])
    agg_cols = [c for c in numeric if not _is_rate_key(c)][:1]
    if agg_cols:
        total = sum(float(row[columns.index(agg_cols[0])]) for row in rows if _finite_number(row[columns.index(agg_cols[0])]))
        caption = "全量汇总"
        return [{"label": agg_cols[0], "value": _fmt_kpi(total, agg_cols[0]), "caption": caption}]
    return []


def _fmt_kpi(value: Any, key: str) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value or "—")
    formatted = f"{num:,.2f}"
    return f"{formatted}%" if _is_rate_key(key) else formatted




