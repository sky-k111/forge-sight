from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.ids import new_id
from app.db.models import (
    AnalysisMessage,
    AnalysisRun,
    AnalysisSession,
    AnalysisSkill,
    AnalysisSkillUsage,
    Base,
    DataSource,
)
from app.skills import seed, service


def _session_factory(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(service, "PlatformSession", factory)
    monkeypatch.setattr(seed, "PlatformSession", factory)
    with factory() as session:
        session.add(
            DataSource(
                name="制造测试库",
                type="mysql",
                host="127.0.0.1",
                port=3306,
                db_name="manufacturing",
                username="reader",
                status="连接正常",
                is_default=True,
            )
        )
        session.commit()
    return factory


def test_feature_flag_is_off_by_default_and_can_be_enabled(monkeypatch):
    _session_factory(monkeypatch)

    assert service.get_settings()["enabled"] is False
    changed = service.update_settings(True, updated_by="admin")

    assert changed["enabled"] is True
    assert changed["updatedBy"] == "admin"


def test_only_published_source_safe_skills_are_retrieved(monkeypatch):
    factory = _session_factory(monkeypatch)
    seed.seed_analysis_skills()
    service.update_settings(True, updated_by="admin")
    with factory() as session:
        source_id = session.query(DataSource.id).scalar()
        session.add(
            AnalysisSkill(
                id=new_id("skill"),
                source_id=source_id + 1,
                name="另一工厂私有良率流程",
                slug="foreign-yield",
                topic="质量分析",
                status="published",
                trigger_json={"terms": ["良率下降"]},
                procedure_json=["不应跨数据源召回"],
            )
        )
        session.commit()

    result = service.retrieve_skills("最近良率为什么下降", source_id)

    assert result["enabled"] is True
    assert result["variant"] == "on"
    assert result["skills"]
    assert all(item["name"] != "另一工厂私有良率流程" for item in result["skills"])


def test_positive_feedback_creates_reviewable_candidate(monkeypatch):
    factory = _session_factory(monkeypatch)
    with factory() as session:
        source_id = session.query(DataSource.id).scalar()
        business_session = AnalysisSession(
            id=new_id("ses"),
            title="良率调查",
            source_id=source_id,
            user_id="user-1",
        )
        message = AnalysisMessage(
            id=new_id("msg"),
            session_id=business_session.id,
            role="user",
            content="分析最近良率下降的原因",
            sequence_no=1,
        )
        run = AnalysisRun(
            id=new_id("run"),
            session_id=business_session.id,
            trigger_message_id=message.id,
            source_id=source_id,
            status="succeeded",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        message.run_id = run.id
        session.add_all([business_session, message, run])
        session.commit()
        run_id = run.id

    result = service.record_feedback(
        run_id,
        user_id="user-1",
        rating="helpful",
        comment="这个排查顺序有效",
    )

    assert result["candidateSkillId"]
    with factory() as session:
        candidate = session.query(AnalysisSkill).filter_by(id=result["candidateSkillId"]).one()
        assert candidate.status == "candidate"
        assert run_id in candidate.evidence_run_ids_json


def test_usage_outcome_is_closed_with_run(monkeypatch):
    factory = _session_factory(monkeypatch)
    with factory() as session:
        session.add(
            AnalysisSkillUsage(
                id=new_id("sku"),
                run_id="run-1",
                task_id="task-1",
                skill_id="skill-1",
                relevance_score=0.9,
            )
        )
        session.commit()

    service.finish_run_usage("run-1", "succeeded")

    with factory() as session:
        row = session.query(AnalysisSkillUsage).one()
        assert row.outcome == "succeeded"
        assert row.finished_at is not None
