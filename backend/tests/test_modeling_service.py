"""建模服务层测试：任务创建、同步训练、推理、治理状态机。"""

import joblib
import pytest

from app.db.models import GovernedModel, ModelingEvaluation, ModelingInferenceLog, ModelingTask
from app.modeling import service as modeling_service
from app.modeling.contracts import ModelingTaskCreate
from app.modeling.service import (
    ModelingError,
    ModelNotFoundError,
    TaskNotTrainedError,
    create_task,
    create_threshold_variant,
    diagnose_task,
    find_reusable_models,
    get_evaluation,
    get_task,
    list_evaluations,
    list_models,
    list_tasks,
    predict,
    publish_model,
    retire_model,
    rollback_model,
    review_model,
    train_task,
)

DATASET_SQL = "SELECT * FROM mes_process_output LIMIT 10000"


def make_task_payload(**overrides):
    base = dict(
        title="高不良风险识别模型",
        source="agent",
        sourceId=1,
        ownerId="zhangsan",
        goal="识别高不良风险的生产记录",
        taskType="classification",
        datasetName="工序产量事实表",
        datasetSql=DATASET_SQL,
        targetField="high_defect_risk",
        targetLabel="高风险标记",
        features=["工序", "产线", "停机时长", "计划数量"],
        candidates=["logistic", "tree", "forest"],
        runId=None,
    )
    base.update(overrides)
    return ModelingTaskCreate(**base)


def test_create_task_and_run_id_idempotent(modeling_session):
    task, duplicate = create_task(make_task_payload(runId="run-001"))
    assert duplicate is False
    assert task["status"] == "draft"
    assert task["taskTypeLabel"] == "二分类"
    assert task["sourceLabel"] == "Agent 创建"
    assert task["candidates"] == ["logistic", "tree", "forest"]
    assert task["metric"] is None
    assert task["sampleSummary"] == ""

    again, duplicate = create_task(make_task_payload(runId="run-001"))
    assert duplicate is True
    assert again["id"] == task["id"]

    assert len(list_tasks(owner_id="zhangsan")) == 1


def test_create_task_rejects_bad_sql(modeling_session, monkeypatch):
    from app.gateway.executor import ExecutionResult

    def fake(sql, action="", user_name="", source=None):
        return ExecutionResult(
            ok=False, columns=[], rows=[], row_count=0,
            error="SQL 包含未注册表 forbidden_table", tables=[], truncated=False,
        )

    monkeypatch.setattr(modeling_service, "execute_readonly", fake)
    with pytest.raises(ModelingError, match="安全校验"):
        create_task(make_task_payload())


def test_create_task_rejects_identifier_as_regression_target(modeling_session):
    with pytest.raises(ModelingError, match="记录标识"):
        create_task(make_task_payload(
            taskType="regression",
            targetField="output_id",
            targetLabel="产量记录 ID",
            candidates=["linear", "tree", "forest"],
        ))


def test_train_full_flow(modeling_session):
    task, _ = create_task(make_task_payload())
    result = train_task(task["id"], "auto", ["logistic", "tree", "forest"], None, "zhangsan")

    assert result["task"]["status"] == "trained"
    updated = get_task(task["id"])
    assert updated["status"] == "trained"
    assert updated["selectedModel"] in {"logistic", "tree", "forest"}
    assert updated["metric"]  # 摘要非空
    assert updated["sampleCount"] == 240

    evaluation = get_evaluation(task["id"])
    assert len(evaluation["models"]) == 3
    assert evaluation["recommendedModel"] in {"logistic", "tree", "forest"}
    assert evaluation["data"]["sampleCount"] == 240
    assert evaluation["qualityAssessment"]["status"] in {"ready", "warning", "blocked"}
    assert set(evaluation["data"]["encodedFeatures"]) >= {"工序", "产线"}
    # 每个模型都带指标与特征重要性
    for item in evaluation["models"]:
        assert "metrics" in item and "featureImportance" in item

    # 模型注册 + joblib 落盘
    models = list_models()
    assert len(models) == 3
    # 两级授权：机器门禁通过的候选训练后直接进入个人自用档
    assert all(m["status"] == "personal" for m in models)
    assert all(m["personalExpiresAt"] for m in models)
    assert all(m["algorithmName"] for m in models)
    with modeling_session() as session:
        for row in session.query(GovernedModel).all():
            payload = joblib.load(row.model_path)
            assert payload["formatVersion"] == 2
            assert "pipeline" in payload and "schema" in payload
            assert row.artifact_checksum and len(row.artifact_checksum) == 64


