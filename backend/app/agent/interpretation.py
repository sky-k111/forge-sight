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

import json
import re
from typing import Any

INTERPRETATION_KIND = "interpretation"
INTERPRETATION_VERSION = 1

_RATE_KEY_RE = re.compile(r"(?:率|占比|比例|份额|构成|百分比|percent|rate|ratio|share)|\b%", re.IGNORECASE)
_DIMENSION_NAME_RE = re.compile(r"(?:名称|名字|name|label|描述|desc)", re.IGNORECASE)
# 中文指标名 -> 常见英文/拼音列名后缀（用于把“合格数”映射到 qualified_qty，而非第一个数值列）。
_METRIC_KEYWORD_SYNONYMS = {
    "合格": ["qualified", "pass", "ok", "good"],
    "良率": ["yield", "pass_rate", "qualified_rate", "good_rate"],
    "不良": ["defect", "reject", "ng", "bad", "fail"],
    "产量": ["output", "qty", "quantity", "amount", "volume", "count", "out"],
    "数量": ["qty", "quantity", "amount", "volume", "count"],
    "库存": ["stock", "inventory", "inv"],
    "停机": ["downtime", "stop", "down"],
    "能耗": ["energy", "consumption", "power"],
}
_DIMENSION_CODE_RE = re.compile(r"(?:代码|编码|code|id|编号|序号|no|num|number)$", re.IGNORECASE)
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


_TIME_DIM_RE = re.compile(
    r"^(?:\u7edf\u8ba1|stat_)?(?:\u5e74\u4efd|\u5e74\u5ea6|\u6708\u4efd|\u6708\u5ea6|\u6708|\u5468\u6570|\u5468\u6b21|\u5468|\u5b63\u5ea6|\u5b63|\u65e5\u671f|\u65f6\u95f4|\u5e74|\u6708)$"
    r"|^(?:stat_)?(?:year|month|week|quarter|day|date)(?:_|$)"
    r"|^(?:stat_)?(?:year|month|week|quarter)_?(?:of_year|num|number|no|label|start|end)$",
    re.IGNORECASE,
)
_YEAR_ONLY_RE = re.compile(r"^(?:\u7edf\u8ba1|stat_)?\u5e74(?:\u4efd|\u5ea6)?$|^(?:stat_)?year$", re.IGNORECASE)


def _is_dimension_label(col: str, numeric: list[str]) -> bool:
    """判断列是否适合作为维度/x 轴（类别、时间粒度、名称或编码），即使它是数值。

    修复：'周数'、'统计年份'、'月份' 这类时间粒度列会被 SQL 输出为数值，
    但它们是维度而不是指标值；若仅按“是否数值”二分，会把它们误判为 y 轴候选，
    导致“6 月份各周良率”的横轴退化为常数年份。
    """
    if col not in numeric:
        return True
    return bool(_TIME_DIM_RE.match(str(col)) or _DIMENSION_CODE_RE.search(str(col)))


def _value_columns(columns: list[str], rows: list[list[Any]]) -> list[str]:
    """真正可用于 y 轴/指标的数值列：数值字段中剔除时间粒度、名称、编码类维度。"""
    numeric = _numeric_columns(columns, rows)
    return [c for c in numeric if not _is_dimension_label(c, numeric)]


def _prefer_dimension(candidates: list[str], group_by: list[str]) -> str:
    """从候选维度列中挑出更有意义的那一个：优先非“年份”（周/月/季粒度的信息量更大）。"""
    non_year = [c for c in candidates if not _YEAR_ONLY_RE.match(str(c))]
    pool = non_year or candidates
    if group_by:
        hits = [c for c in pool if any(term in str(group_by) and term in c for term in group_by)]
        if hits:
            return hits[0]
    return pool[0]


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
        # 中文指标名与英文列名不同形（如“合格数” vs qualified_qty），按同义词映射回退。
        for keyword, latin_tokens in _METRIC_KEYWORD_SYNONYMS.items():
            if keyword in metric_norm:
                for c in numeric:
                    cnorm = normalize(c)
                    if any(token in cnorm for token in latin_tokens):
                        return c
    return numeric[0]


