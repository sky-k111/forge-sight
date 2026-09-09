# -*- coding: utf-8 -*-
"""批量问析测试：结果写入 JSON 报告，避免终端截断。"""
import json, time, urllib.request, sys
BASE = "http://127.0.0.1:8000"
SRC = 2
OUT = r"D:\aaa\a-外包\metric-monitoring-integration\docs\testsets\axis_qa_run.json"

def req(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

def new_session():
    return req("POST", "/api/analysis/sessions", {"title": "axis-qa", "sourceId": SRC})["id"]

def run_query(sid, q):
    rid = req("POST", f"/api/analysis/sessions/{sid}/runs", {"question": q})["runId"]
    for _ in range(55):
        r = req("GET", f"/api/analysis/runs/{rid}")
        # blocked：写意图被守卫直接拦截，属正常终态；waiting_user：Agent 主动停下来澄清，
        # 同样是正常终态（非调度卡死）。把它们一并视为可汇报的终态，避免被误判成超时。
        if r.get("status") in ("succeeded", "failed", "canceled", "blocked", "waiting_user"):
            return r
        time.sleep(1.1)
    return {"status": "timeout", "errorCode": "POLL_TIMEOUT"}

def compact(r):
    ch = {}
    interp = None
    for a in r.get("artifacts") or []:
        if a.get("type") == "interpretation":
            interp = (a.get("content") or {})
            break
    c = (interp or {}).get("chart") or {}
    ch = {"type": c.get("type"), "xKey": c.get("xKey"),
          "xAxis_kind": (c.get("xAxis") or {}).get("kind"),
          "xAxis_grain": (c.get("xAxis") or {}).get("grain"),
          "bars": [b.get("key") for b in c.get("bars") or []],
          "series": [s.get("key") for s in c.get("series") or []],
          "lines": [l.get("key") for l in c.get("lines") or []],
          "unit": c.get("unit"), "valueKey": c.get("valueKey")}
    return {
        "status": r.get("status"), "error": r.get("errorCode"),
        "callCount": (r.get("usage") or {}).get("callCount"),
        "tasks": [(t.get("order"), t.get("title"), t.get("analysisType"), t.get("status")) for t in r.get("tasks") or []],
        "assistant": ((r.get("assistantMessage") or {}).get("content") or "")[:120].replace("\n", " "),
        "chart": ch,
        "kpis": [(k.get("label"), k.get("value"), k.get("caption")) for k in (interp or {}).get("kpis") or []],
    }

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    scenarios = [
        ("A1 2026各月产量-趋势", ["2026年各月产量按趋势展示"]),
        ("A2 6月各周良率", ["6月份各周良率"]),
        ("A3 6月各产线产量", ["6月份各产线的产量"]),
        ("A4 6月各产线良率", ["6月按产线看工序良率"]),
        ("A5 5月各工序不良率", ["5月各工序的不良率"]),
        ("A6 今年各月停机时长", ["今年每月设备停机时长"]),
        ("A7 上半年各月抽检不良率", ["2026年上半年各月抽检不良率"]),
        ("A8 各产线非计划停机", ["各产线的非计划停机时长"]),
        ("B1 6月不良类型构成", ["6月份不良类型构成分布"]),
        ("B2 各产品不良类型占比", ["各产品的不良类型占比"]),
        ("C1 6月产量前5工序", ["6月份产量最高的前5个工序"]),
        ("C2 良率前3产品", ["工序良率最高的前3个产品"]),
        ("D1 6月总产量KPI", ["6月份总产量是多少"]),
        ("D2 5月工序良率单值", ["输出5月份良率"]),
        ("D3 低于安全库存产品", ["当前低于安全库存的产品有哪些"]),
        ("E1 各产线产量+合格数量", ["6月份各产线的产量和合格数量"]),
        ("E2 6月各周良率+不良率", ["6月各周的良率和不良率"]),
        ("F1 闲聊-讲笑话", ["给我讲个笑话"]),
        ("F2 闲聊-天气", ["今天上海天气怎么样"]),
        ("F3 角色-你是谁", ["你是谁"]),
        ("F4 系统-现在几点", ["现在几点了"]),
        ("F5 无效指标-员工薪资", ["6月份员工薪资情况如何"]),
        ("F6 写意图-删除数据", ["把6月份的产量数据删掉"]),
        ("F7 写意图-修改数据", ["把6月总产量改成1000"]),
        ("F8 超范围-2020", ["2020年的总产量是多少"]),
        ("F9 范围外字段-产品价格", ["6月份产品价格是多少"]),
        ("F10 多问题复合", ["6月份各产线的产量以及良率分别是多少"]),
        ("G1 追问-所有产线吗", ["5月份不良率是多少", "你这个是所有产线的吗"]),
        ("G2 追问-那各产线呢", ["6月各产线良率", "那按工序再看一下"]),
    ]
    results = []
    for label, msgs in scenarios:
        sid = new_session()
        turns = [compact(run_query(sid, m)) for m in msgs]
        results.append({"label": label, "turns": turns})
        try: req("DELETE", f"/api/analysis/sessions/{sid}?userId=local-test-user")
        except Exception: pass
        print(f"[{label}]", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"sourceId": SRC, "generatedAt": time.ctime(), "results": results}, f, ensure_ascii=False, indent=2)
    print("WROTE", OUT, "scenarios=", len(results), flush=True)

if __name__ == "__main__":
    main()