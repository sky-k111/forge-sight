"""阶段 4 语义上下文包 HTTP 契约。"""

from fastapi.testclient import TestClient

from app.main import app


def test_retrieval_api_contract(knowledge_session):
    client = TestClient(app)

    response = client.post(
        "/api/retrieval/context",
        json={"question": "统计最近 7 天各工序良率趋势", "sourceId": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contextPackageId"].startswith("ctx-")
    assert body["retrieval"]["strategy"] == "lexical-rule"
    assert body["retrieval"]["intents"] == ["trend"]
    assert body["metrics"][0]["id"] == "M-004"
    assert body["executionPolicy"]["instruction"]

    invalid = client.post("/api/retrieval/context", json={"question": ""})
    assert invalid.status_code == 422

    missing_source = client.post(
        "/api/retrieval/context",
        json={"question": "分析产量", "sourceId": 9999},
    )
    assert missing_source.status_code == 404