def _pick_dimension_column(columns: list[str], numeric: list[str], group_by: list[str], rows: list[list[Any]] | None = None) -> str | None:
    """挑选可读维度的 x 轴列。

    规则：
      1. 优先选和任务 groupBy 关键词相匹配的列；
      2. 其次优先“时间粒度”维度列（周/月/季/年），且在同粒度里避开常数“年份”列；
      3. 排名/对照场景下，优先“名称”类列，其次“代码/id”类列；
      4. 否则取第一个维度列（类别或可读数值维度）。
    """
    dims = [c for c in columns if _is_dimension_label(c, numeric)]
    if not dims:
        return columns[0] if columns else None

    if group_by:
        group_hits = [
            c for c in dims
            if any(term in str(group_by) and term in c for term in group_by)
        ]
        if group_hits:
            name_hits = [c for c in group_hits if _DIMENSION_NAME_RE.search(c)]
            return _prefer_dimension(name_hits or group_hits, group_by)

    time_dims = [c for c in dims if _TIME_DIM_RE.match(str(c))]
    if time_dims:
        return _prefer_dimension(time_dims, group_by)

    name_cols = [c for c in dims if _DIMENSION_NAME_RE.search(c)]
    if name_cols:
        return name_cols[0]
    code_cols = [c for c in dims if _DIMENSION_CODE_RE.search(c)]
    if code_cols and len(dims) > 1:
        return next((c for c in dims if c not in code_cols), dims[0])
    return dims[0]


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
    values = _value_columns(columns, rows) or numeric
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

    x_key = _pick_dimension_column(columns, numeric, group_by, rows)
    # 时间维度（周/月/日/季/年）天然是趋势序列：除非用户点名占比/排名/对比，否则用折线，
    # 避免把“6月份各周良率”“各月产量”画成柱状而丢失时间趋势，也不再依赖 LLM 纠正类型。
    if (
        not requested_chart
        and x_key
        and _TIME_DIM_RE.match(str(x_key))
        and not share
        and not rank
        and not heatmap
        and not corr
        and not compare
    ):
        trend = True
    value_key = metric_key or _pick_value_column(values, metric_name, question, share)
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
        first, second = values[0], values[1] if len(values) > 1 else None
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
        vkey = value_key or (values[0] if values else None)
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
        vkey = value_key or (values[0] if values else None)
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

    want_combo = requested_chart == "combo" or (compare and len(values) >= 2)
    if want_combo and not (value_key and _is_rate_key(value_key)):
        # 多指标对照（柱线组合）：率类作为右侧折线，量类作为左侧柱。
        # 用户同时点名柱状图与折线图时（requested_chart="combo"），即使只有一个指标也要同时呈现柱和线，而非只输出柱或只输出线。
        rate_cols = [c for c in values if _is_rate_key(c)]
        abs_cols = [c for c in values if not _is_rate_key(c)]
        bars = [{"key": c, "name": c} for c in (abs_cols or values[:1])[:2]]
        lines = [{"key": c, "name": c} for c in (rate_cols or values[:1])[:2]]
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
        series_keys = values[:4]
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
    bars = [{"key": c, "name": c} for c in values[:3]]
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
    numeric = _value_columns(columns, rows)
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

_LLM_CHART_TYPES = {"bar", "line", "donut"}


def chart_contract_is_ambiguous(
    chart: dict[str, Any] | None,
    columns: list[str],
    rows: list[list[Any]],
    question: str,
    metric_name: str,
    analysis_type: str | None = None,
) -> bool:
    """判断确定性图表选择是否"没把握"，需要升级到 LLM 语义判定。

    触发条件（都是规则容易踩坑的语义选择）：
      1. 没有产出图表；或
      2. 单选型图表（bar/line/donut）里存在 >=2 个候选数值列，规则可能在多个指标间选错；或
      3. 横轴被选成"年份"但结果里还存在更细的时间粒度列（周/月/季），说明轴选错。
    """
    if not chart:
        return True
    # 多指标对比/相关/热力由规则负责（combo/scatter/heatmap），不升级，避免选错单轴丢多序列。
    if chart.get("type") in ("combo", "scatter", "heatmap", "donut"):
        return False
    numeric = _numeric_columns(columns, rows)
    vk = chart.get("valueKey") or (chart.get("bars") or [{}])[0].get("key")
    if not vk and numeric:
        return True
    if vk and vk not in numeric:
        return True
    x = chart.get("xKey")
    # 横轴回退到了数值/维度列：说明规则没识别出真正的类别/时间维度
    if x and x in numeric and not _is_dimension_label(x, numeric):
        return True
    # 选了“年份”作为横轴，但结果里还有更细的时间粒度列（周/月/季）
    if x and _YEAR_ONLY_RE.match(str(x)):
        fine = [
            c for c in columns
            if c in numeric and _TIME_DIM_RE.match(str(c)) and not _YEAR_ONLY_RE.match(str(c))
        ]
        if fine:
            return True
    # 用户明显要“占比/率”，但选中的值列不是比率列，且结果里存在比率列
    wants_rate = bool(_RATE_KEY_RE.search(str(metric_name))) or bool(_SHARE_QUESTION_RE.search(str(question)))
    if wants_rate and vk and not _is_rate_key(vk):
        if any(c for c in numeric if _is_rate_key(c)):
            return True
    return False


