"""验证图表/维度 LLM 升级机制（visual_contract_llm_enabled）。

背景：规则是字符串匹配（枚举式），遇到未覆盖的列名会把维度当指标值。例如：
  - stat_year/stat_week/yield_rate：规则把年份当横轴、把周数列当指标值，横轴全变 2026；
  - order_no/out_qty：order_no 是数值形式的订单号（order_no / line_no / batch_no 这类
    编号列没有被规则捕获），规则会把它当成 y 轴指标，bars 里变成 [order_no, out_qty]。

本脚本用真实 LLM 走一遍与线上 graph 完全相同的编排函数 _maybe_refine_visual_contract，
逐条展示“开启前规则错 -> 开启后 LLM 纠正”。每次使用真实 API 调用，不做 mock。

用法（后端 root 下）：
  ./.venv/Scripts/python.exe scripts\verify_visual_contract_llm.py            # 跑全部
  ./.venv/Scripts/python.exe scripts\verify_visual_contract_llm.py --sample week
  ./.venv/Scripts/python.exe scripts\verify_visual_contract_llm.py --sample order_no
  ./.venv/Scripts/python.exe scripts\verify_visual_contract_llm.py --sample line_no
"""
from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent import graph as G  # noqa: E402
from app.agent import interpretation as I  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import provider  # noqa: E402

SAMPLES = {
    "week": {
        "columns": ["stat_year", "stat_week", "yield_rate"],
        "rows": [
            ["2026", "23", "0.975"],
            ["2026", "24", "0.962"],
            ["2026", "25", "0.958"],
            ["2026", "26", "0.970"],
            ["2026", "27", "0.974"],
        ],
        "question": "6月份各周良率",
        "metric": {"name": "良率", "source": "published"},
        "expect_x": "stat_week",
        "expect_value": "yield_rate",
        "expect_type": "line",
        "desc": "周数样例：规则把年份当横轴，横轴全变 2026",
    },
    "order_no": {
        "columns": ["order_no", "out_qty"],
        "rows": [
            ["1001", "520"],
            ["1002", "640"],
            ["1003", "580"],
            ["1004", "710"],
            ["1005", "495"],
        ],
        "question": "各订单产量",
        "metric": {"name": "产量", "source": "published"},
        "expect_x": "order_no",
        "expect_value": "out_qty",
        "expect_type": "bar",
        "desc": "订单号样例：order_no 是数值订单号，规则把它当指标值，bars=[order_no, out_qty]",
    },
    "line_no": {
        "columns": ["line_no", "output_qty", "qualified_qty"],
        "rows": [
            ["1", "1000", "980"],
            ["2", "1100", "1020"],
            ["3", "950", "940"],
        ],
        "question": "各产线合格数量",
        "metric": {"name": "合格数", "source": "published"},
        "expect_x": "line_no",
        "expect_value": "qualified_qty",
        "expect_type": "bar",
        "desc": "产线号样例：line_no 是数值产线号，规则把它当指标值，且在多指标间选错列",
    },
}


def run_sample(sample: str) -> bool:
    cfg = SAMPLES[sample]
    columns = cfg["columns"]
    rows = cfg["rows"]
    question = cfg["question"]
    metric = cfg["metric"]

    print(f"===== 样例 [{sample}]：{cfg['desc']} =====")
    deterministic = I.decide_chart(
        analysis_type="query",
        requested_chart=None,
        question=question,
        group_by=[],
        metric_name=metric["name"],
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=False,
    )
    print("--- 开启前：纯规则 ---")
    print(f"  chartType = {deterministic['type']}")
    print(f"  xKey      = {deterministic['xKey']}  (期望 {cfg['expect_x']})")
    print(f"  valueKey  = {deterministic['valueKey']}  (期望 {cfg['expect_value']})")
    print(f"  bars      = {[b['key'] for b in deterministic.get('bars', [])]}")
    print(f"  xValues   = {[r.get(deterministic['xKey']) for r in deterministic.get('data', [])][:5]}")
    ambiguous = I.chart_contract_is_ambiguous(deterministic, columns, rows, question, metric["name"], "query")
    print(f"  是否升级到 LLM: {ambiguous}")

    settings.visual_contract_llm_enabled = True
    interp = {"kind": "interpretation", "version": 1, "chart": deterministic, "kpis": []}
    execution = {"columns": columns, "rows": rows}
    state = {"task": {"analysisType": "query", "normalizedQuestion": question}, "user_question": question}

    used_llm = {"hit": False}

    def gen(state_, step, system, user):
        used_llm["hit"] = True
        raw = provider.chat(system, user, temperature=0.1, max_tokens=300)
        print(f"  LLM 原始返回: {raw.strip()[:120]}")
        return raw

    out = G._maybe_refine_visual_contract(
        state, execution, interp, metric, SimpleNamespace(generate_text=gen), question
    )
    settings.visual_contract_llm_enabled = False

    chart = out.get("chart") or {}
    print("--- 开启后：LLM 升级（flag ON）---")
    print(f"  chartType = {chart.get('type')}")
    print(f"  xKey      = {chart.get('xKey')}")
    print(f"  valueKey  = {chart.get('valueKey')}")
    print(f"  unit      = {chart.get('unit')}")
    print(f"  chart_source = {out.get('chart_source')}")
    print(f"  xValues      = {[r.get(chart.get('xKey')) for r in chart.get('data', [])][:5]}")

    # 现在规则已把这类列名识别为维度，且 LLM 升级默认开启。二者任一修正即可算通过：
    # 规则已正确 -> chart_source=None；规则仍歧义 -> LLM 升级 -> chart_source="llm"。
    source = out.get("chart_source") or ("rule" if not used_llm["hit"] else "llm_missing")
    correct = (
        chart.get("type") == cfg["expect_type"]
        and chart.get("xKey") == cfg["expect_x"]
        and chart.get("valueKey") == cfg["expect_value"]
    )
    print(f"  图表来源 = {source}")
    ok = correct
    print()
    print(f"RESULT[{sample}]: " + ("PASS" if ok else "FAIL"))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="验证图表/维度 LLM 升级机制")
    parser.add_argument("--sample", choices=list(SAMPLES.keys()), default=None, help="只跑指定样例")
    args = parser.parse_args()
    names = [args.sample] if args.sample else list(SAMPLES.keys())
    print(f"待执行样例：{names}")
    print()
    all_ok = True
    for name in names:
        if not run_sample(name):
            all_ok = False
    print()
    print("总体 RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())