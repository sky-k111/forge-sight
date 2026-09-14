import json

from app.agent.intent_contract import AgentIntent
from app.agent.intent_adapter import intent_to_tasks
from app.agent.llm_intent import resolve_intent


def test_compound_requirements_keep_separate_metrics_time_and_charts():
    intent = AgentIntent.model_validate({
        "kind": "analysis", "question": "比较良率，并统计逐周产量",
        "stepsHint": [
            {"kind": "analysis", "metrics": [{"semantic": "良率", "aggregation": "rate"}],
             "dimensions": [{"semantic": "时间", "grain": "month"}],
             "time": {"mode": "absolute", "start": "2026-06-01", "end": "2026-08-01"},
             "chart": {"type": "bar"}},
            {"kind": "analysis", "metrics": [{"semantic": "产量"}],
             "dimensions": [{"semantic": "时间", "grain": "week"}],
             "time": {"mode": "absolute", "start": "2026-06-01", "end": "2026-07-01"},
             "chart": {"type": "line"}},
        ],
    })
    tasks = intent_to_tasks(intent)
    assert len(tasks) == 2
    assert [t["metricHint"] for t in tasks] == ["良率", "产量"]
    assert [t["groupBy"] for t in tasks] == [["月份"], ["周"]]
    assert [t["requestedChart"] for t in tasks] == ["bar", "line"]
    assert tasks[1]["timeConstraint"]["periods"][0]["endExclusive"] == "2026-07-01"
    assert "良率" in tasks[0]["normalizedQuestion"] and "产量" not in tasks[0]["normalizedQuestion"]
    assert "产量" in tasks[1]["normalizedQuestion"] and "良率" not in tasks[1]["normalizedQuestion"]
    assert all(task["originalQuestion"] == intent.question for task in tasks)


def test_compound_business_and_clock_steps_keep_business_tasks_and_mixed_periods():
    from app.agent.time_resolution import resolve_time_constraint

    intent = AgentIntent.model_validate({
        "kind": "analysis",
        "question": "分析最近有数7天的良率，和6月第一周比较，并输出6月逐周产量，再告诉我几点了",
        "stepsHint": [
            {
                "kind": "analysis",
                "metrics": [{"semantic": "良率", "aggregation": "rate"}],
                "dimensions": [{"semantic": "时间", "grain": "day"}],
                "time": {"mode": "recent", "amount": 7, "unit": "day", "grain": "day",
                         "reference": "data_horizon", "label": "最近有数据的7天"},
                "comparison": {"basis": "两个周期良率对比", "periods": [
                    {"mode": "absolute", "grain": "week", "reference": "data_horizon", "label": "6月第一周"},
                ]},
                "chart": {"type": "bar"},
            },
            {
                "kind": "analysis",
                "metrics": [{"semantic": "产量", "aggregation": "sum"}],
                "dimensions": [{"semantic": "时间", "grain": "week"}],
                "time": {"mode": "absolute", "grain": "week", "reference": "data_horizon", "label": "6月逐周"},
                "chart": {"type": "donut"},
            },
            {"kind": "utility"},
        ],
    })

    tasks = intent_to_tasks(intent)
    assert len(tasks) == 2
    assert [task["metricHint"] for task in tasks] == ["良率", "产量"]
    assert [task["requestedChart"] for task in tasks] == ["bar", "donut"]
    assert "按日" not in tasks[0]["normalizedQuestion"]
    assert tasks[0]["groupBy"] == ["比较周期"]
    resolved = resolve_time_constraint(tasks[0]["timeConstraint"], "2026-07-31")
    assert [(p["startInclusive"], p["endExclusive"]) for p in resolved["periods"]] == [
        ("2026-07-25", "2026-08-01"),
        ("2026-06-01", "2026-06-08"),
    ]
    june = resolve_time_constraint(tasks[1]["timeConstraint"], "2026-07-31")
    assert june["periods"][0]["startInclusive"] == "2026-06-01"
    assert june["periods"][0]["endExclusive"] == "2026-07-01"


