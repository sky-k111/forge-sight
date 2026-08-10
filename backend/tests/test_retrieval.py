"""阶段 4 语义检索：召回质量、最小上下文与数据源隔离。"""

from app.db.models import DataSource, DataTable, TableField
from app.knowledge import service as knowledge_service
from app.retrieval import service


def test_retrieves_verified_metric_and_minimal_sql_scope(knowledge_session):
    package = service.build_context_package("请分析各工序的良率", max_metrics=3)

    assert package["coverage"]["status"] == "ready"
    assert package["metrics"][0]["id"] == "M-004"
    assert package["metrics"][0]["physicalFormula"] == "SUM(good_qty) / NULLIF(SUM(input_qty), 0) * 100"
    assert "mes_process_output" in package["executionPolicy"]["allowedTables"]
    assert "good_qty" in package["executionPolicy"]["allowedColumns"]["mes_process_output"]
    assert "input_qty" in package["executionPolicy"]["allowedColumns"]["mes_process_output"]
    assert package["executionPolicy"]["readOnly"] is True
    assert package["executionPolicy"]["allowUnregisteredTables"] is False
    assert all(table["name"] in package["executionPolicy"]["allowedTables"] for table in package["tables"])


def test_retrieves_registered_join_path_for_dimension(knowledge_session):
    package = service.build_context_package("按工序分析工序良率")

    assert {"mes_process_output", "dim_process"}.issubset(package["executionPolicy"]["allowedTables"])
    assert package["relations"] == [
        {
            "fromTable": "mes_process_output",
            "fromField": "process_id",
            "toTable": "dim_process",
            "toField": "process_id",
            "source": "扫描",
        }
    ]
    assert "process_id" in package["executionPolicy"]["allowedColumns"]["mes_process_output"]
    assert "process_id" in package["executionPolicy"]["allowedColumns"]["dim_process"]


def test_context_is_strictly_isolated_by_source(knowledge_session):
    with knowledge_session() as session:
        source = DataSource(
            name="第二工厂",
            type="postgresql",
            host="127.0.0.1",
            port=5432,
            db_name="factory_b",
            username="reader",
            status="连接正常",
        )
        session.add(source)
        session.flush()
        session.add(DataTable(source_id=source.id, name="process_daily", cn_name="工序日报", registered=True))
        for ordinal, (name, cn_name) in enumerate(
            [("business_date", "统计日期"), ("pass_count", "合格数量"), ("total_count", "投入数量")],
            start=1,
        ):
            session.add(
                TableField(
                    source_id=source.id,
                    table_name="process_daily",
                    name=name,
                    cn_name=cn_name,
                    data_type="date" if name == "business_date" else "decimal",
                    ordinal=ordinal,
                )
            )
        session.commit()
        source_id = source.id

    metric = knowledge_service.get_metric("M-004")
    knowledge_service.save_metric(
        {
            "name": metric["name"],
            "topic": metric["topic"],
            "formula": metric["formula"],
            "definition": metric["definition"],
            "synonyms": metric["synonyms"],
            "dimensions": metric["dimensions"],
            "semanticTimeField": metric["semanticTimeField"],
            "precision": metric["precision"],
            "owner": metric["owner"],
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
    knowledge_service.publish_metric("M-004", source_id)

    package = service.build_context_package("分析工序良率", source_id)
    assert package["source"]["id"] == source_id
    assert package["source"]["type"] == "postgresql"
    assert package["executionPolicy"]["allowedTables"] == ["process_daily"]
    assert "mes_process_output" not in str(package)
    assert package["metrics"][0]["sqlDialect"] == "postgresql"


def test_returns_safe_no_match_package(knowledge_session):
    package = service.build_context_package("分析员工食堂菜谱满意度")

    assert package["coverage"]["status"] == "no_match"
    assert package["metrics"] == []
    assert package["tables"] == []
    assert package["executionPolicy"]["allowedTables"] == []
    assert package["coverage"]["warnings"]


def test_no_match_diagnoses_missing_sales_capability_and_product_entity(knowledge_session):
    with knowledge_session() as session:
        source_id = session.query(DataSource.id).filter_by(is_default=True).scalar()
        session.add(
            DataTable(
                source_id=source_id,
                name="dim_product",
                cn_name="产品主数据",
                topic="主数据",
                primary_key="product_id",
                registered=True,
            )
        )
        for ordinal, (name, cn_name) in enumerate(
            [
                ("product_id", "产品 ID"),
                ("product_code", "产品编码"),
                ("product_name", "产品名称"),
            ],
            start=1,
        ):
            session.add(
                TableField(
                    source_id=source_id,
                    table_name="dim_product",
                    name=name,
                    cn_name=cn_name,
                    data_type="varchar",
                    ordinal=ordinal,
                    is_primary=name == "product_id",
                )
            )
        session.commit()

    package = service.build_context_package("这个月泰罗奥特曼一共卖出去多少")

    assert package["coverage"]["status"] == "no_match"
    assert package["diagnostics"]["requestedCapability"] == {
        "code": "sales",
        "label": "销售/出库",
        "matchedTerms": ["卖出"],
        "available": False,
    }
    assert package["diagnostics"]["entityLookup"]["value"] == "泰罗奥特曼"
    assert package["diagnostics"]["entityLookup"]["table"] == "dim_product"
