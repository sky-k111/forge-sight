"""训练引擎纯函数测试：六算法分派、预处理、评估指标与特征重要性。"""

import numpy as np
import pandas as pd
import pytest

from app.modeling.engine import (
    ALGORITHMS,
    MIN_SAMPLE_COUNT,
    encode_input_record,
    evaluate_model,
    extract_importance,
    prepare_dataset,
    summarize_metric,
    train_model,
    ModelingEngineError,
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
    assert set(prepared["encoders"]) == {"工序", "产线"}  # 类别列已编码
    assert "median" in prepared["impute_policy"].values()  # 数值缺失中位数填充
    assert not prepared["X_train"].isna().any().any()
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
        target = "high_defect_risk" if task_type in ("classification", "regression") else ""
        prepared = prepare_dataset(df, task_type, target_field=target)
        model = train_model(task_type, algorithm, prepared["X_train"], prepared["y_train"], prepared["sample_count"])
        assert model is not None, algorithm
        metrics = evaluate_model(
            task_type, algorithm, model,
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