def test_explicit_multi_view_clause_is_bound_to_the_same_business_result():
    question = (
        "比较最近有数7天和6月第一周的良率，并输出6月逐周产量，"
        "分别输出饼状图和柱状图，再告诉我现在几点"
    )
    model_output = {
        "kind": "analysis",
        "question": question,
        "stepsHint": [
            {
                "kind": "analysis",
                "metrics": [{"semantic": "良率", "aggregation": "rate"}],
                "comparison": {"basis": "两个周期良率对比"},
                "chart": {"type": "bar"},
            },
            {
                "kind": "analysis",
                "metrics": [{"semantic": "产量", "aggregation": "sum"}],
                "dimensions": [{"semantic": "时间", "grain": "week"}],
                "time": {"mode": "absolute", "grain": "week", "label": "6月份"},
                "chart": {"type": "donut"},
            },
            {"kind": "utility"},
        ],
    }

    intent, producer, error = resolve_intent(
        question,
        generate_text=lambda *_args: json.dumps(model_output, ensure_ascii=False),
    )

    assert producer == "llm" and error is None
    production_views = [
        step.chart.type
        for step in intent.stepsHint
        if any(metric.semantic == "产量" for metric in step.metrics) and step.chart
    ]
    assert production_views == ["donut", "bar"]
    assert len([step for step in intent.stepsHint if step.kind.value == "analysis"]) == 3


def test_explicit_multi_view_reconciliation_does_not_duplicate_existing_views():
    question = "输出6月逐周产量，分别输出饼状图和柱状图"
    common = {
        "kind": "analysis",
        "dimensions": [{"semantic": "时间", "grain": "week"}],
        "time": {"mode": "absolute", "grain": "month", "label": "6月"},
    }
    model_output = {
        "kind": "analysis",
        "question": question,
        "stepsHint": [
            {**common, "metrics": [{"semantic": "产量", "aggregation": "sum"}], "chart": {"type": "bar"}},
            {**common, "metrics": [{"semantic": "产量", "aggregation": "share"}], "chart": {"type": "donut"}},
        ],
    }

    intent, _, error = resolve_intent(
        question,
        generate_text=lambda *_args: json.dumps(model_output, ensure_ascii=False),
    )

    assert error is None
    assert len(intent.stepsHint) == 2
    assert {step.chart.type for step in intent.stepsHint if step.chart} == {"bar", "donut"}


def test_compiler_groups_heterogeneous_comparison_by_accepted_period_labels():
    from app.agent.query_compiler import compile_query

    metric = {
        "id": "M-004", "name": "良率",
        "physicalFormula": "SUM(good_qty) / NULLIF(SUM(total_qty), 0)",
        "physicalTimeField": "stat_date", "sourceTables": ["mes_output"],
    }
    context = {
        "source": {"type": "mysql"}, "metrics": [metric],
        "tables": [{"name": "mes_output", "objectType": "fact", "fields": [
            {"name": "stat_date"}, {"name": "good_qty"}, {"name": "total_qty"},
        ]}],
    }
    task = {
        "normalizedQuestion": "对比两个周期良率", "analysisType": "comparison",
        "groupBy": ["比较周期"],
        "timeConstraint": {"kind": "comparison", "periods": [
            {"label": "最近有数据的7天", "startInclusive": "2026-07-25", "endExclusive": "2026-08-01"},
            {"label": "6月第一周", "startInclusive": "2026-06-01", "endExclusive": "2026-06-08"},
        ]},
        "queryBinding": {"metric": metric, "factTable": "mes_output", "filters": [], "joins": []},
        "dimensionBindings": [{"semantic": "比较周期", "field": "stat_date", "table": "mes_output", "status": "bound"}],
    }

    sql, metadata = compile_query(context, task, "mysql")
    assert "CASE WHEN" in sql
    assert "最近有数据的7天" in sql and "6月第一周" in sql
    assert "GROUP BY CASE WHEN" in sql
    assert metadata["axis"] == {"key": "比较周期", "kind": "category", "grain": None}


def test_provider_failure_is_not_user_ambiguity():
    def unavailable(*args):
        raise TimeoutError("timeout")
    intent, _, error = resolve_intent("统计产量", generate_text=unavailable)
    assert error
    assert intent.failure["code"] == "INTENT_SERVICE_UNAVAILABLE"
    assert intent.failure["retryable"] is True
    assert not intent.clarify


def test_partial_delivery_cannot_claim_complete():
    from app.agent.delivery import assess_delivery
    result = assess_delivery([{"id": "R1", "kind": "query", "taskRequirementId": "R1"},
                              {"id": "R2", "kind": "query", "taskRequirementId": "R2"}],
                             [{"task": {"requirementId": "R1"}}])
    assert result["missing"] == ["R2"]
    assert not result["complete"]


def test_comparison_filters_union_periods():
    from app.agent.query_compiler import _period_filter
    sql = _period_filter({"physicalTimeField": "day"}, [
        {"startInclusive": "2026-01-01", "endExclusive": "2026-02-01"},
        {"startInclusive": "2026-06-01", "endExclusive": "2026-07-01"},
    ], "mysql")
    assert " OR " in sql
    assert sql.count(" AND ") == 2


