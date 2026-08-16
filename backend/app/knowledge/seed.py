"""首批制造业知识初始化，口径来源于官方 data/数据介绍.md 第5节。"""

from __future__ import annotations

import json
from datetime import datetime

from app.db.models import (
    BusinessObject,
    BusinessRule,
    DataSource,
    DataTable,
    KnowledgeMetric,
    KnowledgeTopic,
    MetricFieldBinding,
    MetricSourceBinding,
    MetricTableBinding,
    TableField,
)
from app.db.platform import PlatformSession

TOPICS = [
    {"code": "PROD", "name": "生产分析", "color": "blue", "description": "工单、产线、工序和产量趋势", "coverage": 92},
    {"code": "QUAL", "name": "质量分析", "color": "teal", "description": "检验、缺陷、不良品与质量趋势", "coverage": 96},
    {"code": "EQPT", "name": "设备分析", "color": "amber", "description": "停机、故障、报警与运行效率", "coverage": 84},
    {"code": "INVT", "name": "库存分析", "color": "violet", "description": "库存快照、安全库存与仓库分布", "coverage": 88},
]

OBJECTS = [
    {"id": "Product", "name": "产品", "topic": "全局", "relation_count": 8, "description": "制造活动的最终或中间产出"},
    {"id": "WorkOrder", "name": "生产工单", "topic": "生产分析", "relation_count": 12, "description": "规定产品、数量和计划时间的生产任务"},
    {"id": "Process", "name": "工序", "topic": "生产分析", "relation_count": 9, "description": "生产过程中可独立度量的作业环节"},
    {"id": "ProductionLine", "name": "产线", "topic": "生产分析", "relation_count": 7, "description": "承载连续生产活动的设备与工位集合"},
    {"id": "Equipment", "name": "设备", "topic": "设备分析", "relation_count": 11, "description": "参与生产并产生运行数据的资产"},
    {"id": "Inspection", "name": "质量检验", "topic": "质量分析", "relation_count": 10, "description": "依据标准对产品质量进行判定的活动"},
    {"id": "Defect", "name": "不良类型", "topic": "质量分析", "relation_count": 6, "description": "检验过程中识别的不符合项类别"},
    {"id": "Inventory", "name": "库存", "topic": "库存分析", "relation_count": 5, "description": "产品在特定日期和仓库的可用、冻结与安全库存状态"},
    {"id": "Warehouse", "name": "仓库", "topic": "库存分析", "relation_count": 3, "description": "存放产品并承载库存快照的业务位置"},
]

RULES = [
    {"id": "R-001", "name": "投入数量为 0 时不计算工序良率", "rule_type": "指标规则", "scope": "质量分析", "content": "分母为0时返回空值，不返回0%。"},
    {"id": "R-002", "name": "停机结束时间不得早于开始时间", "rule_type": "数据校验", "scope": "设备分析", "content": "end_time 必须大于等于 start_time。"},
    {"id": "R-003", "name": "工单完工量不得超过计划量 120%", "rule_type": "异常规则", "scope": "生产分析", "content": "实际产量超过计划数量120%时标记异常并提示复核。"},
    {"id": "R-004", "name": "跨表统计必须使用已登记关联路径", "rule_type": "查询规则", "scope": "全局", "content": "禁止 Agent 编造未登记的表关联关系。"},
    {"id": "R-005", "name": "库存预警以最新快照为准", "rule_type": "指标规则", "scope": "库存分析", "content": "同一产品与仓库只使用查询周期内最新 snapshot_date。"},
]

