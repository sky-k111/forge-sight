"""制造业报告模板。模板描述业务目标，物理表映射仍由知识库完成。"""

from __future__ import annotations

from app.db.models import ReportTemplate
from app.db.platform import PlatformSession


REPORT_TEMPLATES = [
    {
        "id": "tpl-operations-weekly",
        "name": "制造运营综合周报",
        "report_type": "weekly",
        "topic": "综合",
        "audience": "生产经理、厂长",
        "description": "聚合生产、质量、设备和库存风险，适合经营例会。",
        "min_coverage": 65,
        "sections_json": [
            {"code": "production", "title": "生产达成与趋势", "metrics": ["产量"], "question": "统计周期内各产线产量、变化趋势及重点偏差"},
            {"code": "quality", "title": "质量表现与缺陷", "metrics": ["工序良率", "不良数量", "不良类型排行"], "question": "分析周期内工序良率、不良数量与主要缺陷类型"},
            {"code": "equipment", "title": "设备停机与影响", "metrics": ["设备停机时长", "非计划停机时长"], "question": "分析周期内设备停机及非计划停机的对象排名和影响"},
            {"code": "inventory", "title": "库存风险", "metrics": ["低于安全库存"], "question": "识别周期末低于安全库存的产品与仓库，并按缺口排序"},
        ],
    },
    {
        "id": "tpl-production-weekly",
        "name": "生产周报",
        "report_type": "weekly",
        "topic": "生产",
        "audience": "生产主管",
        "description": "关注产量、产线与工序趋势。",
        "min_coverage": 60,
        "sections_json": [
            {"code": "output", "title": "产量概览", "metrics": ["产量"], "question": "汇总周期内总产量，并与上一周期比较"},
            {"code": "line-trend", "title": "产线与工序趋势", "metrics": ["产量"], "question": "按产线和工序展示周期内产量趋势并找出主要变化"},
        ],
    },
    {
        "id": "tpl-quality-weekly",
        "name": "质量周报",
        "report_type": "weekly",
        "topic": "质量",
        "audience": "质量负责人",
        "description": "关注良率、抽检、不良与缺陷 Pareto。",
        "min_coverage": 70,
        "sections_json": [
            {"code": "yield", "title": "良率与不良趋势", "metrics": ["工序良率", "工序不良率"], "question": "分析周期内各工序良率和不良率趋势，并与上一周期比较"},
            {"code": "inspection", "title": "抽检表现", "metrics": ["抽检不良率"], "question": "统计周期内各产品抽检不良率并识别异常对象"},
            {"code": "defects", "title": "缺陷 Pareto", "metrics": ["不良类型排行"], "question": "汇总周期内不良类型，输出 Pareto 排名和累计贡献"},
            {"code": "focus", "title": "重点产品与工序", "metrics": ["不良数量"], "question": "找出周期内不良数量最高的产品和工序并给出证据"},
        ],
    },
    {
        "id": "tpl-equipment-weekly",
        "name": "设备周报",
        "report_type": "weekly",
        "topic": "设备",
        "audience": "设备负责人",
        "description": "关注停机、非计划停机和影响对象。",
        "min_coverage": 70,
        "sections_json": [
            {"code": "downtime", "title": "停机概览", "metrics": ["设备停机时长"], "question": "汇总周期内设备停机时长并与上一周期比较"},
            {"code": "unplanned", "title": "非计划停机", "metrics": ["非计划停机时长"], "question": "按设备和产线分析周期内非计划停机排名与变化"},
        ],
    },
    {
        "id": "tpl-inventory-weekly",
        "name": "库存周报",
        "report_type": "weekly",
        "topic": "库存",
        "audience": "物料计划员、仓储负责人",
        "description": "关注最新快照、安全库存缺口与风险清单。",
        "min_coverage": 70,
        "sections_json": [
            {"code": "shortage", "title": "安全库存风险", "metrics": ["低于安全库存"], "question": "基于周期末最新库存快照，列出低于安全库存的产品、仓库和缺口"},
            {"code": "distribution", "title": "风险分布", "metrics": ["低于安全库存"], "question": "按仓库和产品类别汇总低于安全库存的风险分布"},
        ],
    },
    {
        "id": "tpl-topic-report",
        "name": "专题分析报告",
        "report_type": "topic",
        "topic": "专题",
        "audience": "业务分析人员",
        "description": "围绕一个自然语言业务问题形成可复核的专题结论。",
        "min_coverage": 0,
        "sections_json": [
            {"code": "topic", "title": "专题分析", "metrics": [], "question": "{prompt}"},
        ],
    },
]


def seed_report_templates(session_factory=PlatformSession) -> dict:
    created = 0
    updated = 0
    with session_factory() as session:
        for item in REPORT_TEMPLATES:
            row = session.query(ReportTemplate).filter_by(id=item["id"]).first()
            if row is None:
                session.add(ReportTemplate(**item))
                created += 1
                continue
            # 内置模板可随产品升级，管理员自建模板使用其他 ID，不会被覆盖。
            for key, value in item.items():
                if key != "id":
                    setattr(row, key, value)
            updated += 1
        session.commit()
    return {"created": created, "updated": updated}