def test_quarter_boundaries_cross_year():
    from app.agent.time_resolution import resolve_time_constraint
    previous = resolve_time_constraint({"kind": "previous_quarter", "reference": "data_horizon"}, "2026-01-15")
    current = resolve_time_constraint({"kind": "current_quarter", "reference": "data_horizon"}, "2026-12-15")
    assert previous["periods"][0]["startInclusive"] == "2025-10-01"
    assert previous["periods"][0]["endExclusive"] == "2026-01-01"
    assert current["periods"][0]["endExclusive"] == "2027-01-01"


def test_relative_time_preserves_explicit_reference():
    intent = AgentIntent.model_validate({"kind": "analysis", "question": "上个月产量",
        "metrics": [{"semantic": "产量"}],
        "time": {"mode": "relative", "grain": "month", "reference": "now"}})
    assert intent_to_tasks(intent)[0]["timeConstraint"]["reference"] == "now"


def test_latest_snapshot_time_uses_data_horizon_instead_of_wall_clock():
    intent = AgentIntent.model_validate({
        "kind": "analysis", "question": "查最新库存",
        "metrics": [{"semantic": "低于安全库存"}],
        "time": {"mode": "current", "grain": "day", "label": "最新库存", "reference": "now"},
    })

    constraint = intent_to_tasks(intent)[0]["timeConstraint"]

    assert constraint["reference"] == "data_horizon"


def test_exact_published_metric_mention_outranks_broad_model_paraphrase():
    from app.agent.validators import resolve_metric

    context = {"metrics": [
        {"id": "inventory", "name": "库存量", "physicalFormula": "SUM(available_qty)"},
        {"id": "safety", "name": "低于安全库存", "physicalFormula": "available_qty < safety_stock_qty",
         "aliases": ["安全库存预警"]},
    ]}

    resolved = resolve_metric("库存量", context, "最新库存里哪些产品低于安全库存")

    assert resolved["id"] == "safety"


def test_metric_formula_can_satisfy_redundant_rule_filter_and_dimension_attributes():
    from app.agent.query_requirements import bind_query_requirements, query_contract_failures

    context = {
        "source": {"type": "mysql"},
        "metrics": [{
            "id": "M-011", "name": "低于安全库存", "definition": "可用库存低于安全库存",
            "semanticFormula": "可用库存 < 安全库存", "physicalFormula": "available_qty < safety_stock_qty",
            "physicalTimeField": "snapshot_date", "sourceTables": ["inventory"], "dimensions": ["产品"],
        }],
        "tables": [
            {"name": "inventory", "qualifiedName": "inventory", "objectType": "fact", "fields": [
                {"name": "product_id", "semanticName": "产品ID"}, {"name": "available_qty", "semanticName": "可用库存"},
                {"name": "safety_stock_qty", "semanticName": "安全库存"},
            ]},
            {"name": "product", "qualifiedName": "product", "objectType": "dimension", "fields": [
                {"name": "product_id", "semanticName": "产品ID"}, {"name": "product_name", "semanticName": "产品名称"},
                {"name": "product_code", "semanticName": "产品编码"},
            ]},
        ],
        "relations": [{"fromTable": "inventory", "fromField": "product_id",
                       "toTable": "product", "toField": "product_id"}],
    }
    task = bind_query_requirements({
        "metricHint": "低于安全库存", "groupBy": ["产品"],
        "filters": [{"field": "库存量", "operator": "lt", "value": "安全库存"}],
    }, context)
    sql = (
        "SELECT p.product_id, p.product_name, p.product_code, SUM(f.available_qty) AS available_qty "
        "FROM inventory f JOIN product p ON f.product_id=p.product_id "
        "GROUP BY p.product_id, p.product_name, p.product_code "
        "HAVING SUM(f.available_qty) < SUM(f.safety_stock_qty)"
    )

    assert task["filterBindings"][0]["status"] == "metric_formula"
    assert query_contract_failures(sql, task, context) == []


def test_recent_english_day_unit_resolves_to_exact_seven_day_window(monkeypatch):
    from datetime import date
    from app.agent import time_resolution

    monkeypatch.setattr(time_resolution, "reference_date", lambda _horizon=None: date(2026, 9, 10))
    resolved = time_resolution.resolve_time_constraint({
        "kind": "recent", "amount": 7, "unit": "day", "reference": "now",
        "label": "最近7天",
    })
    period = resolved["periods"][0]
    assert period["endExclusive"] == "2026-09-11"
    assert period["startInclusive"] == "2026-09-04"