def test_train_manual_only_requested_algorithm(modeling_session):
    task, _ = create_task(make_task_payload())
    train_task(task["id"], "manual", [], "tree", "zhangsan")
    evaluation = get_evaluation(task["id"])
    assert [m["algorithm"] for m in evaluation["models"]] == ["tree"]


def test_train_auto_candidate_override(modeling_session):
    task, _ = create_task(make_task_payload(candidates=["logistic", "tree", "forest"]))
    train_task(task["id"], "auto", ["forest"], None, "zhangsan")
    evaluation = get_evaluation(task["id"])
    assert [m["algorithm"] for m in evaluation["models"]] == ["forest"]


def test_train_failure_marks_failed(modeling_session, monkeypatch):
    from app.gateway.executor import ExecutionResult

    def fake(sql, action="", user_name="", source=None):
        if action != "建模取数":
            return ExecutionResult(ok=True, columns=["工序", "high_defect_risk"], rows=[["SMT贴片", 1]],
                                   row_count=1, tables=[], truncated=False)
        raise RuntimeError("数据库连接断开")

    monkeypatch.setattr(modeling_service, "execute_readonly", fake)
    task, _ = create_task(make_task_payload())
    with pytest.raises(ModelingError, match="训练失败"):
        train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    assert get_task(task["id"])["status"] == "failed"
    with modeling_session() as session:
        task_row = session.get(ModelingTask, task["id"])
        assert task_row.status == "failed"
        assert "reason" in (task_row.error_json or {})


def test_predict_requires_trained(modeling_session):
    task, _ = create_task(make_task_payload())
    with pytest.raises(TaskNotTrainedError):
        predict(task["id"], [{"工序": "SMT贴片", "产线": "L01", "停机时长": 500, "计划数量": 3000}], "zhangsan")


def test_predict_classification_multi_records(modeling_session):
    task, _ = create_task(make_task_payload())
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = list_models()[0]
    review_model(model["id"], "pass", "指标合格", "admin")
    publish_model(model["id"], "admin")

    out = predict(task["id"], [
        {"工序": "SMT贴片", "产线": "L01", "停机时长": 480, "计划数量": 3200},
        {"工序": "未来新增工序", "产线": "L04", "停机时长": None, "计划数量": 2100},
    ], "zhangsan")
    assert len(out["predictions"]) == 2
    for item in out["predictions"]:
        assert isinstance(item["prediction"], bool)
        assert 0 <= item["probability"] <= 1

    # 推理留痕 + 调用计数
    with modeling_session() as session:
        log = session.query(ModelingInferenceLog).first()
        assert log is not None and len(log.output_json["predictions"]) == 2
    assert any(m["calls"] == 1 for m in list_models())


def test_governance_lifecycle(modeling_session):
    task, _ = create_task(make_task_payload())
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = list_models()[0]

    # pending 直接发布是非法迁移（必须先复核）
    with pytest.raises(ModelingError, match="不允许模型发布"):
        publish_model(model["id"], "admin")
    # 复核通过 → 发布 → 下线
    reviewed = review_model(model["id"], "pass", "指标合格", "admin")
    assert reviewed["status"] == "reviewing"
    published = publish_model(model["id"], "admin")
    assert published["status"] == "published"
    assert published["tone"] == "green"
    retired = retire_model(model["id"], "admin")
    assert retired["status"] == "retired"
    # 已下线不可再操作
    with pytest.raises(ModelingError):
        retire_model(model["id"], "admin")
    with pytest.raises(ModelingError):
        review_model(model["id"], "pass", "", "admin")


