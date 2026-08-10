"""阶段 5F：MySQL/PostgreSQL EXPLAIN 计划摘要。"""

from app.gateway.cost_guard import _mysql_summary, _postgres_summary


def test_mysql_explain_summary_extracts_rows_cost_and_full_scan():
    plan = {
        "query_block": {
            "cost_info": {"query_cost": "1250.75"},
            "table": {
                "table_name": "mes_process_output",
                "access_type": "ALL",
                "rows_examined_per_scan": 750000,
            },
        }
    }
    rows, cost, full_scans = _mysql_summary(plan)
    assert rows == 750000
    assert cost == 1250.75
    assert full_scans == ["mes_process_output"]


def test_postgresql_explain_summary_extracts_rows_cost_and_seq_scan():
    plan = [
        {
            "Plan": {
                "Node Type": "Aggregate",
                "Plan Rows": 1,
                "Total Cost": 2200.5,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "process_daily",
                        "Plan Rows": 650000,
                        "Total Cost": 2100.0,
                    }
                ],
            }
        }
    ]
    rows, cost, full_scans = _postgres_summary(plan)
    assert rows == 650000
    assert cost == 2200.5
    assert full_scans == ["process_daily"]