def test_bare_month_and_month_end_are_canonicalized_by_calendar(monkeypatch):
    from datetime import date
    from app.agent import time_resolution

    monkeypatch.setattr(time_resolution, "reference_date", lambda _horizon=None: date(2026, 9, 10))
    bare = time_resolution.resolve_time_constraint({
        "kind": "absolute", "label": "6月份", "timeGrain": "month", "reference": "now",
    })
    wrong_end = time_resolution.resolve_time_constraint({
        "kind": "absolute", "label": "2026年6月", "timeGrain": "month", "reference": "now",
        "periods": [{"label": "2026年6月", "startInclusive": "2026-06-01", "endExclusive": "2026-06-30"}],
    })
    assert bare["periods"][0]["startInclusive"] == "2026-06-01"
    assert bare["periods"][0]["endExclusive"] == "2026-07-01"
    assert wrong_end["periods"][0]["endExclusive"] == "2026-07-01"

    comparison = time_resolution.resolve_time_constraint({
        "kind": "comparison", "timeGrain": "month", "reference": "now",
        "periods": [
            {"label": "2026年6月", "startInclusive": "2026-06-01", "endExclusive": "2026-06-30"},
            {"label": "2026年7月", "startInclusive": "2026-07-01", "endExclusive": "2026-07-31"},
        ],
    })
    assert [item["endExclusive"] for item in comparison["periods"]] == ["2026-07-01", "2026-08-01"]


def test_calendar_month_normalization_does_not_widen_an_explicit_day(monkeypatch):
    from datetime import date
    from app.agent import time_resolution

    monkeypatch.setattr(time_resolution, "reference_date", lambda _horizon=None: date(2026, 9, 10))
    current_calendar_month = time_resolution.resolve_time_constraint({
        "kind": "absolute", "label": "12月份", "timeGrain": "month", "reference": "now",
    })
    explicit_day = time_resolution.resolve_time_constraint({
        "kind": "comparison", "timeGrain": "month", "reference": "now",
        "periods": [{
            "label": "2026-06-15", "reference": "now",
            "startInclusive": "2026-06-15", "endExclusive": "2026-06-16",
        }],
    })
    assert current_calendar_month["periods"][0]["startInclusive"] == "2026-12-01"
    assert explicit_day["periods"][0]["startInclusive"] == "2026-06-15"
    assert explicit_day["periods"][0]["endExclusive"] == "2026-06-16"


def test_absolute_multi_month_range_is_not_collapsed_to_its_first_month():
    from app.agent.time_resolution import resolve_time_constraint

    result = resolve_time_constraint({
        "kind": "absolute", "label": "2026年6月-7月", "timeGrain": "month", "reference": "now",
        "periods": [{
            "label": "2026年6月-7月", "startInclusive": "2026-06-01", "endExclusive": "2026-08-01",
        }],
    })
    assert result["periods"][0]["startInclusive"] == "2026-06-01"
    assert result["periods"][0]["endExclusive"] == "2026-08-01"


def test_structured_metric_id_wins_without_reading_question_words():
    from app.agent.query_requirements import bind_query_requirements

    context = {
        "metrics": [
            {"id": "m-good", "name": "工序良率", "physicalFormula": "SUM(good)/SUM(total)",
             "sourceTables": ["fact"]},
            {"id": "m-bad", "name": "不良率", "physicalFormula": "SUM(bad)/SUM(total)",
             "sourceTables": ["fact"]},
        ],
        "tables": [{"name": "fact", "objectType": "fact", "fields": []}],
    }
    task = bind_query_requirements({
        "normalizedQuestion": "这句话故意不含任何指标名称",
        "metricHint": "工序良率",
        "semanticIntent": {"metrics": [{"id": "m-bad", "semantic": "不良率"}], "dimensions": []},
    }, context)
    assert task["queryBinding"]["metric"]["id"] == "m-bad"


def test_generic_yield_name_prefers_the_closest_canonical_metric():
    from app.agent.validators import resolve_metric

    context = {"metrics": [
        {"id": "m-good", "name": "工序良率", "physicalFormula": "SUM(good)/SUM(total)"},
        {"id": "m-bad", "name": "工序不良率", "physicalFormula": "SUM(bad)/SUM(total)"},
        {"id": "m-inspection", "name": "抽检不良率", "physicalFormula": "SUM(bad)/SUM(sample)"},
    ]}
    assert resolve_metric("良率", context, "对比6月和7月的良率")["id"] == "m-good"
    assert resolve_metric("不良率", context, "分析工序不良率")["id"] == "m-bad"
    assert resolve_metric("不良率", context, "分析不良率") is None


