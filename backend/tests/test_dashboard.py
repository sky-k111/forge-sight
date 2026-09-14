"""Personal dashboards must not weaken the governed query boundary."""

from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.dashboard import service
from app.dashboard.schemas import DashboardConfig, Layout, Widget, WidgetQuery
from app.db.models import DataSource, KnowledgeMetric, MetricSourceBinding
from app.gateway.executor import ExecutionResult


@pytest.fixture
def dashboard_session(knowledge_session, monkeypatch):
    monkeypatch.setattr(service, "PlatformSession", knowledge_session)
    return knowledge_session


def test_query_definition_rejects_sql_and_duplicate_widgets():
    with pytest.raises(ValidationError):
        WidgetQuery(sourceId=1, metricId="M-001", kind="kpi", sql="SELECT 1")
    with pytest.raises(ValidationError):
        DashboardConfig(widgets=[Widget(id="same", kind="recent"), Widget(id="same", kind="recent")])

    with pytest.raises(ValueError):
        DashboardConfig(widgets=[Widget(id="recent-a", kind="recent"), Widget(id="recent-b", kind="recent")])

    with pytest.raises(ValueError):
        Widget.model_validate({"id": "bad-color", "kind": "recent", "appearance": {"accent": "red"}})
    with pytest.raises(ValueError):
        Widget.model_validate({"id": "bad-chart", "kind": "trend", "metricId": "M-001", "appearance": {"chartType": "pie"}})
    with pytest.raises(ValidationError):
        Layout(x=10, w=3)


def test_period_has_explicit_source_latest_anchor():
    assert service.period_bounds("last7", date(2026, 6, 30)) == ("2026-06-24", "2026-07-01")
    assert service.period_bounds("all", date(2026, 6, 30)) == (None, None)


def test_layout_is_isolated_and_optimistically_versioned(dashboard_session):
    first = service.load_config(1, "tenant-default", "alice")
    assert first["defaultConfig"]["version"] == 0
    config = DashboardConfig.model_validate(first["config"])
    config.showContext = False
    config.widgets[0].appearance.accent = "violet"
    trend_widget = next(item for item in config.widgets if item.kind == "trend")
    trend_widget.appearance.chartType = "area"
    saved = service.save_config(1, "tenant-default", "alice", config)
    assert saved["version"] == 1
    assert saved["showContext"] is False
    assert service.load_config(1, "tenant-default", "alice")["config"]["widgets"][0]["appearance"]["accent"] == "violet"
    loaded_widgets = service.load_config(1, "tenant-default", "alice")["config"]["widgets"]
    assert next(item for item in loaded_widgets if item["kind"] == "trend")["appearance"]["chartType"] == "area"
    assert service.load_config(1, "tenant-default", "alice")["defaultConfig"]["version"] == 1
    assert service.load_config(1, "tenant-default", "bob")["config"]["version"] == 0
    with pytest.raises(HTTPException) as error:
        service.save_config(1, "tenant-default", "alice", config)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        service.load_config(1, "other-tenant", "alice")
    assert error.value.status_code == 404


def test_unpublished_metric_never_executes(dashboard_session, monkeypatch):
    with dashboard_session() as session:
        session.query(KnowledgeMetric).filter_by(id="M-001").update({"status": "待审核"})
        session.commit()
    monkeypatch.setattr(service, "execute_readonly", lambda **_: pytest.fail("unpublished query executed"))
    result = service.query_widget(WidgetQuery(sourceId=1, metricId="M-001", kind="kpi"), "tenant-default", "alice")
    assert result["state"] == "unavailable"


def test_query_uses_bound_metric_and_no_llm(dashboard_session, monkeypatch):
    calls = []

    def execute(sql, **kwargs):
        calls.append((sql, kwargs))
        if "MAX(" in sql:
            return ExecutionResult(ok=True, columns=["latest"], rows=[[date(2026, 6, 30)]], row_count=1)
        return ExecutionResult(ok=True, columns=["时间", "产量"], rows=[["2026-06-30", 42]], row_count=1)

    monkeypatch.setattr(service, "execute_readonly", execute)
    result = service.query_widget(WidgetQuery(sourceId=1, metricId="M-001", kind="trend", period="last7"), "tenant-default", "alice")
    assert result["state"] == "ready"
    assert "2026-06-24" in calls[-1][0]
    assert "SUM(good_qty + defect_qty)" in calls[-1][0]
    assert calls[-1][1]["source"].id == 1
    assert result["range"]["basis"] == "source_latest"


def test_rank_dimension_is_bound_from_source_catalog_without_llm(dashboard_session, monkeypatch):
    calls = []

    def execute(sql, **kwargs):
        calls.append(sql)
        if "MAX(" in sql:
            return ExecutionResult(ok=True, columns=["latest"], rows=[[date(2026, 6, 30)]], row_count=1)
        return ExecutionResult(ok=True, columns=["工序", "产量"], rows=[["P01", 42]], row_count=1)

    monkeypatch.setattr(service, "execute_readonly", execute)
    request = WidgetQuery(sourceId=1, metricId="M-001", kind="rank", dimension="工序")
    result = service.query_widget(request, "tenant-default", "alice")
    assert result["state"] == "ready"
    assert "process_id" in calls[-1]


def test_empty_and_failure_are_not_fabricated_as_zero(dashboard_session, monkeypatch):
    monkeypatch.setattr(service, "execute_readonly", lambda *_, **__: ExecutionResult(ok=True, rows=[[None]], columns=["latest"]))
    request = WidgetQuery(sourceId=1, metricId="M-001", kind="kpi")
    assert service.query_widget(request, "tenant-default", "alice")["state"] == "empty"
    monkeypatch.setattr(service, "execute_readonly", lambda *_, **__: ExecutionResult(ok=False, error="sensitive driver data"))
    result = service.query_widget(request, "tenant-default", "alice")
    assert result["state"] == "error"
    assert "sensitive" not in str(result)


def test_source_revalidated_after_layout_save(dashboard_session):
    result = service.load_config(1, "tenant-default", "alice")
    service.save_config(1, "tenant-default", "alice", DashboardConfig.model_validate(result["config"]))
    with dashboard_session() as session:
        session.query(DataSource).filter_by(id=1).update({"tenant_id": "other"})
        session.commit()
    with pytest.raises(HTTPException):
        service.load_config(1, "tenant-default", "alice")


def test_unverified_binding_removed_from_catalog(dashboard_session):
    with dashboard_session() as session:
        session.query(MetricSourceBinding).filter_by(metric_id="M-001").update({"status": "待验证"})
        session.commit()
    assert "M-001" not in {item["id"] for item in service.load_config(1, "tenant-default", "alice")["catalog"]}


def test_query_rejects_unregistered_dimension_and_unsupported_kind(dashboard_session, monkeypatch):
    monkeypatch.setattr(service, "execute_readonly", lambda *_, **__: pytest.fail("invalid widget executed"))
    request = WidgetQuery(sourceId=1, metricId="M-001", kind="rank", dimension="客户密钥")
    assert service.query_widget(request, "tenant-default", "alice")["state"] == "unavailable"

    with dashboard_session() as session:
        session.query(MetricSourceBinding).filter_by(metric_id="M-001").update({"time_field": ""})
        session.commit()
    request = WidgetQuery(sourceId=1, metricId="M-001", kind="kpi")
    assert service.query_widget(request, "tenant-default", "alice")["state"] == "unavailable"
    catalog = service.load_config(1, "tenant-default", "alice")["catalog"]
    assert next(item for item in catalog if item["id"] == "M-001")["kinds"] == []