def build_visual_contract_prompt(
    question: str,
    metric_name: str,
    metric_resolution: dict[str, Any] | None,
    columns: list[str],
    sample_rows: list[list[Any]],
) -> str:
    metric = metric_resolution or {}
    return (
        "你是图表参数选择器。根据用户问题、指标口径与查询结果列，选出最合适的图表配置。"
        "只能从下方给定的列名中选值，不要虚构列名。"
        "只输出一个 JSON，不要任何解释或代码块："
        '{"chartType":"bar|line|donut","xKey":"<列名>","valueKey":"<列名>","unit":"%或空"}'
        f"用户问题：{question}"
        f"指标名称：{metric_name}（口径：{metric.get('name') or '—'}；来源：{metric.get('source') or '—'}）"
        f"结果列：{json.dumps(columns, ensure_ascii=False)}"
        f"示例数据：{json.dumps(sample_rows[:5], ensure_ascii=False, default=str)}"
        "判定规则："
        "- xKey 是要做横轴/维度的列（时间、类别、名称均可；周/月/季度优先于年份）。"
        "- valueKey 是要画到纵轴的指标列，必须是数值列。"
        "- 若问题含“占比/构成/比例/份额”，chartType 用 donut；含“趋势/走势/变化/折线”用 line；其余用 bar。"
        "- unit 是纵轴单位，比率列给 \"%\"，其余给 \"\"。"
    )


def _parse_llm_chart_json(llm_text: str) -> dict[str, Any] | None:
    if not llm_text:
        return None
    text = str(llm_text).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def refine_chart_contract(
    columns: list[str],
    rows: list[list[Any]],
    question: str,
    metric_name: str,
    llm_text: str,
) -> dict[str, Any] | None:
    """用 LLM 给出的 JSON 选择图表配置，校验后产出与 decide_chart 一致的契约；非法则返回 None。"""
    payload = _parse_llm_chart_json(llm_text)
    if not payload:
        return None
    chart_type = str(payload.get("chartType") or "").lower()
    if chart_type not in _LLM_CHART_TYPES:
        return None
    x_key = payload.get("xKey")
    value_key = payload.get("valueKey")
    if x_key not in columns or value_key not in columns or x_key == value_key:
        return None
    numeric = _numeric_columns(columns, rows)
    if value_key not in numeric:
        return None
    # valueKey 必须是真正的指标列，不能是时间/类别/编码这类维度；
    # xKey 交由 LLM 按语义决定（可能是我方规则未覆盖的列名），只要与 valueKey 不同即可。
    if _is_dimension_label(value_key, numeric):
        return None
    unit = str(payload.get("unit") or "").strip()
    if unit not in ("", "%"):
        unit = ""
    computed_unit = "%" if _is_rate_key(value_key) else (unit or "")
    display_limit = 36 if chart_type == "line" else 12
    numeric_set = set(numeric)
    chart_rows: list[dict[str, Any]] = []
    for entry in rows[:display_limit]:
        d = dict(zip(columns, entry))
        for key in numeric_set:
            try:
                d[key] = float(d[key])
            except (TypeError, ValueError):
                pass
        chart_rows.append(d)
    note = _note(rows, display_limit)
    if chart_type == "donut":
        donut_data = [
            {"name": str(row.get(x_key, "未命名")), "value": float(row[value_key])}
            for row in chart_rows if value_key
        ]
        total = sum(item["value"] for item in donut_data)
        return {
            "type": "donut",
            "kicker": "结构占比 · 智能识别",
            "data": donut_data,
            "centerTitle": "合计",
            "centerValue": _fmt_total(total),
            "legend": [{"label": item["name"]} for item in donut_data],
            "unit": computed_unit,
            "note": note,
            "source": "llm",
        }
    if chart_type == "line":
        return {
            "type": "line",
            "kicker": "趋势分析 · 智能识别",
            "data": chart_rows,
            "xKey": x_key,
            "series": [{"key": value_key, "name": value_key}],
            "valueKey": value_key,
            "unit": computed_unit,
            "note": note,
            "source": "llm",
        }
    return {
        "type": "bar",
        "kicker": "查询结果 · 智能识别",
        "data": chart_rows,
        "xKey": x_key,
        "bars": [{"key": value_key, "name": value_key}],
        "valueKey": value_key,
        "unit": computed_unit,
        "note": note,
        "source": "llm",
    }