def test_validator_output_is_applied_to_the_current_task_before_binding():
    from app.agent.intent_contract import AgentIntent
    from app.agent.query_requirements import apply_resolved_intent, bind_query_requirements
    from app.agent.validators import validate_intent

    context = {
        "metrics": [{
            "id": "m-output", "name": "总产量", "aliases": ["产量"],
            "physicalFormula": "SUM(qty)", "sourceTables": ["fact_output"],
        }],
        "tables": [
            {"name": "fact_output", "objectType": "fact", "fields": [{"name": "qty"}]},
            {"name": "dim_line", "objectType": "dimension", "fields": [
                {"name": "line_code", "semanticName": "产线"},
            ]},
        ],
    }
    raw = AgentIntent.model_validate({
        "kind": "analysis", "question": "按产线统计产量",
        "metrics": [{"semantic": "产量"}],
        "dimensions": [{"semantic": "产线"}],
    })
    validation = validate_intent(raw, context)
    task = apply_resolved_intent({
        "metricHint": "产量", "groupBy": ["产线"],
        "semanticIntent": raw.model_dump(mode="json", by_alias=True),
    }, validation.resolved)
    bound = bind_query_requirements(task, context)

    assert validation.valid is True
    assert task["semanticIntent"]["metrics"][0]["id"] == "m-output"
    assert task["semanticIntent"]["dimensions"][0]["code"] == "line_code"
    assert bound["queryBinding"]["metric"]["id"] == "m-output"
    assert bound["dimensionBindings"][0]["table"] == "dim_line"


def test_filter_binding_uses_metadata_sample_shape_to_distinguish_id_from_name():
    from app.agent.query_requirements import bind_query_requirements

    context = {
        "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(qty)",
                     "sourceTables": ["fact_output"]}],
        "tables": [
            {"name": "fact_output", "objectType": "fact", "fields": [
                {"name": "product_id", "semanticName": "产品 ID", "sampleValue": "P001"},
                {"name": "qty", "semanticName": "数量", "sampleValue": 10},
            ]},
            {"name": "dim_product", "objectType": "dimension", "primaryKey": "product_id", "fields": [
                {"name": "product_id", "semanticName": "产品 ID", "sampleValue": "P001", "isPrimary": True},
                {"name": "product_code", "semanticName": "产品编码", "sampleValue": "FG-01-001"},
                {"name": "product_name", "semanticName": "产品名称", "sampleValue": "控制器01"},
            ]},
        ],
        "relations": [{"fromTable": "fact_output", "fromField": "product_id",
                       "toTable": "dim_product", "toField": "product_id"}],
    }
    task = bind_query_requirements({
        "metricHint": "产量", "filters": [{"field": "产品", "operator": "eq", "value": "P013"}],
        "semanticIntent": {"metrics": [{"semantic": "产量"}], "dimensions": []},
    }, context)
    assert task["filterBindings"][0]["field"] == "product_id"
    assert task["filterBindings"][0]["status"] == "bound"


def test_duplicate_dimension_semantics_are_rejected_instead_of_guessed():
    from app.agent.validators import resolve_dimension_field

    context = {"tables": [
        {"name": "fact_a", "fields": [{"name": "line_code", "semanticName": "产线"}]},
        {"name": "dim_line", "fields": [{"name": "production_line", "semanticName": "产线"}]},
    ]}
    assert resolve_dimension_field("产线", context) is None


def test_dimension_label_prefers_a_unique_display_field_over_ids_and_codes():
    from app.agent.validators import resolve_dimension_field

    context = {"tables": [
        {"name": "fact", "fields": [{"name": "process_id", "semanticName": "工序 ID"}]},
        {"name": "dim_process", "fields": [
            {"name": "process_id", "semanticName": "工序 ID"},
            {"name": "process_code", "semanticName": "工序编码"},
            {"name": "process_name", "semanticName": "工序名称"},
        ]},
    ]}
    assert resolve_dimension_field("工序", context) == "process_name"


def test_bare_june_contract_compiles_with_complete_month_filter(monkeypatch):
    from datetime import date
    from app.agent import time_resolution
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements

    monkeypatch.setattr(time_resolution, "reference_date", lambda _horizon=None: date(2026, 9, 10))
    context = {
        "source": {"type": "mysql"},
        "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(qty)",
                     "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
        "tables": [{"name": "fact_output", "objectType": "fact", "fields": [
            {"name": "qty", "semanticName": "产量"}, {"name": "stat_date", "semanticName": "日期"},
        ]}],
    }
    task = bind_query_requirements({
        "metricHint": "产量", "timeConstraint": {
            "kind": "absolute", "label": "6月份", "timeGrain": "month", "reference": "now",
        },
        "semanticIntent": {"metrics": [{"id": "m-output", "semantic": "产量"}], "dimensions": []},
    }, context)
    task["timeConstraint"] = time_resolution.resolve_time_constraint(task["timeConstraint"])
    sql, _meta = compile_query(context, task, "mysql")
    assert "`stat_date` >= '2026-06-01'" in sql
    assert "`stat_date` < '2026-07-01'" in sql


