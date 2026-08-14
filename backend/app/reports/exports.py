"""Deterministic PDF and Excel renderers for frozen weekly-report content."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import reportlab
from PIL import Image as PillowImage
from PIL import ImageDraw
from PIL import ImageFont
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Image,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.schema import StructuredWeeklyReport, calculate_confidence


INK = HexColor("#172033")
MUTED = HexColor("#667085")
BRAND = HexColor("#0B6E69")
BRAND_DARK = HexColor("#084C49")
MINT = HexColor("#DDF4EF")
AMBER = HexColor("#F2A93B")
SURFACE = HexColor("#F5F7FA")
LINE = HexColor("#D9E0E8")


def _register_fonts() -> tuple[str, str]:
    regular = "ForgeSightSong"
    if regular not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


def _pdf_text(value: object) -> str:
    """Treat every model/user value as text, never as ReportLab markup."""

    return escape(str(value), {'"': "&quot;", "'": "&#39;"})


def _styles():
    regular, bold = _register_fonts()
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ForgeSightTitle",
            parent=styles["Title"],
            fontName=bold,
            fontSize=24,
            leading=32,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "eyebrow": ParagraphStyle(
            "ForgeSightEyebrow",
            parent=styles["Normal"],
            fontName=regular,
            fontSize=9,
            leading=12,
            textColor=BRAND,
            spaceAfter=4,
        ),
        "heading": ParagraphStyle(
            "ForgeSightHeading",
            parent=styles["Heading2"],
            fontName=bold,
            fontSize=16,
            leading=22,
            textColor=INK,
            spaceBefore=8,
            spaceAfter=8,
        ),
        "subheading": ParagraphStyle(
            "ForgeSightSubheading",
            parent=styles["Heading3"],
            fontName=bold,
            fontSize=11,
            leading=16,
            textColor=INK,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ForgeSightBody",
            parent=styles["BodyText"],
            fontName=regular,
            fontSize=9.5,
            leading=15,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "ForgeSightSmall",
            parent=styles["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=11,
            textColor=MUTED,
        ),
        "tableHeader": ParagraphStyle(
            "ForgeSightTableHeader",
            parent=styles["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=11,
            textColor=colors.white,
        ),
        "metric": ParagraphStyle(
            "ForgeSightMetric",
            parent=styles["Normal"],
            fontName=bold,
            fontSize=17,
            leading=21,
            textColor=BRAND_DARK,
            alignment=TA_CENTER,
        ),
        "metricLabel": ParagraphStyle(
            "ForgeSightMetricLabel",
            parent=styles["Normal"],
            fontName=regular,
            fontSize=8,
            leading=11,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
    }


def _insight_image(report: StructuredWeeklyReport) -> BytesIO:
    """Create a high-resolution, deterministic insight hero from report data."""

    width, height = 1440, 480
    image = PillowImage.new("RGB", (width, height), "#0A4F4B")
    draw = ImageDraw.Draw(image)
    font_root = Path(reportlab.__file__).resolve().parent / "fonts"
    label_font = ImageFont.truetype(str(font_root / "Vera.ttf"), 20)
    headline_font = ImageFont.truetype(str(font_root / "VeraBd.ttf"), 58)
    caption_font = ImageFont.truetype(str(font_root / "Vera.ttf"), 16)
    for y in range(height):
        ratio = y / height
        color = (
            int(10 + 6 * ratio),
            int(79 + 35 * ratio),
            int(75 + 34 * ratio),
        )
        draw.line((0, y, width, y), fill=color)
    draw.rounded_rectangle((48, 42, width - 48, height - 42), radius=30, outline="#5AD6C8", width=3)
    confidence = calculate_confidence(report.data_quality.model_dump(by_alias=True))
    anomaly_variant = confidence["templateVariant"] == "data_anomaly"
    hero_label = "FORGESIGHT  /  DATA QUALITY ALERT" if anomaly_variant else "FORGESIGHT  /  WEEKLY SIGNAL"
    draw.text((82, 74), hero_label, fill="#B8F2E9", font=label_font)
    primary = report.metrics[0] if report.metrics and not anomaly_variant else None
    headline = f"{primary.value:,.0f}" if primary else f"{confidence['score']}%"
    subtitle = "PRIMARY KPI" if primary else "REPORT CONFIDENCE"
    draw.text((84, 145), headline, fill="white", font=headline_font)
    draw.text((86, 220), subtitle, fill="#B8F2E9", font=caption_font)

    chart_left, chart_top, chart_right, chart_bottom = 610, 92, 1340, 370
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#77CFC4", width=2)
    trend = report.trends[0] if report.trends and len(report.trends[0].points) >= 2 else None
    if trend:
        points = [point.value for point in trend.points]
        low, high = min(points), max(points)
        spread = high - low or 1
        coordinates = []
        for index, value in enumerate(points):
            x = chart_left + (chart_right - chart_left) * index / (len(points) - 1)
            y = chart_bottom - (chart_bottom - chart_top) * (value - low) / spread
            coordinates.append((x, y))
        draw.line(coordinates, fill="#F8C76A", width=8, joint="curve")
        for x, y in coordinates:
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill="#F8C76A", outline="white", width=3)
    draw.text(
        (chart_left, 395),
        "TREND / VERIFIED DATA" if trend else "NO VERIFIED TREND DATA",
        fill="#B8F2E9",
        font=caption_font,
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def _page_decoration(canvas, document) -> None:
    regular, _ = _register_fonts()
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(BRAND)
    canvas.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
    canvas.setFont(regular, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "ForgeSight · 企业数据洞察")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"{canvas.getPageNumber():02d}")
    canvas.restoreState()


def _metric_table(report: StructuredWeeklyReport, styles: dict) -> Table:
    cells = []
    for metric in report.metrics[:4]:
        delta = ""
        if metric.change_rate is not None:
            delta = f"{metric.change_rate:+.1%} 环比"
        cells.append(
            [
                Paragraph(_pdf_text(metric.name), styles["metricLabel"]),
                Paragraph(_pdf_text(f"{metric.value:,.2f} {metric.unit}"), styles["metric"]),
                Paragraph(delta or f"可信度 {metric.confidence}", styles["metricLabel"]),
            ]
        )
    if not cells:
        cells.append(
            [
                Paragraph("数据覆盖率", styles["metricLabel"]),
                Paragraph(f"{report.data_quality.coverage}%", styles["metric"]),
                Paragraph("本期数据质量", styles["metricLabel"]),
            ]
        )
    table = Table([cells], colWidths=[170 * mm / len(cells)] * len(cells), rowHeights=[28 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _report_chart(report: StructuredWeeklyReport) -> Drawing | None:
    spec = report.charts[0] if report.charts else None
    series = report.trends[0] if report.trends else None
    points = spec.points if spec is not None else series.points if series is not None else []
    if len(points) < 2:
        return None
    drawing = Drawing(480, 190)
    if spec is not None and spec.type in {"bar", "pareto", "donut"}:
        chart = HorizontalBarChart()
        chart.x = 80
        chart.y = 28
        chart.height = 132
        chart.width = 350
        chart.data = [[point.value for point in reversed(points[:10])]]
        chart.categoryAxis.categoryNames = [point.label for point in reversed(points[:10])]
        chart.categoryAxis.labels.fontName = _register_fonts()[0]
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.labels.fontName = "Helvetica"
        chart.valueAxis.labels.fontSize = 7
        chart.bars[0].fillColor = AMBER if spec.color_role == "quality" else BRAND
        chart.bars[0].strokeColor = None
        chart.valueAxis.gridStrokeColor = LINE
        chart.valueAxis.gridStrokeDashArray = (2, 2)
        drawing.add(chart)
        return drawing
    chart = HorizontalLineChart()
    chart.x = 55
    chart.y = 34
    chart.height = 120
    chart.width = 390
    chart.data = [[point.value for point in points]]
    chart.categoryAxis.categoryNames = [point.label for point in points]
    chart.categoryAxis.labels.fontName = _register_fonts()[0]
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.lines[0].strokeColor = BRAND
    chart.lines[0].strokeWidth = 2.5
    chart.lines[0].symbol = None
    chart.valueAxis.gridStrokeColor = LINE
    chart.valueAxis.gridStrokeDashArray = (2, 2)
    drawing.add(chart)
    return drawing


def render_pdf_bytes(content: dict) -> bytes:
    """Render an A4, searchable PDF without a browser session."""

    report = StructuredWeeklyReport.model_validate(content)
    styles = _styles()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title=report.identity.title,
        author="ForgeSight",
        subject="企业经营周报",
    )
    period = f"{report.identity.period_start:%Y.%m.%d} — {report.identity.period_end:%Y.%m.%d}"
    story = [
        Spacer(1, 7 * mm),
        Paragraph("FORGESIGHT · WEEKLY BUSINESS REVIEW", styles["eyebrow"]),
        Paragraph(_pdf_text(report.identity.title), styles["title"]),
        Paragraph(
            _pdf_text(
                f"报告周期 {period}　·　数据覆盖率 {report.data_quality.coverage}%　·　版本 {report.identity.version_id}"
            ),
            styles["small"],
        ),
        Spacer(1, 6 * mm),
    ]
    hero = Image(_insight_image(report), width=170 * mm, height=56.7 * mm)
    hero.altText = "根据本期真实指标生成的数据洞察主图"
    story.extend([hero, Spacer(1, 6 * mm), _metric_table(report, styles), Spacer(1, 6 * mm)])
    story.append(Paragraph("管理摘要", styles["heading"]))
    for finding in report.management_summary.findings[:5]:
        prefix = "●" if finding.level == "confirmed" else "◆"
        story.append(
            Paragraph(
                f"<font color='#0B6E69'>{prefix}</font>　{_pdf_text(finding.text)}　"
                f"<font color='#667085'>可信度 {finding.confidence}</font>",
                styles["body"],
            )
        )
        story.append(Spacer(1, 2.5 * mm))
    story.append(PageBreak())
    story.extend(
        [
            Paragraph("指标趋势与经营信号", styles["heading"]),
            Paragraph("图表仅使用已冻结版本中的结构化数值，确保与站内展示一致。", styles["small"]),
        ]
    )
    chart_spec = report.charts[0] if report.charts else None
    if chart_spec is not None:
        story.extend(
            [
                Spacer(1, 3 * mm),
                Paragraph(_pdf_text(chart_spec.title), styles["subheading"]),
                Paragraph(_pdf_text(chart_spec.description), styles["small"]),
            ]
        )
    chart = _report_chart(report)
    if chart is not None:
        story.extend([Spacer(1, 3 * mm), chart])
    else:
        story.extend(
            [
                Spacer(1, 3 * mm),
                Paragraph("当前冻结版本没有可验证的时间序列，因此未绘制趋势图。", styles["body"]),
                Spacer(1, 4 * mm),
            ]
        )

    story.append(Paragraph("异常明细", styles["heading"]))
    anomaly_rows = [
        [
            Paragraph("异常", styles["tableHeader"]),
            Paragraph("等级", styles["tableHeader"]),
            Paragraph("影响与说明", styles["tableHeader"]),
            Paragraph("可信度", styles["tableHeader"]),
        ]
    ]
    for item in report.anomalies:
        anomaly_rows.append(
            [
                Paragraph(_pdf_text(item.title), styles["body"]),
                Paragraph(item.severity.upper(), styles["small"]),
                Paragraph(_pdf_text(item.description), styles["body"]),
                Paragraph(str(item.confidence), styles["body"]),
            ]
        )
    if len(anomaly_rows) == 1:
        anomaly_rows.append(
            [Paragraph("冻结证据中未识别到结构化异常记录", styles["body"]), "—", "—", "—"]
        )
    anomaly_table = LongTable(
        anomaly_rows,
        colWidths=[39 * mm, 20 * mm, 91 * mm, 20 * mm],
        repeatRows=1,
    )
    anomaly_table.setStyle(_pdf_table_style())
    story.append(anomaly_table)

    story.append(Paragraph("行动建议", styles["heading"]))
    action_rows = [
        [
            Paragraph("优先级", styles["tableHeader"]),
            Paragraph("行动", styles["tableHeader"]),
            Paragraph("负责人", styles["tableHeader"]),
            Paragraph("截止时间", styles["tableHeader"]),
        ]
    ]
    for item in report.management_summary.actions:
        action_rows.append(
            [
                Paragraph(item.priority.upper(), styles["small"]),
                Paragraph(_pdf_text(item.text), styles["body"]),
                Paragraph(_pdf_text(item.owner or "待指派"), styles["body"]),
                Paragraph(item.due_at.strftime("%Y-%m-%d") if item.due_at else "待确认", styles["body"]),
            ]
        )
    for item in report.recommendations:
        action_rows.append(
            [
                Paragraph(item.priority.upper(), styles["small"]),
                Paragraph(_pdf_text(f"{item.title}：{item.rationale}"), styles["body"]),
                Paragraph("待指派", styles["body"]),
                Paragraph("待确认", styles["body"]),
            ]
        )
    action_table = LongTable(action_rows, colWidths=[22 * mm, 88 * mm, 32 * mm, 28 * mm], repeatRows=1)
    action_table.setStyle(_pdf_table_style())
    story.extend([action_table, Spacer(1, 5 * mm)])
    story.append(
        Paragraph(
            _pdf_text("数据来源：" + "、".join(report.appendix.sources)) + "。本文件由 ForgeSight 服务端基于冻结版本生成。",
            styles["small"],
        )
    )
    document.build(story, onFirstPage=_page_decoration, onLaterPages=_page_decoration)
    return output.getvalue()


def _pdf_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _prepare_sheet(sheet, title: str, report: StructuredWeeklyReport, headers: list[str]) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    title_cell = sheet.cell(1, 1, _excel_safe(title))
    title_cell.font = Font(name="Microsoft YaHei", size=18, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill("solid", fgColor="084C49")
    title_cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 34
    sheet.cell(3, 1, "报告周期")
    sheet.cell(3, 2, report.identity.period_start)
    sheet.cell(3, 3, "至")
    sheet.cell(3, 4, report.identity.period_end)
    sheet.cell(3, 2).number_format = "yyyy-mm-dd"
    sheet.cell(3, 4).number_format = "yyyy-mm-dd"
    sheet.cell(4, 1, "报告版本")
    sheet.cell(4, 2, report.identity.version_id)
    sheet.cell(4, 3, "导出说明")
    sheet.cell(4, 4, "数据来自同一已冻结周报版本")
    sheet.freeze_panes = "A6"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "1:6"
    for column, value in enumerate(headers, start=1):
        cell = sheet.cell(6, column, value)
        cell.font = Font(name="Microsoft YaHei", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B6E69")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.auto_filter.ref = f"A6:{get_column_letter(len(headers))}6"


def _finish_sheet(sheet, widths: list[int]) -> None:
    thin = Side(style="thin", color="D9E0E8")
    for row in sheet.iter_rows(min_row=6):
        for cell in row:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.row > 6:
                cell.font = Font(name="Microsoft YaHei", size=10, color="172033")
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _excel_safe(value):
    """Prevent spreadsheet applications from executing untrusted report text."""

    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_excel_bytes(content: dict) -> bytes:
    """Render a typed, styled workbook from the same frozen report document."""

    report = StructuredWeeklyReport.model_validate(content)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "指标概览"
    _prepare_sheet(
        overview,
        report.identity.title + " · 指标概览",
        report,
        ["指标", "本期值", "单位", "上期值", "环比", "可信度"],
    )
    for row_index, metric in enumerate(report.metrics, start=7):
        values = [
            metric.name,
            metric.value,
            metric.unit,
            metric.previous_value,
            metric.change_rate,
            metric.confidence,
        ]
        for column, value in enumerate(values, start=1):
            overview.cell(row_index, column, _excel_safe(value))
        overview.cell(row_index, 5).number_format = "0.00%"
    _finish_sheet(overview, [24, 16, 12, 16, 14, 12])

    anomalies = workbook.create_sheet("异常明细")
    _prepare_sheet(
        anomalies,
        report.identity.title + " · 异常明细",
        report,
        ["异常", "等级", "指标", "说明", "可信度", "证据引用"],
    )
    for row_index, item in enumerate(report.anomalies, start=7):
        values = [
            item.title,
            item.severity,
            item.metric_key,
            item.description,
            item.confidence,
            "、".join(item.evidence_artifact_ids),
        ]
        for column, value in enumerate(values, start=1):
            anomalies.cell(row_index, column, _excel_safe(value))
    _finish_sheet(anomalies, [28, 12, 18, 52, 12, 30])

    actions = workbook.create_sheet("行动建议")
    _prepare_sheet(
        actions,
        report.identity.title + " · 行动建议",
        report,
        ["优先级", "行动", "负责人", "截止时间", "依据"],
    )
    row_index = 7
    for item in report.management_summary.actions:
        values = [
            item.priority,
            item.text,
            item.owner,
            item.due_at,
            "、".join(item.evidence_artifact_ids),
        ]
        for column, value in enumerate(values, start=1):
            actions.cell(row_index, column, _excel_safe(value))
        actions.cell(row_index, 4).number_format = "yyyy-mm-dd hh:mm"
        row_index += 1
    for item in report.recommendations:
        values = [item.priority, f"{item.title}：{item.rationale}", None, None, "、".join(item.evidence_artifact_ids)]
        for column, value in enumerate(values, start=1):
            actions.cell(row_index, column, _excel_safe(value))
        row_index += 1
    _finish_sheet(actions, [12, 58, 20, 22, 30])

    sources = workbook.create_sheet("数据来源与口径")
    _prepare_sheet(
        sources,
        report.identity.title + " · 数据来源与口径",
        report,
        ["类型", "名称", "说明", "Schema 版本"],
    )
    row_index = 7
    for source in report.appendix.sources:
        sources.append(["数据来源", _excel_safe(source), "已冻结版本引用", report.schema_version])
        row_index += 1
    for definition in report.appendix.definitions:
        sources.append(
            [
                "指标口径",
                _excel_safe(definition.name),
                _excel_safe(definition.definition),
                report.schema_version,
            ]
        )
    _finish_sheet(sources, [16, 30, 70, 24])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
