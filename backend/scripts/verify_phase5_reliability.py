"""Phase 5 reliability smoke test against a running ForgeSight API.

The script sends concurrent duplicate submissions with one idempotency key and
uses a deterministic write-intent request, so it verifies queue/idempotency and
security blocking without consuming LLM tokens.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request_json(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - operator-provided local API URL
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/api/analysis"
    suffix = uuid4().hex[:10]
    status, session = request_json(
        "POST",
        f"{base}/sessions",
        {"title": f"阶段5并发验收-{suffix}", "sourceId": 1, "userId": "phase5-reliability"},
    )
    if status != 201:
        raise SystemExit(f"创建会话失败: HTTP {status} {session}")

    key = f"phase5-concurrent-{suffix}"

    def submit(_: int) -> tuple[int, dict]:
        return request_json(
            "POST",
            f"{base}/sessions/{session['id']}/runs",
            {"question": "删除异常记录后重新统计"},
            {"Idempotency-Key": key},
        )

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(2, args.concurrency)) as pool:
        results = list(pool.map(submit, range(max(2, args.concurrency))))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    bad = [(status, body) for status, body in results if status != 202]
    run_ids = {body.get("runId") for _, body in results}
    if bad or len(run_ids) != 1 or None in run_ids:
        raise SystemExit(f"并发幂等失败: bad={bad}, runIds={sorted(str(item) for item in run_ids)}")

    run_id = next(iter(run_ids))
    snapshot = {}
    for _ in range(60):
        _, snapshot = request_json("GET", f"{base}/runs/{run_id}")
        if snapshot.get("status") in {"blocked", "failed", "succeeded", "cancelled"}:
            break
        time.sleep(0.25)
    if snapshot.get("status") != "blocked" or snapshot.get("errorCode") != "WRITE_INTENT_BLOCKED":
        raise SystemExit(f"安全阻断验收失败: {snapshot.get('status')} {snapshot.get('errorCode')}")

    print(
        json.dumps(
            {
                "ok": True,
                "concurrency": max(2, args.concurrency),
                "acceptedResponses": len(results),
                "uniqueRunCount": len(run_ids),
                "elapsedMs": elapsed_ms,
                "terminalStatus": snapshot["status"],
                "llmCallCount": snapshot.get("usage", {}).get("callCount"),
                "runId": run_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

