"""V05-V06: polished server-side PDF and typed Excel exports."""

from io import BytesIO

from openpyxl import load_workbook
from pypdf import PdfReader

from app.reports.exports import render_excel_bytes, render_pdf_bytes


def _report_content() -> dict:
    return {
        "schemaVersion": "weekly-report/v1",
        "identity": {
            "reportId": "report-1",
            "versionId": "version-1",
            "title": "生产经营周报",
            "reportType": "weekly",
            "periodStart": "2026-08-03T00:00:00",
            "periodEnd": "2026-08-10T00:00:00",
            "timezone": "Asia/Shanghai",
        },
        "managementSummary": {
            "findings": [
                {
                    "level": "confirmed",
                    "text": "本周总产量提升 6.12%，关键产线运行平稳。",
                    "evidenceArtifactIds": ["artifact-output"],
                    "confidence": 94,
                },
                {
                    "level": "attention",
                    "text": "AOI 工序不良率高于目标，需要在下周完成参数复核。",
                    "evidenceArtifactIds": ["artifact-quality"],
                    "confidence": 86,
                },
            ],
            "actions": [
                {
                    "text": "复核 AOI 工序参数并跟踪改善结果。",
                    "owner": "质量负责人",
                    "dueAt": "2026-08-14T18:00:00",
                    "priority": "high",
                    "evidenceArtifactIds": ["artifact-quality"],
                }
            ],
        },
        "metrics": [
            {
                "key": "output_qty",
                "name": "总产量",
                "value": 12840,
                "unit": "件",
                "previousValue": 12100,
                "changeRate": 0.0612,
                "trend": "up",
                "evidenceArtifactIds": ["artifact-output"],
                "confidence": 94,
            },
            {
                "key": "pass_rate",
                "name": "一次通过率",
                "value": 98.2,
                "unit": "%",
                "previousValue": 97.8,
                "changeRate": 0.0041,
                "trend": "up",
                "evidenceArtifactIds": ["artifact-quality"],
                "confidence": 91,
            },
            {
                "key": "downtime",
                "name": "非计划停机",
                "value": 4.6,
                "unit": "小时",
                "previousValue": 6.1,
                "changeRate": -0.2459,
                "trend": "down",
                "evidenceArtifactIds": ["artifact-equipment"],
                "confidence": 89,
            },
        ],
        "trends": [
            {
                "metricKey": "output_qty",
                "label": "近四周总产量",
                "unit": "件",
                "points": [
                    {"label": "W29", "value": 11520},
                    {"label": "W30", "value": 11880},
                    {"label": "W31", "value": 12100},
                    {"label": "W32", "value": 12840},
                ],
            }
        ],
        "anomalies": [
            {
                "title": "AOI 工序不良率偏高",
                "description": "周三夜班出现连续波动，建议复核贴片温度与检测阈值。",
                "severity": "high",
                "metricKey": "pass_rate",
                "evidenceArtifactIds": ["artifact-quality"],
                "confidence": 86,
            }
        ],
        "progress": [
            {
                "title": "瓶颈工序改善",
                "status": "on_track",
                "note": "已完成两条产线节拍调整。",
                "completionRate": 72,
            }
        ],
        "recommendations": [
            {
                "title": "优先治理 AOI 波动",
                "rationale": "该工序对一次通过率贡献最大。",
                "priority": "high",
                "evidenceArtifactIds": ["artifact-quality"],
            }
        ],
        "dataQuality": {
            "coverage": 96,
            "delayedSources": [],
            "missingMetrics": [],
            "temporaryMetrics": [],
            "failedSections": [],
        },
        "appendix": {
            "sources": ["MES 生产执行系统", "QMS 质量管理系统"],
            "definitions": [
                {"name": "一次通过率", "definition": "一次检验合格数量 / 总检验数量"}
            ],
        },
    }


def test_pdf_is_multi_page_searchable_and_contains_a_data_insight_image():
    payload = render_pdf_bytes(_report_content())

    assert payload.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(payload))
    assert len(reader.pages) >= 2
    extracted = "".join(page.extract_text() or "" for page in reader.pages)
    assert "生产经营周报" in extracted
    assert "AOI 工序不良率偏高" in extracted
    resources = reader.pages[0]["/Resources"]
    xobjects = resources.get("/XObject", {})
    assert any(item.get_object().get("/Subtype") == "/Image" for item in xobjects.values())


def test_excel_uses_required_sheets_and_real_numeric_cells():
    payload = render_excel_bytes(_report_content())

    workbook = load_workbook(BytesIO(payload), data_only=True)
    assert workbook.sheetnames == ["指标概览", "异常明细", "行动建议", "数据来源与口径"]
    overview = workbook["指标概览"]
    assert overview.freeze_panes == "A6"
    assert overview["B7"].value == 12840
    assert overview["B7"].data_type == "n"
    assert overview["F7"].value == 94
    assert workbook["异常明细"]["A7"].value == "AOI 工序不良率偏高"


def test_long_pdf_table_repeats_cleanly_and_keeps_last_row_searchable():
    content = _report_content()
    content["anomalies"] = [
        {
            "title": f"异常记录 {index:03d}",
            "description": "这是用于验证跨页表格、自动行高和长文本换行的异常说明。" * 2,
            "severity": "medium",
            "metricKey": "pass_rate",
            "evidenceArtifactIds": [f"artifact-{index}"],
            "confidence": 80,
        }
        for index in range(1, 61)
    ]

    reader = PdfReader(BytesIO(render_pdf_bytes(content)))
    extracted = "".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) >= 5
    assert "异常记录 060" in extracted


def test_excel_neutralizes_formula_injection_in_untrusted_text():
    content = _report_content()
    content["anomalies"][0]["title"] = '=HYPERLINK("https://evil.invalid","click")'

    workbook = load_workbook(BytesIO(render_excel_bytes(content)), data_only=False)
    cell = workbook["异常明细"]["A7"]

    assert cell.data_type == "s"
    assert cell.value.startswith("'=HYPERLINK")
