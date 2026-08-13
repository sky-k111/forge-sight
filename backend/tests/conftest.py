"""后端测试共享夹具。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import service as analysis_service
from app.agent import usage as usage_service
from app.agent import runner as runner_service
from app.config import settings
from app.db.models import Base, DataSource, DataTable, TableField, TableRelation
from app.knowledge import seed, service
from app.retrieval import service as retrieval_service


@pytest.fixture(autouse=True)
def explicit_test_demo_mode(monkeypatch):
    """Existing API tests intentionally exercise the documented local demo mode."""
    monkeypatch.setattr(settings, "auth_email_gate_enabled", False)
    monkeypatch.setattr(settings, "auth_email_gate_allow_capture", True)
    monkeypatch.setattr(settings, "demo_mode_enabled", True)


@pytest.fixture()
def knowledge_session(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(service, "PlatformSession", session_factory)
    monkeypatch.setattr(seed, "PlatformSession", session_factory)
    monkeypatch.setattr(service, "write_audit", lambda **_kwargs: None)
    monkeypatch.setattr(retrieval_service, "PlatformSession", session_factory)
    monkeypatch.setattr(retrieval_service, "write_audit", lambda **_kwargs: None)

    table_fields = {
        "mes_process_output": ["stat_date", "process_id", "good_qty", "defect_qty", "input_qty", "rework_qty"],
        "qms_inspection": ["inspection_date", "defect_qty", "sample_qty"],
        "qms_defect_detail": ["defect_type", "defect_qty"],
        "eqp_downtime_record": ["start_time", "downtime_minutes", "is_planned"],
        "inv_inventory_snapshot": ["snapshot_date", "available_qty", "safety_stock_qty"],
        "dim_process": ["process_id", "process_name"],
    }
    table_names = {"mes_process_output": "工序产量事实表", "dim_process": "工序主数据"}
    field_names = {
        "stat_date": "统计日期",
        "process_id": "工序ID",
        "process_name": "工序名称",
        "good_qty": "合格数量",
        "defect_qty": "不良数量",
        "input_qty": "投入数量",
        "rework_qty": "返工数量",
        "inspection_date": "检验日期",
        "sample_qty": "抽检数量",
        "start_time": "停机开始时间",
        "downtime_minutes": "停机时长",
        "is_planned": "是否计划停机",
        "snapshot_date": "库存快照日期",
        "available_qty": "可用库存",
        "safety_stock_qty": "安全库存",
        "defect_type": "不良类型",
    }
    with session_factory() as session:
        source = DataSource(
            name="测试制造数据底座",
            type="mysql",
            host="127.0.0.1",
            port=3306,
            db_name="test_manufacturing",
            username="reader",
            status="连接正常",
            is_default=True,
        )
        session.add(source)
        session.flush()
        for table_name, fields in table_fields.items():
            session.add(
                DataTable(
                    source_id=source.id,
                    name=table_name,
                    cn_name=table_names.get(table_name, table_name),
                    topic="测试",
                    registered=True,
                )
            )
            for ordinal, field_name in enumerate(fields, start=1):
                session.add(
                    TableField(
                        source_id=source.id,
                        table_name=table_name,
                        name=field_name,
                        cn_name=field_names.get(field_name, field_name),
                        data_type="date" if "date" in field_name or "time" in field_name else "decimal",
                        ordinal=ordinal,
                        is_primary=field_name == "process_id" and table_name == "dim_process",
                    )
                )
        session.add(
            TableRelation(
                source_id=source.id,
                from_table="mes_process_output",
                from_field="process_id",
                to_table="dim_process",
                to_field="process_id",
                source="扫描",
            )
        )
        session.commit()
    seed.seed_knowledge()
    return session_factory


@pytest.fixture()
def analysis_session(monkeypatch):
    """阶段 5 Session/Run/Event/Usage 的隔离平台库。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(analysis_service, "PlatformSession", session_factory)
    monkeypatch.setattr(usage_service, "PlatformSession", session_factory)
    monkeypatch.setattr(runner_service, "PlatformSession", session_factory)
    with session_factory() as session:
        session.add(
            DataSource(
                name="阶段5测试数据源",
                type="mysql",
                host="127.0.0.1",
                port=3306,
                db_name="phase5_test",
                username="reader",
                status="连接正常",
                is_default=True,
            )
        )
        session.commit()
    return session_factory


@pytest.fixture()
def modeling_session(monkeypatch, tmp_path):
    """建模服务隔离环境：SQLite 平台库 + 假取数 + 临时模型目录。"""
    import numpy as np
    import pandas as pd

    from app.config import settings
    from app.gateway.executor import ExecutionResult
    from app.modeling import service as modeling_service

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(modeling_service, "PlatformSession", session_factory)
    monkeypatch.setattr(modeling_service, "write_audit", lambda **_kwargs: None)
    monkeypatch.setattr(settings, "modeling_storage_dir", str(tmp_path))

    def _make_rows(n=240):
        rng = np.random.default_rng(7)
        df = pd.DataFrame(
            {
                "工序": rng.choice(["SMT贴片", "AOI检测", "功能测试"], size=n),
                "产线": rng.choice(["L01", "L04"], size=n),
                "停机时长": rng.integers(0, 600, size=n).astype(float),
                "计划数量": rng.integers(100, 5000, size=n).astype(float),
            }
        )
        df["high_defect_risk"] = (
            (df["停机时长"] > 300).astype(int)
            | rng.choice([0, 1], size=n, p=[0.85, 0.15])
        ).astype(int)
        return df

    def fake_execute_readonly(sql, action="", user_name="", source=None):
        df = _make_rows()
        return ExecutionResult(
            ok=True,
            columns=list(df.columns),
            rows=df.values.tolist(),
            row_count=len(df),
            truncated=False,
            tables=["mes_process_output"],
        )

    monkeypatch.setattr(modeling_service, "execute_readonly", fake_execute_readonly)
    return session_factory
