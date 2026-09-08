"""训练引擎纯函数测试：六算法分派、预处理、评估指标与特征重要性。"""

import numpy as np
import pandas as pd
import pytest

from app.modeling.engine import (
    ALGORITHMS,
    ModelingEngineError,
    diagnose_dataset,
    encode_input_record,
    evaluate_model,
    extract_importance,
    prepare_dataset,
    summarize_metric,
    train_model,
    tune_model,
)


def make_classification_df(n=400):
    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "工序": rng.choice(["SMT贴片", "AOI检测", "功能测试"], size=n),
            "产线": rng.choice(["L01", "L04"], size=n),
            "停机时长": rng.integers(0, 600, size=n).astype(float),
            "计划数量": rng.integers(100, 5000, size=n).astype(float),
        }
    )
    # 停机时长越长、不良风险越高；少量缺失与噪声
    risk = (df["停机时长"] > 300).astype(int) | rng.choice([0, 1], size=n, p=[0.85, 0.15])
    df["high_defect_risk"] = risk
    df.loc[rng.choice(n, 5, replace=False), "停机时长"] = np.nan
    return df


def test_algorithm_catalog():
    assert len(ALGORITHMS) == 6
    ids = {item["id"] for item in ALGORITHMS}
    assert ids == {"linear", "tree", "forest", "logistic", "kmeans", "isolation"}


def test_prepare_dataset_classification():
    df = make_classification_df()
    prepared = prepare_dataset(df, "classification", target_field="high_defect_risk")
    assert prepared["task_type"] == "classification"
    assert prepared["X_train"].shape[0] + prepared["X_test"].shape[0] == 400
    assert prepared["feature_names"] == ["工序", "产线", "停机时长", "计划数量"]
    assert prepared["encoders"] == {}  # 编码器只允许在训练折内拟合并随模型持久化
    assert prepared["impute_policy"]["numeric"] == "median"
    assert prepared["X_train"].isna().any().any()  # 原始切分不提前填充，避免测试集泄漏
    assert prepared["y_train"].nunique() == 2


def test_prepare_dataset_auto_binary_regression():
    """连续目标但唯一值 ≤2 → 自动转二分类并披露。"""
    df = make_classification_df()
    prepared = prepare_dataset(df, "regression", target_field="high_defect_risk")
    assert prepared["task_type"] == "classification"
    assert prepared["target_type"] == "binary"


def test_prepare_dataset_rejects_small_samples():
    with pytest.raises(ModelingEngineError, match="样本过少"):
        prepare_dataset(make_classification_df(n=30), "classification", target_field="high_defect_risk")


def test_prepare_dataset_rejects_missing_target():
    with pytest.raises(ModelingEngineError, match="目标列"):
        prepare_dataset(make_classification_df(), "classification", target_field="不存在")


def test_train_and_evaluate_all_families():
    df = make_classification_df(600)
    df["continuous_target"] = (
        df["停机时长"].fillna(df["停机时长"].median()) * 0.7
        + df["计划数量"] * 0.03
    )
    cases = [
        ("classification", "logistic", {"auc", "f1", "precision", "recall", "accuracy", "confusionMatrix"}),
        ("classification", "forest", {"auc", "f1", "precision", "recall", "accuracy", "confusionMatrix"}),
        ("classification", "tree", {"auc", "f1", "precision", "recall", "accuracy", "confusionMatrix"}),
        ("regression", "linear", {"r2", "mae", "rmse"}),
        ("regression", "forest", {"r2", "mae", "rmse"}),
        ("clustering", "kmeans", {"silhouette", "clusterCount"}),
        ("anomaly", "isolation", {"anomalyRate", "contamination"}),
    ]
    for task_type, algorithm, expected_keys in cases:
        target = "continuous_target" if task_type == "regression" else (
            "high_defect_risk" if task_type == "classification" else ""
        )
        prepared = prepare_dataset(df, task_type, target_field=target)
        effective_type = prepared["task_type"]
        model = train_model(effective_type, algorithm, prepared["X_train"], prepared["y_train"], prepared["sample_count"])
        assert model is not None, algorithm
        metrics = evaluate_model(
            effective_type, algorithm, model,
            prepared["X_test"], prepared["y_test"], prepared["sample_count"],
        )
        assert expected_keys.issubset(metrics.keys()), (algorithm, metrics)
        if "confusionMatrix" in metrics:
            assert len(metrics["confusionMatrix"]) == 2


def test_algorithm_task_mismatch_rejected():
    prepared = prepare_dataset(make_classification_df(200), "classification", target_field="high_defect_risk")
    with pytest.raises(ModelingEngineError, match="不支持"):
        train_model("classification", "linear", prepared["X_train"], prepared["y_train"], 200)


def test_feature_importance_tree_and_linear():
    df = make_classification_df(600)
    prepared = prepare_dataset(df, "classification", target_field="high_defect_risk")
    model = train_model("classification", "forest", prepared["X_train"], prepared["y_train"], 600)
    importance = extract_importance("forest", model, prepared["feature_names"])
    assert len(importance) > 0
    assert all({"name", "importance"}.issubset(item) for item in importance)
    assert abs(sum(item["importance"] for item in importance) - 100) < 0.5
    # 聚类无原生重要性 → 空列表
    clustering = prepare_dataset(df, "clustering")
    km = train_model("clustering", "kmeans", clustering["X_train"], None, 600)
    assert extract_importance("kmeans", km, clustering["feature_names"]) == []


