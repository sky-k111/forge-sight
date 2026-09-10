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
    result = assess_delivery(ledger, [{"task": {"requirementId": "R1"}}])
    assert result["missing"] == ["R2", "R2:chart"]


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
