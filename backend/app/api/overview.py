"""业务人员分析总览。

总览以当前数据源的已登记资产、已发布指标和已验证物理绑定为事实。
它只说明“现在能分析什么”，不在未执行问析时预先生成业务结论。
"""

from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func

from app.auth.deps import effective_user_id, optional_user
from app.db.models import (
    AnalysisMessage,
    AnalysisRun,
    AnalysisSession,
    AuditLog,
    DataSource,
    DataTable,
    GovernedModel,
    KnowledgeMetric,
    KnowledgeTopic,
    MetricSourceBinding,
    ModelingTask,
    TableField,
    TableRelation,
)
from app.db.platform import PlatformSession
from app.tenancy.service import TenantContext, tenant_context, tenant_source_ids

router = APIRouter(prefix="/api/overview", tags=["overview"])

TOPIC_ALIASES = {
    "生产": "生产分析",
    "质量": "质量分析",
    "设备": "设备分析",
    "库存": "库存分析",
}


def _source_or_default(session, source_id: int | None, tenant_id: str) -> DataSource | None:
    if source_id is not None:
        return session.query(DataSource).filter_by(id=source_id, tenant_id=tenant_id).first()
    return (
        session.query(DataSource).filter_by(tenant_id=tenant_id, is_default=True).first()
        or session.query(DataSource).filter_by(tenant_id=tenant_id).first()
    )


def _canonical_topic(name: str) -> str:
    normalized = (name or "").strip() or "未分类"
    return TOPIC_ALIASES.get(normalized, normalized)


def _topic_code(topic: str) -> str:
    codes = {
        "生产分析": "PROD",
        "质量分析": "QUAL",
        "设备分析": "EQPT",
        "库存分析": "INVT",
        "主数据": "MDM",
    }
    return codes.get(topic, "DATA")


def _capability(source: DataSource, table_count: int, metric_count: int) -> dict:
    if source.status != "连接正常":
        return {
            "state": "offline",
            "label": "数据源不可用",
            "detail": "连接恢复后才能发起问析。",
            "canAnalyze": False,
        }
    if table_count == 0:
        return {
            "state": "empty",
            "label": "尚未形成分析范围",
            "detail": "该数据源还没有已确认登记的数据表。",
            "canAnalyze": False,
        }
    if metric_count == 0:
        return {
            "state": "catalog_only",
            "label": "可进行数据探索",
            "detail": "已登记数据表，但尚无已验证的业务指标。",
            "canAnalyze": True,
        }
    return {
        "state": "ready",
        "label": "可直接问析",
        "detail": "已登记数据表并建立可执行的业务指标口径。",
        "canAnalyze": True,
    }


