from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    AgentMemory,
    AgentMemoryJob,
    AnalysisMessage,
    AnalysisRun,
    AnalysisSession,
    DataSource,
    ExternalChatJob,
)
from app.memory import service


def _factory(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(service, "PlatformSession", factory)
    with factory() as session:
        session.add_all([
            DataSource(id=1, name="一厂", type="mysql", db_name="one", username="reader"),
            DataSource(id=2, name="二厂", type="mysql", db_name="two", username="reader"),
        ])
        setting = service._setting(session)
        setting.enabled = True
        session.commit()
    return factory


def test_memory_recall_is_filtered_by_user_and_source_before_ranking(monkeypatch):
    _factory(monkeypatch)
    own = service.remember("默认使用折线图展示良率趋势", user_id="u1", source_id=1)
    service.remember("默认使用柱状图展示良率趋势", user_id="u2", source_id=1)
    service.remember("默认使用饼图展示良率趋势", user_id="u1", source_id=2)

    result = service.retrieve_memories("查看良率趋势", 1, "u1", run_id="run-1")

    assert [item["id"] for item in result["memories"]] == [own["id"]]
    # 未显式传租户时按数据源归属推导；默认企业已归一化为 tenant-default。
    assert result["scope"] == {"tenantId": "tenant-default", "sourceId": 1, "userId": "u1"}


def test_correction_supersedes_old_memory_and_forget_hides_new_memory(monkeypatch):
    factory = _factory(monkeypatch)
    old = service.remember("默认看最近7天", user_id="u1", source_id=1)

    corrected = service.correct_memory(old["id"], "默认看最近30天", user_id="u1")
    service.forget_memory(corrected["id"], user_id="u1")

    with factory() as session:
        assert session.query(AgentMemory).filter_by(id=old["id"]).one().status == "superseded"
        assert session.query(AgentMemory).filter_by(id=corrected["id"]).one().status == "deleted"
    assert service.retrieve_memories("默认时间范围", 1, "u1")["memories"] == []


def test_completed_run_becomes_compact_episode_without_raw_rows(monkeypatch):
    factory = _factory(monkeypatch)
    with factory() as session:
        business_session = AnalysisSession(id="ses-1", title="良率调查", user_id="u1", source_id=1)
        question = AnalysisMessage(
            id="msg-q", session_id="ses-1", run_id="run-1", role="user",
            content="分析最近一周工序良率", sequence_no=1,
        )
        run = AnalysisRun(
            id="run-1", session_id="ses-1", trigger_message_id="msg-q", source_id=1,
            status="succeeded", memory_enabled=True, memory_experiment_variant="on",
        )
        answer = AnalysisMessage(
            id="msg-a", session_id="ses-1", run_id="run-1", role="assistant",
            content="测试工序良率最低，建议复核异常批次。", sequence_no=2,
        )
        external_job = ExternalChatJob(
            id="ecj-1",
            channel="feishu",
            tenant_key="tenant-1",
            external_message_id="om-1",
            external_chat_id="oc-1",
            external_user_id="ou-1",
            question=question.content,
            run_id=run.id,
        )
        session.add_all([business_session, question, run, answer, external_job])
        session.commit()

    created = service.capture_run("run-1")

    episode = next(item for item in created if item["kind"] == "episodic")
    assert episode["metadata"]["runId"] == "run-1"
    assert episode["channel"] == "feishu"
    assert episode["metadata"]["externalTenantKey"] == "tenant-1"
    assert "测试工序良率最低" in episode["content"]
    assert "rows" not in episode["content"]


def test_only_explicit_analysis_preferences_are_extracted():
    assert service.extract_explicit_preferences("你好") == []
    assert service.extract_explicit_preferences("我的身份证是123") == []
    assert service.extract_explicit_preferences("以后良率趋势默认用折线图")


def test_expired_running_memory_job_is_reclaimed(monkeypatch):
    factory = _factory(monkeypatch)
    with factory() as session:
        session.add(
            AgentMemoryJob(
                id="mjob-expired",
                job_type="consolidate",
                reference_id="tenant-default",
                status="running",
                attempt_count=1,
                max_attempts=3,
                lease_owner="dead-worker",
                lease_expires_at=datetime.now() - timedelta(seconds=1),
            )
        )
        session.commit()

    assert service.claim_next_job("recovery-worker") == "mjob-expired"
    with factory() as session:
        row = session.get(AgentMemoryJob, "mjob-expired")
        assert row.status == "running"
        assert row.attempt_count == 2
        assert row.lease_owner == "recovery-worker"