def test_time_contract_normalizes_units_and_rejects_incomplete_recent_range():
    import pytest
    from pydantic import ValidationError
    from app.agent.intent_contract import TimeRange

    assert TimeRange.model_validate({"mode": "recent", "amount": 7, "unit": "天"}).unit == "day"
    with pytest.raises(ValidationError):
        TimeRange.model_validate({"mode": "recent", "amount": 7})


def test_time_grain_validator_rejects_month_when_week_requested():
    from app.agent.query_requirements import bind_query_requirements, dimension_outputs
    context = {"source": {"type": "mysql"},
               "metrics": [{"physicalTimeField": "stat_date"}],
               "tables": [{"name": "fact", "fields": [{"name": "stat_date", "semanticName": "日期"}]}]}
    task = bind_query_requirements({"groupBy": ["周"]}, context)
    wrong = "SELECT DATE_FORMAT(stat_date, '%Y-%m') AS 周, SUM(qty) AS qty FROM fact GROUP BY 1"
    right = "SELECT YEARWEEK(stat_date) AS 周, SUM(qty) AS qty FROM fact GROUP BY 1"
    assert any("时间粒度" in item for item in dimension_outputs(wrong, task, context)[1])
    assert not dimension_outputs(right, task, context)[1]


def test_delivery_ledger_is_independent_from_planner_tasks():
    from app.agent.delivery import assess_delivery, build_requirement_ledger
    intent = AgentIntent.model_validate({"kind": "analysis", "question": "两项分析", "stepsHint": [
        {"kind": "analysis", "metrics": [{"semantic": "良率"}]},
        {"kind": "analysis", "metrics": [{"semantic": "产量"}], "chart": {"type": "line"}},
    ]})
    ledger = build_requirement_ledger(intent)
    # A planner is free to mutate its own steps; immutable requirements still expose omissions.
    result = assess_delivery(ledger, [{"task": {"requirementId": "R1", "metricHint": "良率"}}])
    assert result["missing"] == ["R2", "R2:chart"]


def test_delivery_rejects_wrong_period_and_empty_chart():
    from app.agent.delivery import assess_delivery

    requirements = [
        {"id": "R1", "kind": "query", "taskRequirementId": "R1", "contract": {
            "metricHint": "良率", "timeConstraint": {"kind": "comparison", "periods": [
                {"startInclusive": "2026-06-01", "endExclusive": "2026-07-01"},
                {"startInclusive": "2026-07-01", "endExclusive": "2026-08-01"},
            ]},
        }},
        {"id": "R1:chart", "kind": "chart", "taskRequirementId": "R1", "chartType": "bar",
         "contract": {"metricHint": "良率"}},
    ]
    result = assess_delivery(requirements, [{
        "task": {"requirementId": "R1", "metricHint": "良率",
                 "timeConstraint": {"kind": "comparison", "periods": [
                     {"startInclusive": "2026-07-01", "endExclusive": "2026-08-01"},
                 ]}},
        "execution": {"ok": True, "rows": []},
        "interpretation": {"chart": {"type": "bar", "data": []}},
    }])
    assert result["missing"] == ["R1", "R1:chart"]


def test_delivery_accepts_governed_metric_alias_and_equivalent_midnight_boundaries():
    from app.agent.delivery import assess_delivery

    requirements = [{"id": "R1", "kind": "query", "taskRequirementId": "R1", "contract": {
        "metricHint": "不良数", "timeConstraint": {"periods": [
            {"startInclusive": "2026-06-01T00:00:00", "endExclusive": "2026-07-01T00:00:00"},
        ]},
    }}]
    result = assess_delivery(requirements, [{
        "task": {"requirementId": "R1", "metricHint": "不良数量", "timeConstraint": {"periods": [
            {"startInclusive": "2026-06-01", "endExclusive": "2026-07-01"},
        ]}},
        "metricResolution": {"metric": "不良数量", "name": "不良数量"},
        "execution": {"ok": True, "rows": [[8]]},
    }])
    assert result["complete"] is True


def test_successful_empty_query_is_an_honest_chart_delivery_not_a_failure():
    from app.agent.delivery import assess_delivery

    requirements = [
        {"id": "R1", "kind": "query", "taskRequirementId": "R1",
         "contract": {"metricHint": "产量"}},
        {"id": "R1:chart", "kind": "chart", "taskRequirementId": "R1",
         "chartType": "line", "contract": {"metricHint": "产量"}},
    ]
    result = assess_delivery(requirements, [{
        "task": {"requirementId": "R1", "metricHint": "产量"},
        "execution": {"ok": True, "rows": [], "row_count": 0},
        "interpretation": {"chart": {"type": "line", "data": []}},
    }])

    assert result["complete"] is True