def test_publish_blocks_model_that_is_worse_than_regression_baseline(modeling_session):
    task, _ = create_task(make_task_payload(
        title="停机时长预测",
        taskType="regression",
        targetField="停机时长",
        targetLabel="停机时长",
        features=["工序", "产线"],
        candidates=["linear"],
    ))
    with modeling_session() as session:
        model = GovernedModel(
            id="mdl-poor-regression", task_id=task["id"], name="弱回归模型", version=1,
            algorithm="linear", status="reviewing", owner_id="admin",
            metrics_json={"r2": -0.1}, features_json=["工序", "产线"], model_path="unused",
            feature_names_json=["工序", "产线"], feature_encoders_json={}, feature_importance_json=[],
            explanation_json={"qualityAssessment": {
                "status": "blocked", "publishable": False,
                "reason": "R² 不高于 0，模型没有优于平均值基线。",
            }},
        )
        session.add(model)
        session.commit()
    with pytest.raises(ModelingError, match="未达到发布门槛"):
        publish_model("mdl-poor-regression", "admin")


def test_reject_then_review_pass_and_retire(modeling_session):
    task, _ = create_task(make_task_payload())
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = list_models()[0]

    rejected = review_model(model["id"], "reject", "样本分布失衡", "admin")
    assert rejected["status"] == "rejected"
    # 已驳回不可直接发布，可重新复核通过或下线
    with pytest.raises(ModelingError, match="不允许模型发布"):
        publish_model(model["id"], "admin")
    reviewed = review_model(model["id"], "pass", "已重新校验特征", "admin")
    assert reviewed["status"] == "reviewing"
    republished = publish_model(model["id"], "admin")
    assert republished["status"] == "published"
    retired = retire_model(model["id"], "admin")
    assert retired["status"] == "retired"


def test_unknown_task_or_model(modeling_session):
    with pytest.raises(ModelNotFoundError):
        get_task("mt-not-exist")
    with pytest.raises(ModelNotFoundError):
        train_task("mt-not-exist", "auto", ["logistic"], None, "zhangsan")
    with pytest.raises(ModelNotFoundError):
        publish_model("mdl-not-exist", "admin")


def test_retrain_creates_versions_and_evaluation_history(modeling_session):
    task, _ = create_task(make_task_payload(candidates=["logistic"]))
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    first = list_models()[0]
    review_model(first["id"], "pass", "首版通过", "admin")
    publish_model(first["id"], "admin")

    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    models = sorted(list_models(), key=lambda item: item["version"])
    assert [item["version"] for item in models] == [1, 2]
    assert [item["trainingRevision"] for item in models] == [1, 2]
    assert models[0]["status"] == "published"
    assert models[1]["status"] == "personal"
    history = list_evaluations(task["id"])
    assert [item["trainingRevision"] for item in history] == [2, 1]
    with modeling_session() as session:
        assert session.query(ModelingEvaluation).count() == 2

    # 待复核的新版本不能抢占生产流量，仍由已发布 v1 推理。
    out = predict(task["id"], [{"工序": "SMT贴片", "产线": "L01", "停机时长": 480, "计划数量": 3200}], "zhangsan")
    assert out["modelId"] == first["id"]
    assert out["modelVersion"] == 1


def test_diagnostics_tuning_threshold_variant_and_rollback(modeling_session):
    task, _ = create_task(make_task_payload(candidates=["logistic"]))
    diagnostics = diagnose_task(task["id"], "zhangsan")
    assert diagnostics["status"] in {"ready", "warning"}
    assert diagnostics["score"] > 0
    diagnosed_task = get_task(task["id"])
    assert diagnosed_task["sampleCount"] == diagnostics["sampleCount"]
    assert "条样本" in diagnosed_task["sampleSummary"]

    result = train_task(
        task["id"], "auto", ["logistic"], None, "zhangsan", optimization_level="balanced",
    )
    trained = result["results"]["models"][0]
    assert trained["tuning"]["searched"] is True
    assert trained["explanation"]["applicabilityBoundary"]
    model = list_models(task_id=task["id"])[0]
    review_model(model["id"], "pass", "体检与阈值通过", "admin")
    publish_model(model["id"], "admin")

    variant = create_threshold_variant(model["id"], 0.35, "降低漏报", "admin")
    assert variant["status"] == "reviewing"
    assert variant["decisionThreshold"] == 0.35
    assert variant["thresholdPolicy"]["basedOnModelId"] == model["id"]
    publish_model(variant["id"], "admin")
    assert next(item for item in list_models(task_id=task["id"]) if item["id"] == model["id"])["status"] == "retired"

    restored = rollback_model(model["id"], "admin")
    assert restored["status"] == "published"
    assert next(item for item in list_models(task_id=task["id"]) if item["id"] == variant["id"])["status"] == "retired"


