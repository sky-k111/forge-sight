import json
from types import SimpleNamespace

import pytest

from app.knowledge import candidates, service
from app.db.models import MetricFieldBinding, MetricSourceBinding, MetricTableBinding


def _proposal(*, field_name="downtime_minutes", confidence=0.88):
    return {
        "candidates": [
            {
                "kind": "new_metric",
                "name": "平均停机时长",
                "topic": "设备分析",
                "definition": "统计周期内单次停机记录的平均分钟数。",
                "formula": "AVG(停机时长)",
                "physicalFormula": f"AVG({field_name})",
                "sourceTables": ["eqp_downtime_record"],
                "semanticTimeField": "停机开始时间",
                "bindingTimeField": "start_time",
                "dimensions": ["设备", "产线"],
                "synonyms": ["平均故障停机时间"],
                "fieldMappings": [
                    {
                        "semanticField": "停机时长",
                        "tableName": "eqp_downtime_record",
                        "fieldName": field_name,
                    },
                    {
                        "semanticField": "停机开始时间",
                        "tableName": "eqp_downtime_record",
                        "fieldName": "start_time",
                    },
                ],
                "precision": "分钟 · 2 位小数",
                "confidence": confidence,
                "reason": "eqp_downtime_record 包含 downtime_minutes 和 start_time。",
            }
        ]
    }


def _configure(monkeypatch, knowledge_session, proposal):
    monkeypatch.setattr(candidates, "PlatformSession", knowledge_session)
    monkeypatch.setattr(candidates, "write_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        candidates,
        "chat_completion",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=json.dumps(proposal, ensure_ascii=False),
            provider_request_id="req-test",
            model="test-model",
        ),
    )


def test_candidate_acceptance_keeps_publish_gate(monkeypatch, knowledge_session):
    _configure(monkeypatch, knowledge_session, _proposal())
    source_id = service.list_metrics()[0]["sourceId"]

    generated = candidates.generate_candidates(source_id)

    assert generated["generated"] == 1
    candidate = generated["items"][0]
    assert candidate["validation"]["valid"] is True
    assert candidate["confidence"] == 88

    accepted = candidates.accept_candidate(candidate["id"])

    assert accepted["requiresPublish"] is True
    assert accepted["metric"]["status"] == "待审核"
    assert accepted["metric"]["bindingStatus"] == "待验证"
    assert accepted["metric"]["availableForAnalysis"] is False
    assert candidates.list_candidates(source_id)[0]["status"] == "accepted"


def test_invalid_physical_field_is_persisted_for_review_but_cannot_be_accepted(
    monkeypatch, knowledge_session
):
    _configure(monkeypatch, knowledge_session, _proposal(field_name="invented_minutes", confidence=0.99))
    source_id = service.list_metrics()[0]["sourceId"]

    candidate = candidates.generate_candidates(source_id)["items"][0]

    assert candidate["validation"]["valid"] is False
    assert candidate["confidence"] == 49
    assert any("不存在" in error for error in candidate["validation"]["errors"])
    with pytest.raises(service.KnowledgeValidation, match="未通过自动校验"):
        candidates.accept_candidate(candidate["id"])


def test_existing_metric_binding_candidate_does_not_bypass_binding_validation(
    monkeypatch, knowledge_session
):
    source_id = service.list_metrics()[0]["sourceId"]
    with knowledge_session() as session:
        binding = session.query(MetricSourceBinding).filter_by(metric_id="M-009", source_id=source_id).one()
        session.query(MetricFieldBinding).filter_by(binding_id=binding.id).delete()
        session.query(MetricTableBinding).filter_by(binding_id=binding.id).delete()
        session.delete(binding)
        session.commit()
    proposal = _proposal()
    proposal["candidates"][0].update(
        {
            "kind": "source_binding",
            "targetMetricId": "M-009",
            "name": "设备停机时长",
            "formula": "SUM(停机时长)",
            "physicalFormula": "SUM(downtime_minutes)",
        }
    )
    _configure(monkeypatch, knowledge_session, proposal)

    generated = candidates.generate_candidates(source_id)["items"][0]
    accepted = candidates.accept_candidate(generated["id"])

    assert generated["kind"] == "source_binding"
    assert accepted["metric"]["id"] == "M-009"
    assert accepted["metric"]["bindingStatus"] == "待验证"
    assert accepted["metric"]["availableForAnalysis"] is False