METRICS = [
    {"id": "M-001", "name": "产量", "topic": "生产分析", "formula": "SUM(合格数量 + 不良数量)", "physical_formula": "SUM(good_qty + defect_qty)", "definition": "统计周期内产出的良品与不良品数量之和。", "synonyms": ["实际产量", "生产数量"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "数量 · 整数", "owner": "生产部", "tables": ["mes_process_output"], "fields": [("合格数量", "mes_process_output", "good_qty"), ("不良数量", "mes_process_output", "defect_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-002", "name": "合格数量", "topic": "质量分析", "formula": "SUM(合格数量)", "physical_formula": "SUM(good_qty)", "definition": "统计周期内工序产出的合格品数量。", "synonyms": ["良品数量", "良品数"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "数量 · 整数", "owner": "质量部", "tables": ["mes_process_output"], "fields": [("合格数量", "mes_process_output", "good_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-003", "name": "不良数量", "topic": "质量分析", "formula": "SUM(不良数量)", "physical_formula": "SUM(defect_qty)", "definition": "统计周期内工序产出的不良品数量。", "synonyms": ["不良数", "缺陷数量"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "数量 · 整数", "owner": "质量部", "tables": ["mes_process_output"], "fields": [("不良数量", "mes_process_output", "defect_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-004", "name": "工序良率", "topic": "质量分析", "formula": "SUM(合格数量) / NULLIF(SUM(投入数量), 0) × 100%", "physical_formula": "SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100", "definition": "衡量指定工序在统计周期内产出合格品的能力，采用投入数量加权计算。", "synonyms": ["工序合格率", "制程良率", "工序通过率"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "百分比 · 2 位小数", "owner": "质量部", "tables": ["mes_process_output"], "fields": [("合格数量", "mes_process_output", "good_qty"), ("投入数量", "mes_process_output", "input_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-005", "name": "工序不良率", "topic": "质量分析", "formula": "SUM(不良数量) / NULLIF(SUM(投入数量), 0) × 100%", "physical_formula": "SUM(defect_qty) / NULLIF(SUM(input_qty), 0) * 100", "definition": "统计工序投入中不良数量所占比例。", "synonyms": ["制程不良率"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "百分比 · 2 位小数", "owner": "质量部", "tables": ["mes_process_output"], "fields": [("不良数量", "mes_process_output", "defect_qty"), ("投入数量", "mes_process_output", "input_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-006", "name": "返工率", "topic": "质量分析", "formula": "SUM(返工数量) / NULLIF(SUM(投入数量), 0) × 100%", "physical_formula": "SUM(rework_qty) / NULLIF(SUM(input_qty), 0) * 100", "definition": "统计工序投入中需要返工的数量占比。", "synonyms": ["重工率"], "dimensions": ["工序", "产品", "产线"], "semantic_time_field": "统计日期", "time_field": "stat_date", "precision": "百分比 · 2 位小数", "owner": "质量部", "tables": ["mes_process_output"], "fields": [("返工数量", "mes_process_output", "rework_qty"), ("投入数量", "mes_process_output", "input_qty"), ("统计日期", "mes_process_output", "stat_date")]},
    {"id": "M-007", "name": "抽检不良率", "topic": "质量分析", "formula": "SUM(不良数量) / NULLIF(SUM(抽检数量), 0) × 100%", "physical_formula": "SUM(defect_qty) / NULLIF(SUM(sample_qty), 0) * 100", "definition": "抽检样本中不良数量所占比例。", "synonyms": ["抽检不合格率", "检验不良率"], "dimensions": ["产品", "工序"], "semantic_time_field": "检验日期", "time_field": "inspection_date", "precision": "百分比 · 2 位小数", "owner": "质量部", "tables": ["qms_inspection"], "fields": [("不良数量", "qms_inspection", "defect_qty"), ("抽检数量", "qms_inspection", "sample_qty"), ("检验日期", "qms_inspection", "inspection_date")]},
    {"id": "M-008", "name": "不良类型排行", "topic": "质量分析", "formula": "按不良类型分组 SUM(不良数量) 并降序排序", "physical_formula": "GROUP BY qms_defect_detail.defect_type ORDER BY SUM(qms_defect_detail.defect_qty) DESC", "definition": "按不良类型汇总不良数量，用于识别主要缺陷类别。", "synonyms": ["缺陷排行", "不良原因排行"], "dimensions": ["不良类型", "工序"], "semantic_time_field": "检验日期", "time_field": "inspection_date", "precision": "数量 · 整数", "owner": "质量部", "tables": ["qms_defect_detail", "qms_inspection"], "fields": [("不良类型", "qms_defect_detail", "defect_type"), ("不良数量", "qms_defect_detail", "defect_qty"), ("检验日期", "qms_inspection", "inspection_date")]},
    {"id": "M-009", "name": "设备停机时长", "topic": "设备分析", "formula": "SUM(停机时长)", "physical_formula": "SUM(downtime_minutes)", "definition": "统计设备在周期内的停机总分钟数。", "synonyms": ["停机时间"], "dimensions": ["设备", "产线"], "semantic_time_field": "停机开始时间", "time_field": "start_time", "precision": "分钟 · 整数", "owner": "设备部", "tables": ["eqp_downtime_record"], "fields": [("停机时长", "eqp_downtime_record", "downtime_minutes"), ("停机开始时间", "eqp_downtime_record", "start_time")]},
    {"id": "M-010", "name": "非计划停机时长", "topic": "设备分析", "formula": "SUM(停机时长) WHERE 是否计划停机 = 否", "physical_formula": "SUM(downtime_minutes)", "definition": "统计周期内排除计划停机后的设备停机分钟数。", "synonyms": ["故障停机时长", "意外停机时间"], "dimensions": ["设备", "产线"], "semantic_time_field": "停机开始时间", "time_field": "start_time", "filter_expression": "is_planned = false", "precision": "分钟 · 整数", "owner": "设备部", "tables": ["eqp_downtime_record"], "fields": [("停机时长", "eqp_downtime_record", "downtime_minutes"), ("是否计划停机", "eqp_downtime_record", "is_planned"), ("停机开始时间", "eqp_downtime_record", "start_time")]},
    {"id": "M-011", "name": "低于安全库存", "topic": "库存分析", "formula": "可用库存 < 安全库存", "physical_formula": "available_qty < safety_stock_qty", "definition": "识别最新库存快照中可用库存低于安全库存的产品与仓库。", "synonyms": ["安全库存预警", "库存不足"], "dimensions": ["产品", "仓库"], "semantic_time_field": "库存快照日期", "time_field": "snapshot_date", "precision": "布尔值", "owner": "仓储部", "tables": ["inv_inventory_snapshot"], "fields": [("可用库存", "inv_inventory_snapshot", "available_qty"), ("安全库存", "inv_inventory_snapshot", "safety_stock_qty"), ("库存快照日期", "inv_inventory_snapshot", "snapshot_date")]},
]


def seed_knowledge() -> dict:
    """幂等初始化：只补缺失知识，不覆盖管理员已有修改。"""
    created = {"topics": 0, "objects": 0, "rules": 0, "metrics": 0, "bindings": 0}
    with PlatformSession() as session:
        for item in TOPICS:
            if session.query(KnowledgeTopic).filter_by(code=item["code"]).first() is None:
                session.add(KnowledgeTopic(**item))
                created["topics"] += 1
        for item in OBJECTS:
            if session.query(BusinessObject).filter_by(id=item["id"]).first() is None:
                session.add(BusinessObject(**item))
                created["objects"] += 1
        for item in RULES:
            if session.query(BusinessRule).filter_by(id=item["id"]).first() is None:
                session.add(BusinessRule(**item))
                created["rules"] += 1
        session.flush()

        source = session.query(DataSource).filter_by(is_default=True).first()
        source_id = source.id if source else None
        registered = set()
        registered_fields = set()
        if source_id is not None:
            registered = {
                row[0]
                for row in session.query(DataTable.name).filter_by(source_id=source_id, registered=True).all()
            }
            registered_fields = {
                (row[0], row[1])
                for row in session.query(TableField.table_name, TableField.name).filter_by(source_id=source_id).all()
            }

        for item in METRICS:
            metric = session.query(KnowledgeMetric).filter_by(id=item["id"]).first()
            valid_tables = [name for name in item["tables"] if name in registered]
            valid_fields = [field for field in item["fields"] if (field[1], field[2]) in registered_fields]
            binding_valid = len(valid_tables) == len(item["tables"]) and len(valid_fields) == len(item["fields"])
            if metric is None:
                metric = KnowledgeMetric(
                    id=item["id"],
                    name=item["name"],
                    topic=item["topic"],
                    definition=item["definition"],
                    formula=item["formula"],
                    synonyms_json=json.dumps(item["synonyms"], ensure_ascii=False),
                    dimensions_json=json.dumps(item["dimensions"], ensure_ascii=False),
                    time_field=item["semantic_time_field"],
                    precision=item["precision"],
                    owner=item["owner"],
                    status="已发布" if binding_valid else "待审核",
                    version=1,
                    published_at=datetime.now() if binding_valid else None,
                )
                session.add(metric)
                session.flush()
                created["metrics"] += 1
            if source_id is not None:
                binding = session.query(MetricSourceBinding).filter_by(metric_id=metric.id, source_id=source_id).first()
                if binding is None and valid_tables:
                    binding = MetricSourceBinding(
                        metric_id=metric.id,
                        source_id=source_id,
                        physical_formula=item["physical_formula"],
                        time_field=item["time_field"],
                        filter_expression=item.get("filter_expression", ""),
                        sql_dialect=source.type,
                        status="已验证" if binding_valid else "待验证",
                        validated_at=datetime.now() if binding_valid else None,
                    )
                    session.add(binding)
                    session.flush()
                    for table_name in valid_tables:
                        session.add(MetricTableBinding(binding_id=binding.id, table_name=table_name))
                    for semantic_field, table_name, field_name in valid_fields:
                        session.add(
                            MetricFieldBinding(
                                binding_id=binding.id,
                                semantic_field=semantic_field,
                                table_name=table_name,
                                field_name=field_name,
                            )
                        )
                    created["bindings"] += 1
        session.commit()
    return created
