"""阶段3知识接口的HTTP契约与错误码。"""

from fastapi.testclient import TestClient

from app.main import app


def test_knowledge_api_contract(knowledge_session):
    client = TestClient(app)

    topics = client.get("/api/knowledge/topics")
    assert topics.status_code == 200
    assert {item["code"] for item in topics.json()} == {"PROD", "QUAL", "EQPT", "INVT"}

    invalid = client.post(
        "/api/knowledge/metrics",
        json={"name": "非法指标", "topic": "生产分析", "formula": "COUNT(*)", "source": "missing_table"},
    )
    assert invalid.status_code == 400

    created = client.post(
        "/api/knowledge/metrics",
        json={
            "name": "工序投入数量",
            "topic": "生产分析",
            "formula": "SUM(input_qty)",
            "source": "mes_process_output",
            "timeField": "stat_date",
            "dimensions": ["工序"],
        },
    )
    assert created.status_code == 200
    metric_id = created.json()["id"]
    assert created.json()["status"] == "待审核"

    published = client.post(f"/api/knowledge/metrics/{metric_id}/publish")
    assert published.status_code == 200
    assert published.json()["availableForAnalysis"] is True

    conflict = client.delete("/api/knowledge/objects/Product")
    assert conflict.status_code == 409