def test_agent_model_discovery_only_returns_published_same_source(modeling_session):
    task, _ = create_task(make_task_payload(candidates=["logistic"]))
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = list_models(task_id=task["id"])[0]
    assert find_reusable_models("识别高不良风险生产记录", 1, "classification") == []
    review_model(model["id"], "pass", "通过", "admin")
    publish_model(model["id"], "admin")
    matches = find_reusable_models("识别高不良风险生产记录", 1, "classification")
    assert matches and matches[0]["modelId"] == model["id"]
    assert find_reusable_models("识别高不良风险生产记录", 2, "classification") == []


def test_personal_tier_owner_only_serving(modeling_session):
    task, _ = create_task(make_task_payload())
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    personal = [m for m in list_models() if m["status"] == "personal"]
    assert personal, "门禁通过的模型应进入个人自用档"
    model = personal[0]
    assert model["personalActive"] is True and model["personalExpiresAt"]

    record = {"工序": "装配", "产线": "L1", "停机时长": 10, "计划数量": 100}
    mine = predict(task["id"], [record], "zhangsan")
    assert mine["scope"] == "personal" and mine["predictions"]
    # 非所有者拿不到别人的个人档
    with pytest.raises(TaskNotTrainedError):
        predict(task["id"], [record], "lisi")
    # 复用匹配同样带查看者身份
    assert find_reusable_models("识别高不良风险的生产记录", 1, "classification", viewer_id="zhangsan")
    assert not find_reusable_models("识别高不良风险的生产记录", 1, "classification", viewer_id="lisi")


def test_direct_publish_admin_only_path(modeling_session, monkeypatch):
    from app.config import settings as app_settings
    from app.modeling.service import publish_model_direct

    task, _ = create_task(make_task_payload(title="直发验证模型"))
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = next(m for m in list_models() if m["status"] == "personal")

    published = publish_model_direct(model["id"], "admin")
    assert published["status"] == "published" and published["directPublish"] is True

    # 四眼原则开启后直发被禁止
    task2, _ = create_task(make_task_payload(title="四眼原则模型"))
    train_task(task2["id"], "auto", ["logistic"], None, "zhangsan")
    model2 = next(m for m in list_models() if m["status"] == "personal" and m["taskId"] == task2["id"])
    monkeypatch.setattr(app_settings, "require_review_for_admin_publish", True)
    with pytest.raises(ModelingError):
        publish_model_direct(model2["id"], "admin")
    monkeypatch.setattr(app_settings, "require_review_for_admin_publish", False)
    # 常规发布通道仍强制复核：personal 直接 publish 非法
    with pytest.raises(ModelingError):
        publish_model(model2["id"], "admin")


def test_purge_retired_artifacts_respects_grace(modeling_session, tmp_path, monkeypatch):
    from app.config import settings as app_settings
    from app.modeling.service import purge_retired_artifacts
    from datetime import datetime, timedelta

    task, _ = create_task(make_task_payload(title="清理验证模型"))
    train_task(task["id"], "auto", ["logistic"], None, "zhangsan")
    model = next(m for m in list_models() if m["status"] == "personal")
    retire_model(model["id"], "admin")

    artifact = tmp_path / "old.joblib"
    artifact.write_bytes(b"fake")
    with modeling_session() as session:
        row = session.get(GovernedModel, model["id"])
        row.model_path = str(artifact)
        # 刚下线：宽限期内不清理
        row.retired_at = datetime.now() - timedelta(days=1)
        session.commit()
    assert purge_retired_artifacts()["purged"] == 0 and artifact.exists()

    with modeling_session() as session:
        row = session.get(GovernedModel, model["id"])
        row.retired_at = datetime.now() - timedelta(days=app_settings.modeling_artifact_grace_days + 1)
        session.commit()
    assert purge_retired_artifacts()["purged"] == 1 and not artifact.exists()
    # 元数据永留：行还在，路径清空
    with modeling_session() as session:
        row = session.get(GovernedModel, model["id"])
        assert row.model_path == ""