@router.get("")
def get_business_overview(
    sourceId: int | None = Query(default=None),
    userId: str = Query(default="local-test-user", min_length=1, max_length=64),
    user: dict | None = Depends(optional_user),
    context: TenantContext = Depends(tenant_context),
):
    with PlatformSession() as session:
        source = _source_or_default(session, sourceId, context.tenant_id)
        if source is None:
            raise HTTPException(status_code=404, detail="尚未配置数据源")

        tables = session.query(DataTable).filter_by(source_id=source.id, registered=True).all()
        bindings = session.query(MetricSourceBinding).filter_by(source_id=source.id).all()
        verified_metric_ids = {item.metric_id for item in bindings if item.status == "已验证"}
        trusted_metrics = (
            session.query(KnowledgeMetric)
            .filter(KnowledgeMetric.tenant_id == context.tenant_id, KnowledgeMetric.id.in_(verified_metric_ids), KnowledgeMetric.status == "已发布")
            .order_by(KnowledgeMetric.id)
            .all()
            if verified_metric_ids
            else []
        )

        table_counts = Counter(_canonical_topic(item.topic) for item in tables)
        metric_groups = defaultdict(list)
        for metric in trusted_metrics:
            metric_groups[_canonical_topic(metric.topic)].append(metric)

        topic_rows = session.query(KnowledgeTopic).filter_by(tenant_id=context.tenant_id, enabled=True).order_by(KnowledgeTopic.code).all()
        topic_definitions = {_canonical_topic(item.name): item for item in topic_rows}
        topic_names = list(dict.fromkeys([*topic_definitions, *table_counts, *metric_groups]))
        topics = []
        for name in topic_names:
            table_count = table_counts.get(name, 0)
            metrics = metric_groups.get(name, [])
            if not table_count and not metrics:
                continue
            definition = topic_definitions.get(name)
            mode = "metrics" if metrics else "tables"
            topics.append({
                "code": definition.code if definition else _topic_code(name),
                "name": name,
                "description": definition.description if definition else "已登记数据表，等待补充业务指标口径。",
                "color": definition.color if definition else "blue",
                "tableCount": table_count,
                "metricCount": len(metrics),
                "metricNames": [item.name for item in metrics[:3]],
                "mode": mode,
            })

        latest_scan = max((item.scanned_at for item in tables if item.scanned_at), default=None)
        update_dates = [item.data_updated for item in tables if item.data_updated]
        session_scope = session.query(AnalysisSession).filter_by(
            source_id=source.id, user_id=effective_user_id(userId, user), tenant_id=context.tenant_id
        )
        session_count = session_scope.count()
        sessions = (
            session_scope
            .order_by(AnalysisSession.updated_at.desc())
            .limit(4)
            .all()
        )
        recent_tasks = []
        for item in sessions:
            run = session.query(AnalysisRun).filter_by(session_id=item.id).order_by(AnalysisRun.created_at.desc()).first()
            latest_question = (
                session.query(AnalysisMessage)
                .filter_by(session_id=item.id, role="user")
                .order_by(AnalysisMessage.sequence_no.desc())
                .first()
            )
            display_title = " ".join((latest_question.content if latest_question else item.title).split())
            recent_tasks.append({
                "id": item.id,
                "title": display_title[:120] or "未命名分析",
                "updatedAt": item.updated_at.isoformat() if item.updated_at else "",
                "status": run.status if run else "未开始",
                "runCount": session.query(func.count(AnalysisRun.id)).filter_by(session_id=item.id).scalar() or 0,
            })

    suggestions = []
    for item in topics:
        if item["metricNames"]:
            metric_name = item["metricNames"][0]
            title = f"分析{metric_name}"
            prompt = f"请分析当前数据源中{metric_name}的最近趋势，并按可用业务维度对比。"
            caption = f"已验证指标：{' / '.join(item['metricNames'])}"
        else:
            title = f"了解{item['name']}数据"
            prompt = f"请概览当前数据源中与{item['name']}相关的已登记数据表和可分析字段。"
            caption = f"{item['tableCount']} 张已登记表 · 尚无已验证指标"
        suggestions.append({"id": f"topic-{item['code']}", "title": title, "prompt": prompt, "caption": caption})

    if not suggestions:
        suggestions.append({
            "id": "catalog",
            "title": "了解当前数据源",
            "prompt": "当前数据源中有哪些已登记的数据表？",
            "caption": "先确认数据资产，再建立可用业务指标",
        })

    capability = _capability(source, len(tables), len(trusted_metrics))
    return {
        "source": {
            "id": source.id,
            "name": source.name,
            "type": source.type,
            "database": source.db_name,
            "status": source.status,
        },
        "capability": capability,
        "snapshot": {
            "registeredTables": len(tables),
            "records": sum(item.row_count or 0 for item in tables),
            "latestData": max(update_dates) if update_dates else "待识别",
            "earliestTableUpdate": min(update_dates) if update_dates else "",
            "datedTables": len(update_dates),
            "lastScan": latest_scan.isoformat() if latest_scan else "",
            "readOnly": True,
        },
        "metrics": {
            "trusted": len(trusted_metrics),
            "pendingBindings": sum(1 for item in bindings if item.status != "已验证"),
        },
        "topics": topics,
        "suggestions": suggestions[:4],
        "recent": {
            "total": session_count,
            "items": recent_tasks,
        },
    }