def test_specific_month_with_explicit_year_does_not_fall_back_to_data_horizon_year():
    from app.agent.time_resolution import resolve_time_constraint

    resolved = resolve_time_constraint({
        "kind": "specific_month", "label": "2028年6月", "month": 6,
        "reference": "data_horizon", "timeGrain": "month",
    }, "2026-07-15")
    assert resolved["periods"] == [{
        "key": "specific", "label": "2028-06",
        "startInclusive": "2028-06-01", "endExclusive": "2028-07-01",
    }]


def test_explicit_year_month_and_day_keep_authoritative_boundaries():
    comparison = AgentIntent.model_validate({
        "kind": "analysis",
        "question": "对比2025年与2026年6月产量",
        "metrics": [{"semantic": "产量"}],
        "comparison": {"periods": ["2025", "2026-06", "2026-06-15"]},
    })
    periods = intent_to_tasks(comparison)[0]["timeConstraint"]["periods"]
    assert periods[0]["startInclusive"] == "2025-01-01"
    assert periods[0]["endExclusive"] == "2026-01-01"
    assert periods[1]["startInclusive"] == "2026-06-01"
    assert periods[1]["endExclusive"] == "2026-07-01"
    assert periods[2]["startInclusive"] == "2026-06-15"
    assert periods[2]["endExclusive"] == "2026-06-16"


def test_mixed_comparison_periods_resolve_each_own_reference():
    from app.agent.time_resolution import resolve_time_constraint

    resolved = resolve_time_constraint({
        "kind": "comparison",
        "reference": "now",
        "periods": [
            {"label": "2026-06", "reference": "now", "startInclusive": "2026-06-01", "endExclusive": "2026-07-01"},
            {"label": "5月", "reference": "data_horizon"},
        ],
    }, "2024-07-31")
    assert resolved["periods"][1]["startInclusive"] == "2024-05-01"


def test_empty_delivery_ledger_is_vacuously_complete():
    from app.agent.delivery import assess_delivery

    assert assess_delivery([], [])["complete"] is True


def test_binding_contract_is_shared_and_cross_table_dimension_never_uses_fact_alias():
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements

    context = {
        "source": {"type": "mysql"},
        "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(f.qty)",
                     "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
        "tables": [
            {"name": "fact_output", "objectType": "fact", "fields": [
                {"name": "qty", "semanticName": "产量"}, {"name": "stat_date", "semanticName": "日期"},
            ]},
            {"name": "dim_product", "fields": [{"name": "product_name", "semanticName": "产品名称"}]},
        ],
    }
    task = bind_query_requirements({
        "normalizedQuestion": "按产品名称看产量",
        "metricHint": "产量",
        "groupBy": ["产品名称"],
        "semanticIntent": {"dimensions": [{"semantic": "产品名称", "code": "product_name"}]},
    }, context)
    assert task["queryBinding"]["factTable"] == "fact_output"
    assert task["queryBinding"]["dimensions"][0]["table"] == "dim_product"
    assert compile_query(context, task, "mysql") is None


def test_binding_contract_compiles_a_governed_registered_join():
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements

    context = {
        "source": {"type": "postgresql"},
        "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(f.qty)",
                     "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
        "tables": [
            {"name": "fact_output", "schemaName": "public", "qualifiedName": "public.fact_output",
             "objectType": "fact", "fields": [{"name": "qty"}, {"name": "stat_date"}, {"name": "product_id"}]},
            {"name": "dim_product", "schemaName": "public", "qualifiedName": "public.dim_product",
             "fields": [{"name": "product_id"}, {"name": "product_name", "semanticName": "产品名称"}]},
        ],
        "relations": [{"fromQualifiedTable": "public.fact_output", "fromField": "product_id",
                       "toQualifiedTable": "public.dim_product", "toField": "product_id"}],
    }
    task = bind_query_requirements({
        "normalizedQuestion": "按产品名称看产量", "metricHint": "产量", "groupBy": ["产品名称"],
        "semanticIntent": {"dimensions": [{"semantic": "产品名称", "code": "product_name"}]},
    }, context)
    sql, contract = compile_query(context, task, "postgresql")
    assert 'FROM "public"."fact_output" AS f' in sql
    assert 'JOIN "public"."dim_product" AS d1' in sql
    assert 'f."product_id" = d1."product_id"' in sql
    assert 'd1."product_name" AS "产品名称"' in sql
    assert contract["joins"][0]["toTable"] == "public.dim_product"


