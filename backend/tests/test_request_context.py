import json

from app.agent.intent_contract import AgentIntent
from app.agent.llm_intent import resolve_intent
from app.agent.request_context import (
    apply_presentation_change,
    merge_intent,
    presentation_reuse_task,
    successful_snapshot,
)
from app.agent.query_requirements import dimension_outputs, validate_result
from app.agent.intent_adapter import intent_to_tasks


def previous():
    return {"runId": "r1", "sourceId": 1, "agentIntent": AgentIntent.model_validate({
        "kind": "analysis", "question": "今年各月产量", "metrics": [{"semantic": "产量"}],
        "dimensions": [{"semantic": "时间", "grain": "month"}],
        "time": {"mode": "absolute", "start": "2026-01-01", "end": "2027-01-01", "reference": "now"},
    }).model_dump(mode="json")}


def test_add_dimension_preserves_absolute_time_and_month():
    intent = AgentIntent(kind="analysis", question="按车间拆开", relation="refine_previous", operation="add_dimension", changedFields=["dimensions"], dimensions=[{"semantic": "车间"}])
    merged = merge_intent(intent, previous())
    task = intent_to_tasks(merged)[0]
    assert task["groupBy"] == ["月份", "车间"]
    assert task["timeConstraint"]["periods"][0]["startInclusive"] == "2026-01-01"
    assert task["metricHint"] == "产量"


def test_replace_and_presentation_do_not_drop_unmentioned_fields():
    intent = AgentIntent(kind="analysis", question="改按车间", relation="refine_previous", operation="replace_dimension", dimensions=[{"semantic": "车间"}])
    assert [d.semantic for d in merge_intent(intent, previous()).dimensions] == ["车间"]
    intent = AgentIntent(kind="analysis", question="换成柱状图", relation="refine_previous", operation="change_presentation", changedFields=["chart"], chart={"type": "bar"})
    merged = merge_intent(intent, previous())
    assert merged.chart.type == "bar"
    assert merged.dimensions[0].grain == "month"


def test_metric_revision_preserves_explicit_dimension_qualified_name():
    prior = {"runId": "r-old", "agentIntent": AgentIntent.model_validate({
        "kind": "analysis", "question": "各工序不良数量",
        "metrics": [{"semantic": "不良数量"}], "dimensions": [{"semantic": "工序"}],
    }).model_dump(mode="json")}
    intent = AgentIntent.model_validate({
        "kind": "analysis", "question": "刚才这个改成工序不良率",
        "relation": "refine_previous", "operation": "query", "changedFields": ["metrics"],
        "metrics": [{"semantic": "不良率"}],
    })

    merged = merge_intent(intent, prior)

    assert merged.metrics[0].semantic == "工序不良率"
    assert merged.dimensions[0].semantic == "工序"


CTX = {"source": {"type": "mysql"}, "tables": [{"name": "fact", "fields": [{"name": "workshop_id", "semanticName": "车间"}]}]}
TASK = {"groupBy": ["车间"]}


def test_ast_accepts_alias_position_cte_and_detail():
    for sql in ["SELECT workshop_id AS workshop, SUM(qty) AS qty FROM fact GROUP BY workshop",
                "SELECT workshop_id AS workshop, SUM(qty) AS qty FROM fact GROUP BY 1",
                "WITH x AS (SELECT workshop_id AS workshop, SUM(qty) AS qty FROM fact GROUP BY workshop_id) SELECT workshop, qty FROM x",
                "SELECT workshop_id AS workshop, qty FROM fact"]:
        _, errors = dimension_outputs(sql, TASK, CTX)
        assert not errors, (sql, errors)
        assert not validate_result(sql, TASK, CTX, {"columns": ["workshop", "qty"], "rows": [["一车间", 10]]})


def test_ast_rejects_total_comment_and_missing_result_column():
    for sql in ["SELECT SUM(qty) AS qty FROM fact /* GROUP BY workshop_id */",
                "SELECT SUM(workshop_id) AS workshop FROM fact"]:
        assert dimension_outputs(sql, TASK, CTX)[1]
    assert validate_result("SELECT workshop_id, SUM(qty) FROM fact GROUP BY workshop_id", TASK, CTX, {"columns": ["qty"]})


def test_parser_repairs_once_and_reports_failure():
    calls = []
    def generate(*args):
        calls.append(args[1])
        return "invalid"
    intent, producer, error = resolve_intent("按车间拆开", generate_text=generate)
    assert calls == ["intent.parse", "intent.repair"]
    assert intent.kind.value == "clarify" and error


def test_multi_result_followup_requires_target_and_uses_per_result_intent():
    prior = {"runId": "r1", "results": [
        {"resultId": "R1", "agentIntent": {"kind": "analysis", "question": "良率", "metrics": [{"semantic": "良率"}]}},
        {"resultId": "R2", "agentIntent": {"kind": "analysis", "question": "产量", "metrics": [{"semantic": "产量"}]}},
    ]}
    ambiguous = AgentIntent(kind="analysis", question="换成折线图", relation="refine_previous",
                            operation="change_presentation", chart={"type": "line"})
    assert merge_intent(ambiguous, prior).kind.value == "clarify"
    selected = ambiguous.model_copy(update={"targetResultId": "R2"})
    merged = merge_intent(selected, prior)
    assert merged.metrics[0].semantic == "产量"
    assert merged.targetResultId == "R2"


