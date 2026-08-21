"""Build and render compact Feishu chart previews from Agent result artifacts."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


COLORS = ["#3568D4", "#0C8F87", "#7B61C9", "#D88923", "#C95454", "#54738F"]
GRID = "#DCE4EC"
INK = "#142334"
MUTED = "#66788A"


@dataclass(frozen=True)
class FeishuChartSpec:
    type: str
    title: str
    x_key: str
    value_keys: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    note: str
    task_id: str | None = None


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_rate(key: str) -> bool:
    return bool(re.search(r"(?:率|占比|比例|百分比|rate|ratio|percent|pct)", key, re.I))


def _is_date(key: str, rows: list[dict[str, Any]]) -> bool:
    if re.search(r"(?:date|time|day|month|year|日期|时间|月份|年度)", key, re.I):
        return True
    values = [str(row.get(key) or "") for row in rows[:8] if row.get(key) not in (None, "")]
    return bool(values) and all(re.match(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?", value) for value in values)


def _requested_chart(snapshot: dict, task: dict | None) -> str | None:
    requested = (task or {}).get("requestedChart") or ((task or {}).get("intent") or {}).get("requestedChart")
    if requested:
        return str(requested)
    text = " ".join(
        str(value or "")
        for value in (snapshot.get("question"), (task or {}).get("title"), (task or {}).get("normalizedQuestion"))
    )
    patterns = (
        (r"组合图|双轴图|柱线图", "combo"),
        (r"热力图|热度图|矩阵图", "heatmap"),
        (r"面积图", "area"),
        (r"折线图|趋势图|曲线图", "line"),
        (r"柱状图|条形图", "bar"),
        (r"饼图|环形图", "donut"),
        (r"散点图", "scatter"),
        (r"表格展示|用表格|以表格", "table"),
    )
    return next((kind for pattern, kind in patterns if re.search(pattern, text)), None)


def _chart_type(snapshot: dict, task: dict | None, x_key: str, numeric: list[str], rows: list[dict]) -> str:
    requested = _requested_chart(snapshot, task)
    if requested:
        return requested
    analysis_type = str((task or {}).get("analysisType") or "")
    question = " ".join(
        str(value or "")
        for value in (snapshot.get("question"), (task or {}).get("title"), (task or {}).get("normalizedQuestion"))
    )
    if analysis_type == "correlation" and len(numeric) >= 2:
        return "scatter"
    if _is_date(x_key, rows) or analysis_type == "trend":
        return "line"
    if len(numeric) >= 2 and any(_is_rate(key) for key in numeric) and any(not _is_rate(key) for key in numeric):
        if re.search(r"对比|比较|与|和|同时|关系", question):
            return "combo"
    if len(numeric) == 1 and len(rows) <= 10 and re.search(r"占比|构成|比例|份额", question):
        return "donut"
    return "bar"


def build_chart_specs(snapshot: dict, *, limit: int = 3) -> list[FeishuChartSpec]:
    """Project successful result artifacts into at most ``limit`` charts."""

    tasks = {str(task.get("id")): task for task in snapshot.get("tasks") or []}
    specs: list[FeishuChartSpec] = []
    for artifact in snapshot.get("artifacts") or []:
        if artifact.get("type") != "result":
            continue
        content = artifact.get("content") or {}
        if not content.get("ok", True):
            continue
        columns = [str(item) for item in content.get("columns") or []]
        raw_rows = content.get("rows") or []
        if len(columns) < 2 or not raw_rows:
            continue
        rows = [dict(row) if isinstance(row, dict) else dict(zip(columns, list(row))) for row in raw_rows]
        numeric_all = [key for key in columns if any(_number(row.get(key)) is not None for row in rows)]
        x_key = next((key for key in columns if key not in numeric_all), columns[0])
        numeric = [key for key in numeric_all if key != x_key]
        if not numeric:
            continue
        task_id = str(artifact.get("taskId") or "") or None
        task = tasks.get(task_id or "")
        chart_type = _chart_type(snapshot, task, x_key, numeric_all, rows)
        if chart_type == "table":
            continue
        value_keys = numeric_all[:2] if chart_type == "scatter" else numeric[:4]
        if not value_keys:
            continue
        display_limit = 36 if chart_type in {"line", "area"} else 12
        title = str((task or {}).get("title") or snapshot.get("question") or "数据分析结果").strip()
        note = (
            f"图表展示前 {display_limit} 项，共返回 {len(rows)} 条记录"
            if len(rows) > display_limit
            else f"图表展示全部 {len(rows)} 条返回记录"
        )
        specs.append(
            FeishuChartSpec(
                type=chart_type,
                title=title[:80],
                x_key=x_key,
                value_keys=tuple(value_keys),
                rows=tuple(rows[:display_limit]),
                note=note,
                task_id=task_id,
            )
        )
        if len(specs) >= max(1, limit):
            break
    return specs


def _font(size: int, *, bold: bool = False):
    names = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _label(value: Any, *, width: int = 10) -> str:
    text = str(value if value is not None else "—")
    date = re.match(r"^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?", text)
    if date:
        return f"{date.group(2).zfill(2)}-{date.group(3).zfill(2)}" if date.group(3) else f"{date.group(1)}-{date.group(2).zfill(2)}"
    return text if len(text) <= width else text[: width - 1] + "…"


def _nice_domain(values: list[float], *, rate: bool = False) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    spread = high - low
    pad = spread * 0.16 if spread else max(abs(high) * 0.05, 0.1 if rate else 1.0)
    low, high = low - pad, high + pad
    if rate:
        low, high = max(0.0, low), min(100.0, high)
    elif min(values) >= 0 and (max(values) - min(values)) > max(values) * 0.35:
        low = 0.0
    if low == high:
        high = low + 1.0
    return low, high


def _format_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if absolute >= 10_000:
        return f"{value / 10_000:.1f}万"
    if absolute >= 1000:
        return f"{value:,.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _base(spec: FeishuChartSpec):
    image = Image.new("RGB", (1200, 680), "#F5F8FB")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, 1180, 660), radius=24, fill="white", outline="#DCE4EC", width=2)
    draw.text((58, 48), spec.title, font=_font(30, bold=True), fill=INK)
    draw.text((58, 93), f"{spec.x_key} · {' / '.join(spec.value_keys)}", font=_font(17), fill=MUTED)
    draw.text((58, 622), spec.note, font=_font(15), fill=MUTED)
    return image, draw, (92, 145, 1130, 560)


def _axes(draw, box, domain, *, right_axis: bool = False) -> None:
    left, top, right, bottom = box
    low, high = domain
    for index in range(5):
        y = bottom - (bottom - top) * index / 4
        draw.line((left, y, right, y), fill=GRID, width=1)
        label = _format_number(low + (high - low) * index / 4)
        x = right + 12 if right_axis else left - 16
        draw.text((x, y), label, font=_font(14), fill=MUTED, anchor="lm" if right_axis else "rm")
    if not right_axis:
        draw.line((left, top, left, bottom), fill="#B7C4D1", width=2)
        draw.line((left, bottom, right, bottom), fill="#B7C4D1", width=2)


def _series_values(spec, keys=None):
    return {key: [_number(row.get(key)) for row in spec.rows] for key in (keys or spec.value_keys)}


def _legend(draw, items, *, y=590) -> None:
    x = 58
    for name, color in items[:6]:
        draw.rounded_rectangle((x, y + 5, x + 18, y + 17), radius=4, fill=color)
        draw.text((x + 26, y), _label(name, width=18), font=_font(15), fill=INK)
        x += min(245, 54 + len(name) * 17)


def _to_png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _render_cartesian(spec: FeishuChartSpec, kind: str) -> bytes:
    image, draw, box = _base(spec)
    left, top, right, bottom = box
    series = _series_values(spec)
    all_values = [value for values in series.values() for value in values if value is not None]
    rate = bool(spec.value_keys) and all(_is_rate(key) for key in spec.value_keys) and all(0 <= value <= 100 for value in all_values)
    domain = _nice_domain(all_values, rate=rate)
    _axes(draw, box, domain)
    count = len(spec.rows)
    slot = (right - left) / max(count, 1)
    y_for = lambda value: bottom - (value - domain[0]) / (domain[1] - domain[0]) * (bottom - top)
    if kind == "bar":
        width = min(54, slot * 0.72 / max(len(spec.value_keys), 1))
        for row_index, row in enumerate(spec.rows):
            center = left + slot * (row_index + 0.5)
            for key_index, key in enumerate(spec.value_keys):
                value = _number(row.get(key))
                if value is not None:
                    x0 = center - width * len(spec.value_keys) / 2 + key_index * width + 2
                    draw.rounded_rectangle((x0, y_for(value), x0 + width - 4, bottom), radius=6, fill=COLORS[key_index])
    else:
        for key_index, key in enumerate(spec.value_keys):
            points = [
                (left + slot * (index + 0.5), y_for(value))
                for index, row in enumerate(spec.rows)
                if (value := _number(row.get(key))) is not None
            ]
            if kind == "area" and len(points) >= 2:
                overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                ImageDraw.Draw(overlay).polygon(points + [(points[-1][0], bottom), (points[0][0], bottom)], fill=COLORS[key_index] + "30")
                image.paste(overlay, (0, 0), overlay)
                draw = ImageDraw.Draw(image)
            if len(points) >= 2:
                draw.line(points, fill=COLORS[key_index], width=5, joint="curve")
            for x, y in points:
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="white", outline=COLORS[key_index], width=4)
    step = max(1, math.ceil(count / 8))
    for index, row in enumerate(spec.rows):
        if index % step == 0 or index == count - 1:
            draw.text((left + slot * (index + 0.5), bottom + 15), _label(row.get(spec.x_key)), font=_font(14), fill=MUTED, anchor="ma")
    _legend(draw, [(key, COLORS[index]) for index, key in enumerate(spec.value_keys)])
    return _to_png(image)


def _render_donut(spec: FeishuChartSpec) -> bytes:
    image, draw, _ = _base(spec)
    key = spec.value_keys[0]
    values = [max(0.0, _number(row.get(key)) or 0.0) for row in spec.rows]
    total = sum(values)
    box = (120, 165, 535, 580)
    angle = -90.0
    for index, value in enumerate(values):
        sweep = 360 * value / total if total else 0
        draw.pieslice(box, start=angle, end=angle + sweep, fill=COLORS[index % len(COLORS)], outline="white", width=3)
        angle += sweep
    draw.ellipse((245, 290, 410, 455), fill="white")
    draw.text((327, 350), _format_number(total), font=_font(28, bold=True), fill=INK, anchor="mm")
    draw.text((327, 387), "合计", font=_font(16), fill=MUTED, anchor="mm")
    y = 175
    for index, (row, value) in enumerate(zip(spec.rows, values)):
        if index >= 8:
            break
        draw.rounded_rectangle((630, y + 7, 650, y + 27), radius=5, fill=COLORS[index % len(COLORS)])
        draw.text((668, y), _label(row.get(spec.x_key), width=18), font=_font(18), fill=INK)
        share = value / total * 100 if total else 0
        draw.text((1090, y), f"{_format_number(value)}  ·  {share:.1f}%", font=_font(17), fill=MUTED, anchor="ra")
        y += 48
    return _to_png(image)


def _render_scatter(spec: FeishuChartSpec) -> bytes:
    if len(spec.value_keys) < 2:
        return _render_cartesian(spec, "bar")
    image, draw, box = _base(spec)
    x_key, y_key = spec.value_keys[:2]
    points = [(_number(row.get(x_key)), _number(row.get(y_key))) for row in spec.rows]
    points = [(x, y) for x, y in points if x is not None and y is not None]
    x_domain = _nice_domain([point[0] for point in points])
    y_domain = _nice_domain([point[1] for point in points])
    _axes(draw, box, y_domain)
    left, top, right, bottom = box
    for x, y in points:
        px = left + (x - x_domain[0]) / (x_domain[1] - x_domain[0]) * (right - left)
        py = bottom - (y - y_domain[0]) / (y_domain[1] - y_domain[0]) * (bottom - top)
        draw.ellipse((px - 8, py - 8, px + 8, py + 8), fill="#7B61C9", outline="white", width=3)
    draw.text((left, bottom + 25), _format_number(x_domain[0]), font=_font(14), fill=MUTED, anchor="la")
    draw.text((right, bottom + 25), _format_number(x_domain[1]), font=_font(14), fill=MUTED, anchor="ra")
    _legend(draw, [(f"{x_key} × {y_key}", "#7B61C9")])
    return _to_png(image)


def _render_heatmap(spec: FeishuChartSpec) -> bytes:
    categorical = [key for key in spec.rows[0] if key not in spec.value_keys and any(_number(row.get(key)) is None for row in spec.rows)]
    if len(categorical) < 2:
        return _render_cartesian(spec, "bar")
    row_key, column_key = categorical[:2]
    value_key = spec.value_keys[0]
    row_labels = list(dict.fromkeys(_label(row.get(row_key), width=12) for row in spec.rows))[:10]
    col_labels = list(dict.fromkeys(_label(row.get(column_key), width=10) for row in spec.rows))[:8]
    values = {}
    for row in spec.rows:
        key = (_label(row.get(row_key), width=12), _label(row.get(column_key), width=10))
        values[key] = values.get(key, 0.0) + (_number(row.get(value_key)) or 0.0)
    maximum = max(values.values(), default=1.0) or 1.0
    image, draw, box = _base(spec)
    left, top, right, bottom = box
    cell_w = (right - left - 120) / max(len(col_labels), 1)
    cell_h = (bottom - top - 35) / max(len(row_labels), 1)
    for col, label in enumerate(col_labels):
        draw.text((left + 120 + (col + 0.5) * cell_w, top), label, font=_font(14), fill=MUTED, anchor="ma")
    for row_index, row_label in enumerate(row_labels):
        y = top + 35 + row_index * cell_h
        draw.text((left + 105, y + cell_h / 2), row_label, font=_font(14), fill=MUTED, anchor="rm")
        for col, col_label in enumerate(col_labels):
            value = values.get((row_label, col_label), 0.0)
            strength = value / maximum
            fill = tuple(round(245 + (channel - 245) * strength) for channel in (53, 104, 212))
            x = left + 120 + col * cell_w
            draw.rounded_rectangle((x + 2, y + 2, x + cell_w - 2, y + cell_h - 2), radius=5, fill=fill)
            draw.text((x + cell_w / 2, y + cell_h / 2), _format_number(value), font=_font(13), fill="white" if strength > 0.55 else INK, anchor="mm")
    return _to_png(image)


def _render_combo(spec: FeishuChartSpec) -> bytes:
    absolute = tuple(key for key in spec.value_keys if not _is_rate(key))
    rates = tuple(key for key in spec.value_keys if _is_rate(key))
    if not absolute or not rates:
        return _render_cartesian(spec, "line")
    image, draw, box = _base(spec)
    left, top, right, bottom = box
    bar_values = _series_values(spec, absolute)
    line_values = _series_values(spec, rates)
    bar_domain = _nice_domain([v for values in bar_values.values() for v in values if v is not None])
    line_flat = [v for values in line_values.values() for v in values if v is not None]
    line_domain = _nice_domain(line_flat, rate=all(0 <= v <= 100 for v in line_flat))
    _axes(draw, box, bar_domain)
    _axes(draw, box, line_domain, right_axis=True)
    count = len(spec.rows)
    slot = (right - left) / max(count, 1)
    bar_width = min(55, slot * 0.58 / max(len(absolute), 1))
    for row_index, row in enumerate(spec.rows):
        center = left + slot * (row_index + 0.5)
        for key_index, key in enumerate(absolute):
            value = _number(row.get(key))
            if value is not None:
                y = bottom - (value - bar_domain[0]) / (bar_domain[1] - bar_domain[0]) * (bottom - top)
                x = center - bar_width * len(absolute) / 2 + key_index * bar_width
                draw.rounded_rectangle((x, y, x + bar_width - 4, bottom), radius=5, fill=COLORS[key_index])
    for rate_index, key in enumerate(rates):
        points = []
        for row_index, row in enumerate(spec.rows):
            value = _number(row.get(key))
            if value is not None:
                x = left + slot * (row_index + 0.5)
                y = bottom - (value - line_domain[0]) / (line_domain[1] - line_domain[0]) * (bottom - top)
                points.append((x, y))
        color = COLORS[len(absolute) + rate_index]
        if len(points) >= 2:
            draw.line(points, fill=color, width=5, joint="curve")
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="white", outline=color, width=3)
    for index, row in enumerate(spec.rows):
        if index % max(1, math.ceil(count / 8)) == 0 or index == count - 1:
            draw.text((left + slot * (index + 0.5), bottom + 15), _label(row.get(spec.x_key)), font=_font(14), fill=MUTED, anchor="ma")
    _legend(draw, [(key, COLORS[index]) for index, key in enumerate((*absolute, *rates))])
    return _to_png(image)


def render_chart_png(spec: FeishuChartSpec) -> bytes:
    if spec.type == "donut":
        return _render_donut(spec)
    if spec.type == "scatter":
        return _render_scatter(spec)
    if spec.type == "heatmap":
        return _render_heatmap(spec)
    if spec.type == "combo":
        return _render_combo(spec)
    kind = "area" if spec.type == "area" else "line" if spec.type == "line" else "bar"
    return _render_cartesian(spec, kind)
