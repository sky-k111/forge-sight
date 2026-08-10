"""阶段3业务知识中心：隔离数据库服务测试。"""

import pytest

from app.db.models import BusinessObject, DataSource, DataTable, TableField
from app.knowledge import service


def test_seed_official_knowledge_and_resource_bindings(knowledge_session):
    assert len(service.list_topics()) == 4
    assert len(service.list_objects()) == 9
    assert len(service.list_rules()) == 5
    metrics = service.list_metrics()
    assert len(metrics) == 11
    yield_metric = service.get_metric("M-004")
    assert yield_metric["name"] == "工序良率"
    assert yield_metric["formula"] == "SUM(合格数量) / NULLIF(SUM(投入数量), 0) × 100%"
    assert yield_metric["physicalFormula"] == "SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100"
    assert yield_metric["sourceTables"] == ["mes_process_output"]
    assert yield_metric["bindingStatus"] == "已验证"
    assert yield_metric["availableForAnalysis"] is True
    assert yield_metric["usageWarning"] == ""


def test_metric_binding_rejects_unknown_or_unregistered_tables(knowledge_session):
    with pytest.raises(service.KnowledgeValidation, match="未登记的数据表"):
        service.save_metric(
            {
                "name": "不存在的数据指标",
                "topic": "生产分析",
                "formula": "COUNT(*)",
                "source": "unknown_table",
            }
        )


def test_metric_publish_and_edit_returns_to_review(knowledge_session):
    created = service.save_metric(
        {
            "name": "工序投入数量",
            "topic": "生产分析",
            "formula": "SUM(input_qty)",
            "source": "mes_process_output",
            "timeField": "stat_date",
            "dimensions": ["工序", "产品"],
            "owner": "生产部",
        }
    )
    assert created["status"] == "待审核"
    assert created["availableForAnalysis"] is False
    assert "尚未发布" in created["usageWarning"]

    published = service.publish_metric(created["id"])
    assert published["status"] == "已发布"
    edited = service.save_metric(
        {
            **published,
            "formula": "SUM(input_qty) FILTER_VALID_ROWS",
        },
        published["id"],
    )
    assert edited["status"] == "待审核"
    assert edited["version"] == 2


def test_referenced_business_object_cannot_be_deleted(knowledge_session):
    with pytest.raises(service.KnowledgeConflict, match="作为分析维度引用"):
        service.delete_object("Product")

    service.create_object({"id": "UnusedObject", "name": "未使用对象", "topic": "全局"})
    assert service.delete_object("UnusedObject") == {"ok": True}
    with knowledge_session() as session:
        assert session.query(BusinessObject).filter_by(id="UnusedObject").first() is None


def test_topic_with_children_cannot_be_deleted(knowledge_session):
    with pytest.raises(service.KnowledgeConflict, match="仍有"):
        service.delete_topic("QUAL")


def test_same_metric_has_isolated_bindings_for_two_sources(knowledge_session):
    with knowledge_session() as session:
        source = DataSource(
            name="第二工厂数据底座",
            type="postgresql",
            host="127.0.0.1",
            port=5432,
            db_name="factory_b",
            username="reader",
            status="连接正常",
            is_default=False,
        )
        session.add(source)
        session.flush()
        session.add(DataTable(source_id=source.id, name="process_daily", registered=True))
        for ordinal, field_name in enumerate(["business_date", "pass_count", "total_count"], start=1):
            session.add(
                TableField(
                    source_id=source.id,
                    table_name="process_daily",
                    name=field_name,
                    data_type="date" if field_name == "business_date" else "decimal",
                    ordinal=ordinal,
                )
            )
        session.commit()
        source_id = source.id

    canonical = service.get_metric("M-004")
    factory_b = service.save_metric(
        {
            "name": canonical["name"],
            "topic": canonical["topic"],
            "formula": canonical["formula"],
            "definition": canonical["definition"],
            "synonyms": canonical["synonyms"],
            "dimensions": canonical["dimensions"],
            "semanticTimeField": canonical["semanticTimeField"],
            "precision": canonical["precision"],
            "owner": canonical["owner"],
            "sourceId": source_id,
            "sourceTables": ["process_daily"],
            "physicalFormula": "SUM(pass_count) / NULLIF(SUM(total_count), 0) * 100",
            "bindingTimeField": "business_date",
            "sqlDialect": "postgresql",
            "fieldMappings": [
                {"semanticField": "合格数量", "tableName": "process_daily", "fieldName": "pass_count"},
                {"semanticField": "投入数量", "tableName": "process_daily", "fieldName": "total_count"},
                {"semanticField": "统计日期", "tableName": "process_daily", "fieldName": "business_date"},
            ],
        },
        "M-004",
    )
    assert factory_b["sourceTables"] == ["process_daily"]
    assert factory_b["bindingStatus"] == "待验证"
    assert factory_b["availableForAnalysis"] is False

    published_b = service.publish_metric("M-004", source_id)
    assert published_b["availableForAnalysis"] is True
    assert published_b["sqlDialect"] == "postgresql"

    factory_a = service.get_metric("M-004")
    assert factory_a["sourceTables"] == ["mes_process_output"]
    assert factory_a["physicalFormula"] == "SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100"
    assert len(factory_a["bindings"]) == 2