def test_success_snapshot_keeps_each_result_semantics():
    snapshot = successful_snapshot({"run_id": "run-1", "source_id": 1,
        "agent_intent": {"kind": "analysis", "question": "复合", "stepsHint": []},
        "task_results": [
            {"task": {"requirementId": "R1", "title": "良率", "semanticIntent": {"kind": "analysis", "question": "良率", "metrics": [{"semantic": "良率"}]}}, "execution": {"columns": ["良率"]}},
            {"task": {"requirementId": "R2", "title": "产量", "semanticIntent": {"kind": "analysis", "question": "产量", "metrics": [{"semantic": "产量"}]}}, "execution": {"columns": ["产量"]}},
        ]})
    assert snapshot["version"] == 2
    assert snapshot["results"][0]["agentIntent"]["metrics"][0]["semantic"] == "良率"
    assert snapshot["results"][1]["agentIntent"]["metrics"][0]["semantic"] == "产量"
    assert snapshot["agentIntent"] == snapshot["results"][1]["agentIntent"]


def test_success_snapshot_is_json_serializable_with_database_types():
    import json
    from datetime import date, datetime
    from decimal import Decimal

    snapshot = successful_snapshot({
        "run_id": "run-decimal", "source_id": 1,
        "agent_intent": {"kind": "analysis", "question": "产量"},
        "task_results": [{
            "task": {"requirementId": "R1"},
            "execution": {"columns": ["日期", "产量"], "rows": [[date(2026, 6, 1), Decimal("12.50")]]},
            "interpretation": {"generatedAt": datetime(2026, 6, 1, 8, 0)},
        }],
    })
    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert "2026-06-01" in encoded
    assert "12.5" in encoded


def test_presentation_followup_reuses_selected_result_without_changing_data():
    prior = {
        "results": [{
            "resultId": "R2",
            "sql": "SELECT month, SUM(output_qty) AS output_qty FROM production GROUP BY month",
            "task": {"title": "月度产量", "groupBy": ["月份"]},
            "metricResolution": {"name": "产量"},
            "interpretation": {
                "chart": {
                    "type": "bar",
                    "xKey": "month",
                    "bars": [{"key": "output_qty", "name": "产量"}],
                    "data": [{"month": "6月", "output_qty": 100}],
                },
                "charts": [],
            },
        }]
    }
    intent = AgentIntent.model_validate({
        "kind": "analysis",
        "question": "把第二张改成折线图",
        "relation": "refine_previous",
        "operation": "change_presentation",
        "changedFields": ["chart"],
        "targetResultId": "R2",
        "chart": {"type": "line"},
    })

    task = presentation_reuse_task(intent, prior)
    changed = apply_presentation_change(task)

    assert task["presentationReuse"]["sql"].startswith("SELECT month")
    assert changed["chart"]["type"] == "line"
    assert changed["chart"]["series"] == [{"key": "output_qty", "name": "产量"}]
    assert changed["chart"]["data"] == [{"month": "6月", "output_qty": 100}]
    assert changed["presentationOnly"] is True


def test_presentation_reuse_keeps_execution_and_builds_donut_total():
    prior = {"results": [{
        "resultId": "R1",
        "sql": "SELECT line, SUM(qty) AS qty FROM fact GROUP BY line",
        "task": {"title": "产线产量", "groupBy": ["产线"]},
        "execution": {"columns": ["line", "qty"], "rows": [["L1", 10], ["L2", 20]], "row_count": 2},
        "interpretation": {"chart": {"type": "bar", "xKey": "line", "xAxis": {"kind": "category"},
                                     "bars": [{"key": "qty", "name": "产量"}],
                                     "data": [{"line": "L1", "qty": 10}, {"line": "L2", "qty": 20}]}}
    }]}
    intent = AgentIntent.model_validate({
        "kind": "analysis", "question": "换成环形图", "relation": "refine_previous",
        "operation": "change_presentation", "changedFields": ["chart"], "chart": {"type": "donut"},
    })
    task = presentation_reuse_task(intent, prior)
    changed = apply_presentation_change(task)
    assert task["presentationReuse"]["execution"]["rows"] == [["L1", 10], ["L2", 20]]
    assert changed["chart"]["data"] == [{"name": "L1", "value": 10}, {"name": "L2", "value": 20}]
    assert changed["chart"]["centerValue"] == 30


def test_multi_chart_reuse_rejects_partial_conversion():
    prior = {"results": [{"resultId": "R1", "task": {}, "interpretation": {
        "charts": [
            {"type": "bar", "xKey": "line", "bars": [{"key": "qty"}], "data": [{"line": "L1", "qty": 10}]},
            {"type": "scatter", "xNumericKey": "x", "yNumericKey": "y", "data": [{"x": 1, "y": 2}]},
        ]
    }}]}
    intent = AgentIntent.model_validate({
        "kind": "analysis", "question": "都换成折线图", "relation": "refine_previous",
        "operation": "change_presentation", "changedFields": ["chart"], "chart": {"type": "line"},
    })
    assert presentation_reuse_task(intent, prior) is None