def test_clustering_auto_k_profiles_and_script_reproducible(modeling_session):
    task, _ = create_task(make_task_payload(
        title="产线运行模式聚类",
        taskType="clustering",
        targetField="",
        targetLabel="",
        goal="按运行模式给工序记录自动分群",
        candidates=["kmeans"],
    ))
    response = train_task(task["id"], "auto", ["kmeans"], None, "zhangsan")
    item = response["results"]["models"][0]
    k = item["tuning"]["bestParams"]["n_clusters"]
    assert 2 <= k <= 8, "k 必须来自轮廓系数自动选择范围"
    profiles = item["explanation"]["clusterProfiles"]
    assert profiles, "聚类必须输出群画像"
    assert profiles[0]["drivers"], "画像需含区分特征"
    assert abs(sum(p["share"] for p in profiles) - 100.0) < 1.5
    script = item["explanation"]["script"]
    compile(script, "<modeling-script>", "exec")
    assert "SQL = " in script and "hyperparams" in script


def test_linear_model_exposes_coefficient_importance(modeling_session):
    task, _ = create_task(make_task_payload(
        title="停机时长回归",
        taskType="regression",
        targetField="停机时长",
        targetLabel="停机时长",
        goal="预测停机时长",
        features=["工序", "产线", "计划数量"],
        candidates=["linear"],
    ))
    response = train_task(task["id"], "auto", ["linear"], None, "zhangsan")
    item = response["results"]["models"][0]
    assert item["featureImportance"], "线性模型需给出系数归一化重要度解释"


def test_run_prediction_returns_business_card(modeling_session):
    from app.modeling.service import run_prediction

    task, _ = create_task(make_task_payload(candidates=["tree"]))
    train_task(task["id"], "auto", ["tree"], None, "zhangsan")
    model = list_models()[0]
    review_model(model["id"], "pass", "指标达标", "admin")
    publish_model(model["id"], "admin")

    card = run_prediction(task["id"], "zhangsan")
    assert card["kind"] == "prediction" and card["scope"] == "published"
    assert card["sampleCount"] > 0 and "高风险" in card["headline"]
    assert card["stats"]["share"] is not None
    assert isinstance(card["factors"], list)

    # 个人档同样可预测（owner 本人），scope 标注个人
    task2, _ = create_task(make_task_payload(title="个人档预测", candidates=["tree"]))
    train_task(task2["id"], "auto", ["tree"], None, "lisi")
    personal_card = run_prediction(task2["id"], "lisi")
    assert personal_card["scope"] == "personal"


def test_thorough_level_uses_flaml_with_grid_fallback(modeling_session, monkeypatch):
    task, _ = create_task(make_task_payload(title="FLAML 深入搜索", candidates=["forest"]))
    response = train_task(task["id"], "auto", ["forest"], None, "zhangsan", optimization_level="thorough")
    tuning = response["results"]["models"][0]["tuning"]
    assert tuning.get("engine") == "flaml", "已安装 FLAML 时 thorough 档应走限时搜索"
    assert tuning["searched"] is True and tuning["timeBudgetSeconds"] == 20
    assert tuning["candidateCount"] >= 1

    # FLAML 不可用时静默回退网格档，训练不失败
    from app.modeling import engine as modeling_engine
    monkeypatch.setattr(modeling_engine, "_flaml_search", lambda *_args, **_kwargs: None)
    task2, _ = create_task(make_task_payload(title="FLAML 回退验证", candidates=["forest"]))
    response2 = train_task(task2["id"], "auto", ["forest"], None, "zhangsan", optimization_level="thorough")
    tuning2 = response2["results"]["models"][0]["tuning"]
    assert "engine" not in tuning2 and tuning2["searched"] is True


def test_prediction_trace_and_drift_backfill(modeling_session):
    from app.modeling.service import drift_report, run_prediction

    task, _ = create_task(make_task_payload(candidates=["tree"]))
    train_task(task["id"], "auto", ["tree"], None, "zhangsan")
    model = list_models()[0]
    review_model(model["id"], "pass", "指标达标", "admin")
    publish_model(model["id"], "admin")
    run_prediction(task["id"], "zhangsan")

    report = drift_report("tenant-default")
    assert report["summary"]["models"] == 1
    item = report["items"][0]
    assert item["traces"] >= 1 and item["filled"] >= 1, "实际值应自动回填"
    assert item["avgDeviation"] is not None
    assert item["status"] in {"normal", "watch", "drift"}