def test_bound_filter_can_use_registered_dimension_join_and_is_audited():
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements, bound_result_schema, query_contract_failures

    context = {
        "source": {"type": "postgresql"},
        "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(f.qty)",
                     "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
        "tables": [
            {"name": "fact_output", "objectType": "fact", "fields": [
                {"name": "qty"}, {"name": "stat_date"}, {"name": "product_id"},
            ]},
            {"name": "dim_product", "fields": [
                {"name": "product_id"}, {"name": "product_name", "semanticName": "产品名称"},
            ]},
        ],
        "relations": [{"fromTable": "fact_output", "fromField": "product_id",
                       "toTable": "dim_product", "toField": "product_id"}],
    }
    task = bind_query_requirements({
        "normalizedQuestion": "A产品的产量前5名", "metricHint": "产量", "groupBy": ["产品名称"],
        "filters": [{"field": "产品名称", "operator": "eq", "value": "A产品"}],
        "sort": [{"field": "产量", "direction": "desc"}], "topN": 5,
        "semanticIntent": {"dimensions": [{"semantic": "产品名称", "code": "product_name"}]},
    }, context)

    sql, _ = compile_query(context, task, "postgresql")

    assert 'd1."product_name" = \'A产品\'' in sql
    assert 'ORDER BY "产量" DESC' in sql
    assert sql.endswith("LIMIT 5")
    assert query_contract_failures(sql, task, context) == []
    assert bound_result_schema(sql, task, context) == [
        {"key": "产品名称", "role": "dimension", "grain": None, "semantic": "产品名称"},
        {"key": "产量", "role": "measure", "metricId": "m-output", "semantic": "产量", "unit": None},
    ]
    assert query_contract_failures(
        'SELECT d1."product_name" AS "产品名称", SUM(f.qty) AS "产量" '
        'FROM "fact_output" AS f JOIN "dim_product" AS d1 '
        'ON f."product_id" = d1."product_id" GROUP BY d1."product_name"',
        task,
        context,
    ) == [
        "查询缺少筛选条件「产品名称」",
        "查询没有落实排序「产量 DESC」",
        "查询没有落实 Top 5 数量限制",
    ]


def test_compiler_delegates_time_bucket_and_limit_to_each_database_adapter():
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements, dimension_outputs, query_contract_failures

    for source_type, expected in {
        "mysql": "DATE_FORMAT",
        "postgresql": "DATE_TRUNC",
        "sqlserver": "DATEADD",
        "oracle": "TRUNC",
    }.items():
        context = {
            "source": {"type": source_type},
            "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(f.qty)",
                         "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
            "tables": [{"name": "fact_output", "objectType": "fact", "fields": [
                {"name": "qty", "semanticName": "产量"}, {"name": "stat_date", "semanticName": "日期"},
            ]}],
        }
        task = bind_query_requirements({"normalizedQuestion": "按周看产量", "metricHint": "产量",
                                        "groupBy": ["周"], "topN": 10}, context)
        compiled = compile_query(context, task, source_type)
        assert compiled is not None
        sql, contract = compiled
        assert expected in sql.upper(), (source_type, sql)
        assert contract["axis"] == {"key": "周", "kind": "time", "grain": "week"}
        assert dimension_outputs(sql, task, context)[1] == [], (source_type, sql)
        assert query_contract_failures(sql, task, context) == [], (source_type, sql)


def test_time_grain_contract_recognizes_every_supported_adapter_bucket():
    from app.agent.query_compiler import compile_query
    from app.agent.query_requirements import bind_query_requirements, dimension_outputs

    for source_type in ("mysql", "postgresql", "sqlserver", "oracle"):
        for label in ("年", "季度", "月份", "周", "日期"):
            context = {
                "source": {"type": source_type},
                "metrics": [{"id": "m-output", "name": "产量", "physicalFormula": "SUM(f.qty)",
                             "physicalTimeField": "stat_date", "sourceTables": ["fact_output"]}],
                "tables": [{"name": "fact_output", "objectType": "fact", "fields": [
                    {"name": "qty"}, {"name": "stat_date", "semanticName": "日期"},
                ]}],
            }
            task = bind_query_requirements({"normalizedQuestion": f"按{label}看产量", "metricHint": "产量",
                                            "groupBy": [label]}, context)
            compiled = compile_query(context, task, source_type)
            assert compiled is not None, (source_type, label, task)
            sql, _ = compiled
            assert dimension_outputs(sql, task, context)[1] == [], (source_type, label, sql)
