"""业务总览只读取当前数据源已登记、已验证的底座能力。"""

from fastapi.testclient import TestClient

from app.api import overview
from app.db.models import AnalysisMessage, AnalysisRun, AnalysisSession, DataTable
from app.main import app


def test_business_overview_is_scoped_to_source(knowledge_session, monkeypatch):
    monkeypatch.setattr(overview, "PlatformSession", knowledge_session)
    client = TestClient(app)

    response = client.get("/api/overview?sourceId=1")

    assert response.status_code == 200
    body = response.json()
    assert body["source"]["id"] == 1
    assert body["snapshot"]["records"] == 0
    assert body["snapshot"]["registeredTables"] == 6
    assert body["snapshot"]["readOnly"] is True
    assert body["capability"]["state"] == "ready"
    assert body["capability"]["canAnalyze"] is True
    assert body["metrics"]["trusted"] > 0
    assert body["recent"] == {"total": 0, "items": []}
    assert body["topics"]
    assert body["suggestions"]


def test_business_overview_merges_physical_and_semantic_topic_names(knowledge_session, monkeypatch):
    monkeypatch.setattr(overview, "PlatformSession", knowledge_session)
    with knowledge_session() as session:
        session.add(DataTable(source_id=1, name="production_extra", topic="生产", registered=True))
        session.commit()

    body = TestClient(app).get("/api/overview?sourceId=1").json()
    topic_names = [item["name"] for item in body["topics"]]

    assert "生产分析" in topic_names
    assert "生产" not in topic_names
    production = next(item for item in body["topics"] if item["name"] == "生产分析")
    assert production["tableCount"] == 1
    assert "coverage" not in production


def test_business_overview_only_returns_current_users_real_questions(knowledge_session, monkeypatch):
    monkeypatch.setattr(overview, "PlatformSession", knowledge_session)
    with knowledge_session() as session:
        session.add_all([
            AnalysisSession(id="business-session", title="系统生成标题", user_id="local-test-user", source_id=1),
            AnalysisSession(id="acceptance-session", title="阶段5 EXPLAIN 验收", user_id="qa-user", source_id=1),
        ])
        session.add_all([
            AnalysisMessage(
                id="business-question",
                session_id="business-session",
                role="user",
                content="  分析本月各工序良率\n并与目标值比较  ",
                sequence_no=1,
            ),
            AnalysisMessage(
                id="acceptance-question",
                session_id="acceptance-session",
                role="user",
                content="执行内部并发验收",
                sequence_no=1,
            ),
        ])
        session.add(
            AnalysisRun(
                id="business-run",
                session_id="business-session",
                trigger_message_id="business-question",
                source_id=1,
                status="succeeded",
            )
        )
        session.commit()

    body = TestClient(app).get("/api/overview?sourceId=1&userId=local-test-user").json()

    assert body["recent"]["total"] == 1
    assert body["recent"]["items"][0]["title"] == "分析本月各工序良率 并与目标值比较"
    assert "阶段5" not in str(body["recent"])
    assert "验收" not in str(body["recent"])
