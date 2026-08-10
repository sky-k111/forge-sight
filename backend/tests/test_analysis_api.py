"""阶段 5A HTTP 契约。"""

from fastapi.testclient import TestClient

from app.main import app


def test_analysis_session_run_event_and_usage_api(analysis_session):
    client = TestClient(app)
    session_response = client.post(
        "/api/analysis/sessions",
        json={"title": "生产问析", "sourceId": 1, "userId": "u-api"},
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    run_response = client.post(
        f"/api/analysis/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "api-idem-1"},
        json={"question": "统计最近 7 天各工序良率"},
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["runId"]

    duplicate = client.post(
        f"/api/analysis/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "api-idem-1"},
        json={"question": "重复点击"},
    )
    assert duplicate.json()["runId"] == run_id
    assert client.get(f"/api/analysis/runs/{run_id}").json()["lastSeq"] == 1
    events = client.get(f"/api/analysis/runs/{run_id}/events").json()["items"]
    assert events[0]["type"] == "run.accepted"
    usage_body = client.get(f"/api/analysis/runs/{run_id}/usage").json()
    assert usage_body["callCount"] == 0
    assert usage_body["budget"]["limits"]["max_llm_calls"] > 0

    cancelled = client.post(f"/api/analysis/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    stream = client.get(f"/api/analysis/runs/{run_id}/stream")
    assert stream.status_code == 200
    assert "event: run.accepted" in stream.text
    assert "event: run.cancelled" in stream.text