@router.get("/admin")
def get_admin_overview(context: TenantContext = Depends(tenant_context)):
    """管理员治理总览：以指标、资源、模型与审计拦截的真实状态为事实。"""
    with PlatformSession() as session:
        source_ids = tenant_source_ids(session, context.tenant_id)
        metrics = session.query(KnowledgeMetric).filter_by(tenant_id=context.tenant_id).all()
        pending_metrics = [item for item in metrics if item.status == "待审核"]
        published_count = sum(1 for item in metrics if item.status == "已发布")
        publish_rate = round(published_count * 100 / len(metrics)) if metrics else 0

        table_count = session.query(DataTable).filter(DataTable.source_id.in_(source_ids), DataTable.registered.is_(True)).count() if source_ids else 0
        relation_count = session.query(TableRelation).filter(TableRelation.source_id.in_(source_ids)).count() if source_ids else 0
        unmapped_fields = session.query(TableField).filter(TableField.source_id.in_(source_ids), TableField.cn_name == "").count() if source_ids else 0

        tenant_models = session.query(GovernedModel).join(
            ModelingTask,
            ModelingTask.id == GovernedModel.task_id,
        )
        if source_ids:
            tenant_models = tenant_models.filter(ModelingTask.source_id.in_(source_ids))
            model_total = tenant_models.count()
            model_published = tenant_models.filter(GovernedModel.status == "published").count()
            model_pending = tenant_models.filter(
                GovernedModel.status.in_(["pending", "reviewing"])
            ).count()
        else:
            model_total = model_published = model_pending = 0

        intercept_count = session.query(AuditLog).filter_by(tenant_id=context.tenant_id, result="已拦截").count()
        intercept_records = (
            session.query(AuditLog)
            .filter_by(tenant_id=context.tenant_id, result="已拦截")
            .order_by(AuditLog.created_at.desc())
            .limit(3)
            .all()
        )
        topic_rows = (
            session.query(KnowledgeTopic)
            .filter_by(tenant_id=context.tenant_id, enabled=True)
            .order_by(KnowledgeTopic.code)
            .all()
        )
        latest_scan = session.query(func.max(DataTable.scanned_at)).filter(DataTable.source_id.in_(source_ids)).scalar() if source_ids else None

        health = [
            {
                "icon": "knowledge",
                "label": "业务语义",
                "value": f"{publish_rate}%",
                "note": f"{len(metrics)} 个指标 · {len(pending_metrics)} 个待审核",
                "tone": "blue",
                "page": "knowledge",
            },
            {
                "icon": "database",
                "label": "数据资源",
                "value": str(table_count),
                "note": f"{table_count} 张表 · {relation_count} 条关系",
                "tone": "teal",
                "page": "resources",
            },
            {
                "icon": "model",
                "label": "模型治理",
                "value": str(model_total),
                "note": f"{model_published} 个已发布 · {model_pending} 个待复核",
                "tone": "violet",
                "page": "model-governance",
            },
            {
                "icon": "shield",
                "label": "安全运行",
                "value": "100%",
                "note": f"只读策略与审计已启用 · 拦截 {intercept_count} 次已记录",
                "tone": "green",
                "page": "security",
            },
        ]

        queue: list[dict] = []
        for metric in pending_metrics[:3]:
            queue.append(
                {
                    "index": f"{len(queue) + 1:02d}",
                    "title": f"审核“{metric.name}”指标口径",
                    "note": f"{metric.owner} 提交 · {metric.topic} 主题",
                    "status": "待审核",
                    "tone": "amber",
                    "page": "knowledge",
                }
            )
        if unmapped_fields:
            queue.append(
                {
                    "index": f"{len(queue) + 1:02d}",
                    "title": "确认数据字段映射",
                    "note": f"{unmapped_fields} 个字段尚未补充业务中文名",
                    "status": "待映射",
                    "tone": "blue",
                    "page": "resources",
                }
            )
        for record in intercept_records:
            queue.append(
                {
                    "index": f"{len(queue) + 1:02d}",
                    "title": "复核危险 SQL 拦截记录",
                    "note": (record.action or "").strip()[:60],
                    "status": "已拦截",
                    "tone": "red",
                    "page": "security",
                }
            )

        pending_categories = sum(bool(n) for n in (len(pending_metrics), unmapped_fields, intercept_count))
        detail = " · ".join(
            [
                f"{len(pending_metrics)} 个指标待审核",
                f"{unmapped_fields} 个字段待映射",
                f"{intercept_count} 条拦截记录待复核",
            ]
        )

        return {
            "summary": {
                "categoryCount": pending_categories,
                "detail": detail,
                "baseline": "治理基线正常" if pending_categories == 0 else f"{pending_categories} 类事项待处理",
                "lastCheck": latest_scan.strftime("%H:%M") if latest_scan else "",
            },
            "health": health,
            "queue": queue,
            "topics": [{"name": item.name, "coverage": item.coverage} for item in topic_rows],
        }