def test_encode_input_record_roundtrip():
    encoders = {"工序": {"SMT贴片": 0, "AOI检测": 1}}
    row = encode_input_record({"工序": "AOI检测", "停机时长": 120}, encoders, ["工序", "停机时长"])
    assert row == [1, 120]
    unknown = encode_input_record({"工序": "未知工序", "停机时长": None}, encoders, ["工序", "停机时长"])
    assert unknown == [0, 0]


def test_summarize_metric():
    assert summarize_metric("classification", "logistic", {"auc": 0.986}) == "AUC 0.986"
    assert summarize_metric("anomaly", "isolation", {"anomalyRate": 6.8}) == "异常占比 6.8%"


def test_group_split_keeps_work_orders_isolated():
    df = make_classification_df(300)
    df["work_order_id"] = [f"WO-{index // 5:03d}" for index in range(len(df))]
    prepared = prepare_dataset(
        df,
        "classification",
        target_field="high_defect_risk",
        features=["work_order_id", "工序", "停机时长"],
        split_strategy="group",
        group_field="work_order_id",
    )
    assert prepared["split"]["strategy"] == "group"
    assert set(prepared["X_train"]["work_order_id"]).isdisjoint(prepared["X_test"]["work_order_id"])


def test_time_split_fits_categories_only_on_past_rows():
    df = make_classification_df(200)
    df["record_date"] = pd.date_range("2026-01-01", periods=len(df), freq="D")
    df.loc[160:, "工序"] = "未来新增工序"
    prepared = prepare_dataset(
        df,
        "classification",
        target_field="high_defect_risk",
        features=["工序", "产线", "停机时长"],
        split_strategy="time",
        time_field="record_date",
    )
    assert prepared["split"]["strategy"] == "time"
    assert prepared["split"]["trainEnd"] < prepared["split"]["testStart"]
    model = train_model(
        "classification", "logistic", prepared["X_train"], prepared["y_train"], prepared["sample_count"],
    )
    encoder = model.named_steps["preprocessor"].named_transformers_["categorical"].named_steps["encoder"]
    categories = {str(value) for values in encoder.categories_ for value in values}
    assert "未来新增工序" not in categories
    assert len(model.predict(prepared["X_test"])) == prepared["test_count"]


def test_dataset_diagnostics_blocks_bad_labels_and_recommends_group_split():
    df = make_classification_df(240)
    df["work_order_id"] = [f"WO-{index // 4:03d}" for index in range(len(df))]
    report = diagnose_dataset(
        df,
        "classification",
        target_field="high_defect_risk",
        features=["work_order_id", "工序", "停机时长"],
        split_strategy="auto",
        group_field="work_order_id",
    )
    assert report["status"] in {"ready", "warning"}
    assert report["recommendedSplit"] == "group"
    assert report["sampleCount"] == 240
    assert {item["key"] for item in report["checks"]} >= {"sample_count", "target", "leakage", "split"}

    broken = df.copy()
    broken["high_defect_risk"] = 1
    blocked = diagnose_dataset(broken, "classification", target_field="high_defect_risk")
    assert blocked["status"] == "blocked"
    assert any("2 个类别" in item for item in blocked["blockers"])


def test_dataset_diagnostics_blocks_text_regression_target_before_training():
    df = make_classification_df(240)
    df["output_id"] = [f"OUT-{index:05d}" for index in range(len(df))]

    report = diagnose_dataset(
        df,
        "regression",
        target_field="output_id",
        features=["工序", "停机时长", "计划数量"],
    )

    assert report["status"] == "blocked"
    assert any("非数值" in item for item in report["blockers"])
    target_check = next(item for item in report["checks"] if item["key"] == "target")
    assert target_check["status"] == "fail"


def test_balanced_automl_uses_training_cv_and_returns_threshold_policy():
    prepared = prepare_dataset(make_classification_df(300), "classification", target_field="high_defect_risk")
    model, tuning = tune_model("classification", "logistic", prepared, optimization_level="balanced")
    assert tuning["searched"] is True
    assert tuning["candidateCount"] == 3
    assert tuning["cvPolicy"]
    assert 0.2 <= tuning["decisionThreshold"] <= 0.8
    assert tuning["thresholdPolicy"]["source"] in {"training_oof", "default"}
    metrics = evaluate_model(
        "classification", "logistic", model,
        prepared["X_test"], prepared["y_test"], prepared["sample_count"],
        decision_threshold=tuning["decisionThreshold"],
    )
    assert len(metrics["confusionMatrix"]) == 2


def test_automl_search_failure_falls_back_to_a_trainable_default(monkeypatch):
    class BrokenSearch:
        def __init__(self, *args, **kwargs):
            pass

        def fit(self, *args, **kwargs):
            raise ValueError("single-class validation fold")

    monkeypatch.setattr("app.modeling.engine.GridSearchCV", BrokenSearch)
    prepared = prepare_dataset(make_classification_df(220), "classification", target_field="high_defect_risk")
    model, tuning = tune_model("classification", "logistic", prepared, optimization_level="balanced")

    assert tuning["searched"] is False
    assert "single-class validation fold" in tuning["fallbackReason"]
    assert len(model.predict(prepared["X_test"])) == prepared["test_count"]
