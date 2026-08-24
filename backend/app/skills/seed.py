"""开箱即用的制造业分析技能；均为程序性步骤，不包含物理表权限。"""

from app.agent.ids import new_id
from app.db.models import AnalysisSkill
from app.db.platform import PlatformSession


BUILTIN_SKILLS = (
    {
        "name": "良率下降归因",
        "slug": "yield-drop-investigation",
        "topic": "质量分析",
        "description": "先确认指标口径和可比时间窗，再从工序、产线、产品和缺陷类型逐层定位变化来源。",
        "terms": ["良率下降", "良率异常", "合格率下降", "不良率上升", "质量波动"],
        "preconditions": ["存在可验证的良率或合格/投入数量口径", "比较周期和统计粒度可比"],
        "procedure": [
            "核对指标口径、统计对象和本期/基期时间覆盖",
            "先计算总体变化，再按可用工序、产线或产品维度下钻",
            "定位贡献最大的异常分组，并关联缺陷类型或检验结果",
            "区分数据覆盖变化与真实业务变化，输出可验证证据",
        ],
        "output": {"required": ["口径说明", "时间覆盖", "变化幅度", "主要贡献项", "证据限制"]},
    },
    {
        "name": "设备停机异常调查",
        "slug": "equipment-downtime-investigation",
        "topic": "设备分析",
        "description": "区分计划与非计划停机，从设备、产线、故障类型和时间分布定位停机异常。",
        "terms": ["停机异常", "停机时间", "非计划停机", "设备故障", "运行效率"],
        "preconditions": ["停机记录包含时间和设备标识", "计划/非计划口径已明确或可披露推导"],
        "procedure": [
            "确认分析时段和停机时长计算口径",
            "拆分计划与非计划停机并比较趋势",
            "按设备、产线和故障类型计算停机贡献",
            "检查高频短停与低频长停，给出重点设备和证据",
        ],
        "output": {"required": ["时间范围", "停机口径", "重点设备", "贡献分解", "证据限制"]},
    },
    {
        "name": "安全库存风险调查",
        "slug": "inventory-risk-investigation",
        "topic": "库存分析",
        "description": "以库存快照和安全库存口径为基础，识别低库存对象、持续时长和变化趋势。",
        "terms": ["安全库存", "低于安全库存", "库存风险", "缺货风险", "库存趋势"],
        "preconditions": ["存在库存快照日期", "可用库存和安全库存口径可比较"],
        "procedure": [
            "确认最新有效快照和数据延迟",
            "计算低于安全库存的对象和缺口",
            "按产品、仓库或物料等可用维度排序并比较趋势",
            "披露缺少出入库或需求数据时无法判断补货原因的限制",
        ],
        "output": {"required": ["快照日期", "风险对象", "库存缺口", "趋势", "数据限制"]},
    },
)


def seed_analysis_skills() -> None:
    with PlatformSession() as session:
        created = 0
        for item in BUILTIN_SKILLS:
            exists = session.query(AnalysisSkill.id).filter_by(
                slug=item["slug"], source_id=None, version=1
            ).first()
            if exists:
                continue
            session.add(
                AnalysisSkill(
                    id=new_id("skill"),
                    source_id=None,
                    name=item["name"],
                    slug=item["slug"],
                    description=item["description"],
                    topic=item["topic"],
                    status="published",
                    version=1,
                    trigger_json={"terms": item["terms"]},
                    preconditions_json=item["preconditions"],
                    procedure_json=item["procedure"],
                    output_policy_json=item["output"],
                    constraints_json=[
                        "不得绕过数据源、表和字段白名单",
                        "不得把技能中的示例当作当前数据事实",
                        "结论必须引用本次查询结果，并披露数据覆盖限制",
                    ],
                    confidence=0.85,
                    created_by="builtin",
                    reviewed_by="builtin",
                )
            )
            created += 1
        if created:
            from app.retrieval.index_jobs import enqueue_all_sources

            enqueue_all_sources(
                session,
                mode="incremental",
                reason="初始化受治理分析技能",
            )
        session.commit()
