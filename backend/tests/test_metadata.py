"""schema.sql 注释解析测试（不依赖数据库）。"""

from app.metadata.scanner import derive_topic, load_schema_comments


def test_load_schema_comments():
    comments = load_schema_comments()
    assert comments["dim_product"]["comment"] == "产品主数据"
    assert comments["mes_process_output"]["fields"]["good_qty"] == "合格数量"
    assert comments["qms_defect_detail"]["fields"]["responsible_process_id"] == "责任工序 ID"
    assert len(comments) == 10


def test_derive_topic():
    assert derive_topic("mes_work_order") == "生产"
    assert derive_topic("qms_inspection") == "质量"
    assert derive_topic("eqp_downtime_record") == "设备"
    assert derive_topic("inv_inventory_snapshot") == "库存"
    assert derive_topic("dim_product") == "主数据"
    assert derive_topic("unknown_table") == "未分类"
