"""Run a traceable business-conversation acceptance suite against a live API.

The suite deliberately uses normal manufacturing staff language. It records
the semantic contract, SQL, result shape, chart axes, final narrative, usage,
and failure evidence without persisting any API key.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from statistics import median
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


CASES: list[dict[str, Any]] = [
    {"id": "V01", "class": "有效-时间对比", "turns": [{"q": "王主管想看一下，2026年6月和7月的工序良率变化，给我一张折线图。", "expect": {"metric": ["工序良率"], "charts": ["line"], "x": ["月", "时间"], "sql": ["2026-06-01", "2026-08-01"], "minRows": 2}}]},
    {"id": "V02", "class": "有效-周粒度", "turns": [{"q": "帮我把2026年6月每周的产量列出来，用柱状图看。", "expect": {"metric": ["产量"], "charts": ["bar"], "x": ["周"], "sql": ["2026-06-01", "2026-07-01"], "minRows": 4}}]},
    {"id": "V03", "class": "有效-产线维度", "turns": [{"q": "我想看看2026年7月各条产线的产量，按高到低排一下。", "expect": {"metric": ["产量"], "dimension": ["产线", "line"], "minRows": 3}}]},
    {"id": "V04", "class": "有效-产品维度", "turns": [{"q": "2026年6月产量最高的5个产品是哪几个？给我柱状图。", "expect": {"metric": ["产量"], "charts": ["bar"], "dimension": ["产品", "product"], "minRows": 5}}]},
    {"id": "V05", "class": "有效-工序维度", "turns": [{"q": "质量例会上要用，帮我比较2026年6月各工序的不良数量。", "expect": {"metric": ["不良数量"], "dimension": ["工序", "process"], "minRows": 3}}]},
    {"id": "V06", "class": "有效-标量", "turns": [{"q": "2026年6月整体工序良率是多少？一句话告诉我。", "expect": {"metric": ["工序良率"], "sql": ["2026-06-01", "2026-07-01"], "minRows": 1}}]},
    {"id": "V07", "class": "有效-月份产线交叉", "turns": [{"q": "比较2026年6月和7月各产线的产量，用分组柱状图。", "expect": {"metric": ["产量"], "charts": ["bar"], "dimension": ["产线", "月份"], "sql": ["2026-06-01", "2026-08-01"], "minRows": 5}}]},
    {"id": "V08", "class": "有效-抽检口径", "turns": [{"q": "帮我看看2026年6月各工序的抽检不良率，找出最高的几个。", "expect": {"metric": ["抽检不良率"], "dimension": ["工序"], "minRows": 3}}]},
    {"id": "V09", "class": "有效-库存规则", "turns": [{"q": "仓库这边要排查，最新库存里哪些产品低于安全库存？", "expect": {"metric": ["低于安全库存"], "minRows": 0, "allowEmpty": True}}]},
    {"id": "V10", "class": "有效-设备指标", "turns": [{"q": "设备主管想看2026年6月各设备的非计划停机时长，按最长的排。", "expect": {"metric": ["非计划停机时长"], "dimension": ["设备"], "minRows": 1}}]},
    {"id": "V11", "class": "有效-精确筛选", "turns": [{"q": "只看产品P013，统计它2026年6月的产量。", "expect": {"metric": ["产量"], "sql": ["2026-06-01", "2026-07-01", "P013"], "minRows": 1}}]},
    {"id": "V12", "class": "有效-年度趋势", "turns": [{"q": "把2025年8月到2026年7月的月度产量趋势画出来。", "expect": {"metric": ["产量"], "charts": ["line"], "x": ["月", "时间"], "sql": ["2025-08-01", "2026-08-01"], "minRows": 10}}]},
    {"id": "V13", "class": "有效-复合交付", "turns": [{"q": "先比较2026年6月和7月的良率并输出折线图，再统计6月逐周产量，分别给我饼图和柱状图。", "expect": {"metric": ["工序良率", "产量"], "charts": ["line", "donut", "bar"], "sql": ["2026-06-01", "2026-08-01"], "minRows": 2}}]},
    {"id": "V14", "class": "有效-自然口吻", "turns": [{"q": "老周，帮我瞅一眼6月份哪条线做得最多，别给我太复杂。", "expect": {"metric": ["产量"], "dimension": ["产线"], "minRows": 1}}]},

    {"id": "M01", "class": "多轮-图表与时间变更", "turns": [
        {"q": "先看2026年6月各产线产量，用柱状图。", "expect": {"metric": ["产量"], "charts": ["bar"], "dimension": ["产线"], "minRows": 3}},
        {"q": "这个换成折线图，数据别重新算。", "expect": {"metric": ["产量"], "charts": ["line"], "reuse": True, "minRows": 3}},
        {"q": "那7月份呢？", "expect": {"metric": ["产量"], "sql": ["2026-07-01", "2026-08-01"], "minRows": 3}},
    ]},
    {"id": "M02", "class": "多轮-闲聊穿插后承接", "turns": [
        {"q": "分析2026年6月各工序的良率，按从低到高排。", "expect": {"metric": ["工序良率"], "dimension": ["工序"], "minRows": 3}},
        {"q": "顺便问一句，你是谁？", "expect": {"kind": "chat", "contains": ["分析"]}},
        {"q": "回到刚才，最低的是哪个工序？", "expect": {"metric": ["工序良率"], "context": True, "minRows": 1}},
    ]},
    {"id": "M03", "class": "多轮-筛选递进", "turns": [
        {"q": "统计数据里最近7天各产线的产量。", "expect": {"metric": ["产量"], "dimension": ["产线"], "minRows": 3}},
        {"q": "只看L03。", "expect": {"metric": ["产量"], "sql": ["L03"], "context": True, "minRows": 1}},
        {"q": "再和L02比一下。", "expect": {"metric": ["产量"], "sql": ["L02", "L03"], "context": True, "minRows": 2}},
    ]},
    {"id": "M04", "class": "多轮-指标与维度切换", "turns": [
        {"q": "看看2026年6月各工序不良数量。", "expect": {"metric": ["不良数量"], "dimension": ["工序"], "minRows": 3}},
        {"q": "刚才这个改成工序不良率。", "expect": {"metric": ["工序不良率"], "context": True, "minRows": 3}},
        {"q": "再按产品看看。", "expect": {"metric": ["工序不良率"], "dimension": ["产品"], "context": True, "minRows": 3}},
    ]},

    {"id": "C01", "class": "闲聊-问候", "turns": [{"q": "你好，今天辛苦了。", "expect": {"kind": "chat", "contains": ["你好"]}}]},
    {"id": "C02", "class": "闲聊-能力介绍", "turns": [{"q": "你能帮我们生产主管做些什么？", "expect": {"kind": "chat", "contains": ["产量", "质量"]}}]},
    {"id": "C03", "class": "闲聊-身份", "turns": [{"q": "你是机器人还是数据分析师？", "expect": {"kind": "chat", "contains": ["助手"]}}]},
    {"id": "C04", "class": "闲聊-超出范围", "turns": [{"q": "今天上海天气怎么样？", "expect": {"kind": "chat", "forbid": ["实时温度是", "当前气温为"]}}]},
    {"id": "C05", "class": "闲聊-轻松请求", "turns": [{"q": "开会前讲个不太冷的制造业笑话吧。", "expect": {"kind": "chat", "minText": 15}}]},
    {"id": "C06", "class": "闲聊-使用引导", "turns": [{"q": "我第一次用，问题怎么问你才容易得到准一点的结果？", "expect": {"kind": "chat", "contains": ["指标", "时间"], "minText": 40}}]},
    {"id": "U01", "class": "通用能力-时间", "turns": [{"q": "现在几点了？", "expect": {"kind": "utility", "contains": [":"]}}]},
    {"id": "U02", "class": "通用能力-数据范围", "turns": [{"q": "我现在问的数据是哪个数据源？", "expect": {"kind": "utility", "contains": ["年度合成制造数据"]}}]},

    {"id": "I01", "class": "无效-信息不足", "turns": [{"q": "帮我看看数据有没有问题。", "expect": {"kind": "clarify", "noSql": True}}]},
    {"id": "I02", "class": "无效-未登记指标", "turns": [{"q": "分析2026年6月每条产线的单位能耗成本趋势。", "expect": {"kind": "clarify", "noSql": True}}]},
    {"id": "I03", "class": "无效-未登记维度", "turns": [{"q": "按供应商比较2026年6月的工序良率。", "expect": {"kind": "clarify", "noSql": True}}]},
    {"id": "I04", "class": "无效-超出数据时间", "turns": [{"q": "看看2028年6月各产线的产量。", "expect": {"metric": ["产量"], "allowEmpty": True, "sql": ["2028-06-01", "2028-07-01"]}}]},
    {"id": "I05", "class": "无效-越权写操作", "turns": [{"q": "把6月份的不良记录删掉，再重新统计良率。", "expect": {"kind": "reject", "noSql": True, "contains": ["只读", "写入"]}}]},
    {"id": "I06", "class": "无效-口径歧义", "turns": [{"q": "帮我看看2026年6月的不良率。", "expect": {"kind": "clarify", "noSql": True}}]},
]


def call(base: str, method: str, path: str, payload: dict[str, Any] | None = None,
         headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(f"{base}{path}", data=data, method=method, headers={
        "Content-Type": "application/json", "X-Tenant-ID": "tenant-default", **(headers or {}),
    })
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit localhost acceptance target
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"API unavailable at {base}: {exc.reason}") from exc


def terminal_snapshot(base: str, run_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = call(base, "GET", f"/api/analysis/runs/{run_id}")
        if snapshot.get("status") in {"succeeded", "failed", "blocked", "cancelled", "waiting_user"}:
            return snapshot
        time.sleep(1)
    return {"id": run_id, "status": "timeout", "errorCode": "QA_TIMEOUT", "artifacts": []}


def artifact_contents(snapshot: dict[str, Any], artifact_type: str) -> list[dict[str, Any]]:
    return [item.get("content") or {} for item in snapshot.get("artifacts") or [] if item.get("type") == artifact_type]


def compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    keep_types = {"query-binding", "sql", "validation", "result", "conclusion", "interpretation", "delivery-ledger"}
    return {
        "runId": snapshot.get("id"), "status": snapshot.get("status"), "errorCode": snapshot.get("errorCode"),
        "error": snapshot.get("error"), "activeDurationMs": snapshot.get("activeDurationMs"),
        "usage": snapshot.get("usage"), "tasks": snapshot.get("tasks"), "steps": snapshot.get("steps"),
        "assistantMessage": snapshot.get("assistantMessage"), "delivery": snapshot.get("delivery"),
        "interaction": snapshot.get("interaction"),
        "artifacts": [item for item in snapshot.get("artifacts") or [] if item.get("type") in keep_types],
    }


def inspect(snapshot: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    status = str(snapshot.get("status") or "")
    assistant = snapshot.get("assistantMessage") or {}
    text = str(assistant.get("content") or "")
    error = snapshot.get("error") or {}
    interaction = snapshot.get("interaction") or {}
    interaction_prompt = interaction.get("prompt") or {}
    clarification_text = " ".join(
        str(field.get("question") or "") for field in interaction_prompt.get("fields") or []
    ).strip()
    step_safe_text = " ".join(
        str((step.get("error") or {}).get("userSafeMessage") or "") for step in snapshot.get("steps") or []
    ).strip()
    display_text = text or str(
        error.get("userSafeMessage") or error.get("safeMessage") or error.get("message")
        or clarification_text or step_safe_text or ""
    )
    sql_items = artifact_contents(snapshot, "sql")
    sql = "\n".join(str(item.get("sql") or "") for item in sql_items)
    results = artifact_contents(snapshot, "result")
    row_count = sum(int(item.get("row_count") or len(item.get("rows") or [])) for item in results if item.get("ok"))
    interpretations = artifact_contents(snapshot, "interpretation")
    charts: list[dict[str, Any]] = []
    for item in interpretations:
        charts.extend(item.get("charts") or ([item["chart"]] if item.get("chart") else []))
    artifact_text = json.dumps(snapshot.get("artifacts") or [], ensure_ascii=False)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, actual: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual})

    kind = expected.get("kind", "analysis")
    if kind in {"chat", "utility"}:
        check("会话成功", status == "succeeded", status)
        check("未误查业务数据库", not sql and not results, f"sql={bool(sql)}, results={len(results)}")
    elif kind == "clarify":
        check("没有伪造分析成功", status != "succeeded" or not results, status)
        check("给出澄清或能力边界", bool(display_text or snapshot.get("interaction")), snapshot.get("errorCode"))
    elif kind == "reject":
        check("写操作被拒绝", status in {"failed", "blocked", "succeeded"} and not sql, status)
    else:
        check("运行完成", status == "succeeded", status)
        allow_empty = bool(expected.get("allowEmpty"))
        if expected.get("reuse"):
            check("查询得到可接受结果", bool(charts) and not sql, f"charts={len(charts)}, sql={bool(sql)}")
        else:
            check("查询得到可接受结果", bool(results) and (allow_empty or row_count >= int(expected.get("minRows", 1))), row_count)

    if expected.get("noSql"):
        check("未生成 SQL", not sql, bool(sql))
    for token in expected.get("metric") or []:
        check(f"指标:{token}", token in artifact_text or token in text, token)
    for token in expected.get("dimension") or []:
        check(f"维度:{token}", token.lower() in artifact_text.lower() or token in text, token)
    for token in expected.get("sql") or []:
        check(f"SQL约束:{token}", token in sql, token)
    for chart_type in expected.get("charts") or []:
        check(f"图表:{chart_type}", any(chart.get("type") == chart_type for chart in charts), [c.get("type") for c in charts])
    x_candidates = expected.get("x") or []
    if x_candidates:
        actual_x = [str(chart.get("xKey") or (chart.get("xAxis") or {}).get("key") or "") for chart in charts]
        check(f"横轴:{'/'.join(x_candidates)}", any(token in value for token in x_candidates for value in actual_x), actual_x)
    if charts:
        check("图表存在数据", allow_empty or all(bool(chart.get("data")) for chart in charts),
              [len(chart.get("data") or []) for chart in charts])
        check("纵轴绑定指标", allow_empty or all(bool(chart.get("valueKey") or chart.get("series") or chart.get("bars")) for chart in charts),
              [chart.get("valueKey") for chart in charts])
        check("图文表述一致", not any(phrase in display_text for phrase in ("无法直接输出图", "不能直接输出图", "无法生成图")), display_text[:120])
    if expected.get("reuse"):
        check("复用上轮查询结果", not sql and bool(charts) and "复用" in display_text,
              {"sql": bool(sql), "charts": len(charts), "reply": display_text[:80]})
    if expected.get("context"):
        task_text = json.dumps(snapshot.get("tasks") or [], ensure_ascii=False)
        check("承接会话上下文", any(marker in task_text for marker in (
            "inheritedFrom", '"relation": "follow_up"', '"relation": "refine_previous"', "；指标："
        )), task_text[:300])
    for token in expected.get("contains") or []:
        check(f"回复包含:{token}", token in display_text, token)
    for token in expected.get("forbid") or []:
        check(f"回复不虚构:{token}", token not in display_text, token)
    check("回复可读", len(display_text.strip()) >= int(expected.get("minText", 10)), len(display_text.strip()))
    check("无内部异常泄露", not any(token in display_text for token in (
        "Traceback", "Exception", "SELECT `", "[SQL:", "pymysql.err", "sqlalchemy.exc", "Internal Server Error",
    )), None)

    passed = all(item["ok"] for item in checks)
    return {
        "passed": passed, "score": round(sum(item["ok"] for item in checks) / max(1, len(checks)) * 100, 1),
        "status": status, "errorCode": snapshot.get("errorCode"), "rowCount": row_count,
        "charts": [{"type": c.get("type"), "xKey": c.get("xKey"), "valueKey": c.get("valueKey"),
                    "series": [s.get("key") for s in c.get("series") or []], "points": len(c.get("data") or [])} for c in charts],
        "checks": checks, "assistantExcerpt": display_text[:500],
    }


def set_model(base: str, model: str) -> dict[str, Any]:
    current = call(base, "GET", "/api/model-settings/llm")
    profile_id = current.get("activeProfileId")
    if not profile_id:
        raise RuntimeError("No active LLM profile")
    return call(base, "PATCH", "/api/model-settings/llm", {"profileId": profile_id, "model": model, "activate": True})


def run_suite(base: str, source_id: int, model: str, cases: list[dict[str, Any]], timeout_seconds: int) -> dict[str, Any]:
    set_model(base, model)
    started = datetime.now().astimezone()
    records = []
    for index, case in enumerate(cases, 1):
        print(f"[{model}] {index}/{len(cases)} {case['id']} {case['class']}", flush=True)
        session = call(base, "POST", "/api/analysis/sessions", {
            "title": f"QA {model} {case['id']}", "sourceId": source_id, "userId": "local-test-user",
        })
        turns = []
        for turn_index, turn in enumerate(case["turns"], 1):
            accepted = call(base, "POST", f"/api/analysis/sessions/{session['id']}/runs", {"question": turn["q"]}, {
                "Idempotency-Key": f"qa-{model}-{case['id']}-{turn_index}-{uuid4().hex[:8]}"
            })
            snapshot = terminal_snapshot(base, accepted["runId"], timeout_seconds)
            assessment = inspect(snapshot, turn["expect"])
            print(f"  T{turn_index} {snapshot.get('status')} score={assessment['score']} run={accepted['runId']}", flush=True)
            turns.append({"turn": turn_index, "question": turn["q"], "expected": turn["expect"],
                          "assessment": assessment, "trace": compact_snapshot(snapshot)})
        records.append({"id": case["id"], "class": case["class"], "sessionId": session["id"],
                        "passed": all(turn["assessment"]["passed"] for turn in turns), "turns": turns})
    return {
        "model": model, "sourceId": source_id, "startedAt": started.isoformat(),
        "finishedAt": datetime.now().astimezone().isoformat(), "caseCount": len(records),
        "turnCount": sum(len(item["turns"]) for item in records),
        "passedCases": sum(item["passed"] for item in records), "cases": records,
    }


def render_report(rounds: list[dict[str, Any]]) -> str:
    lines = ["# 对话系统业务验收报告", "", f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}", "",
             "判定说明：脚本检查运行状态、指标与维度绑定、SQL 时间/筛选条件、图表类型、横纵轴、数据点、回复可读性和安全边界。所有运行均可通过 runId 回查。", ""]
    if rounds:
        primary = rounds[0]
        fallback_by_id = {
            case["id"]: case
            for round_result in rounds[1:]
            for case in round_result["cases"]
        }
        effective = [
            case for case in primary["cases"]
            if case["passed"] or fallback_by_id.get(case["id"], {}).get("passed")
        ]
        unresolved = [
            case["id"] for case in primary["cases"]
            if not case["passed"] and not fallback_by_id.get(case["id"], {}).get("passed")
        ]
        fallback_resolved = sum(
            1 for case in primary["cases"]
            if not case["passed"] and fallback_by_id.get(case["id"], {}).get("passed")
        )
        lines += [
            "## 综合结论", "",
            f"最终通过：{len(effective)}/{primary['caseCount']}；主模型未通过后由备用模型解决：{fallback_resolved} 项；"
            f"仍未通过：{'、'.join(unresolved) if unresolved else '无'}。", "",
        ]
    for result in rounds:
        total = result["caseCount"]
        all_turns = [turn for case in result["cases"] for turn in case["turns"]]
        durations = sorted(int(turn["trace"].get("activeDurationMs") or 0) for turn in all_turns)
        tokens = sum(int((turn["trace"].get("usage") or {}).get("totalTokens") or 0) for turn in all_turns)
        calls = sum(int((turn["trace"].get("usage") or {}).get("callCount") or 0) for turn in all_turns)
        p95_index = max(0, min(len(durations) - 1, int(len(durations) * 0.95) - 1)) if durations else 0
        latency = (
            f"耗时：中位数 {median(durations) / 1000:.1f}s，P95 {durations[p95_index] / 1000:.1f}s；"
            f"模型调用 {calls} 次，记录 Token {tokens}。"
            if durations else ""
        )
        lines += [f"## {result['model']}", "", f"场景通过：{result['passedCases']}/{total}；对话轮次：{result['turnCount']}。", "",
                  latency, "",
                  "| 编号 | 等价类 | 结果 | 最低得分 | 运行追踪 |", "| --- | --- | --- | ---: | --- |"]
        for case in result["cases"]:
            score = min(turn["assessment"]["score"] for turn in case["turns"])
            runs = "、".join(str(turn["trace"].get("runId")) for turn in case["turns"])
            lines.append(f"| {case['id']} | {case['class']} | {'通过' if case['passed'] else '未通过'} | {score:.1f} | {runs} |")
        lines += ["", "### 未通过明细", ""]
        failed = [case for case in result["cases"] if not case["passed"]]
        if not failed:
            lines.append("无。")
        for case in failed:
            lines.append(f"#### {case['id']} · {case['class']}")
            for turn in case["turns"]:
                bad = [item["name"] for item in turn["assessment"]["checks"] if not item["ok"]]
                if bad:
                    lines += [f"- 问题：{turn['question']}", f"- Run ID：{turn['trace'].get('runId')}",
                              f"- 状态/错误：{turn['assessment']['status']} / {turn['assessment'].get('errorCode')}",
                              f"- 未满足：{'、'.join(bad)}", f"- 回复摘录：{turn['assessment']['assistantExcerpt'] or '无'}"]
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--source-id", type=int, default=2)
    parser.add_argument("--primary-model", default="deepseek-chat")
    parser.add_argument("--fallback-model", default="deepseek-v4-flash")
    parser.add_argument("--timeout", type=int, default=330)
    parser.add_argument("--output-dir", default="../../docs/qa/runs/conversation-acceptance-20260910")
    parser.add_argument("--only", default="", help="Comma-separated case ids")
    args = parser.parse_args()
    selected = CASES
    if args.only:
        ids = {value.strip() for value in args.only.split(",") if value.strip()}
        selected = [case for case in CASES if case["id"] in ids]
    output = (Path(__file__).resolve().parent / args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    original = call(args.base_url, "GET", "/api/model-settings/llm")
    original_model = original.get("model")
    rounds: list[dict[str, Any]] = []
    try:
        primary = run_suite(args.base_url, args.source_id, args.primary_model, selected, args.timeout)
        rounds.append(primary)
        (output / f"results-{args.primary_model}.json").write_text(json.dumps(primary, ensure_ascii=False, indent=2), encoding="utf-8")
        failed_ids = {case["id"] for case in primary["cases"] if not case["passed"]}
        fallback_cases = [case for case in selected if case["id"] in failed_ids]
        if fallback_cases and args.fallback_model:
            fallback = run_suite(args.base_url, args.source_id, args.fallback_model, fallback_cases, args.timeout)
            rounds.append(fallback)
            (output / f"results-{args.fallback_model}.json").write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if original_model:
            set_model(args.base_url, original_model)
    (output / "report.md").write_text(render_report(rounds), encoding="utf-8")
    (output / "cases.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output / 'report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
