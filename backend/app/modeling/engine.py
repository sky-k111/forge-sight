"""建模训练引擎：纯 sklearn/pandas 实现，无数据库依赖，可独立单测。

职责划分：
- prepare_dataset：取数后的 DataFrame → 编码/填充/缩放/分层划分的训练数据
- train_model：按算法与任务类型分派训练，返回 sklearn 估计器
- evaluate_model：按任务类型计算评估指标
- extract_importance：提取特征重要性（或系数归一化近似）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
    silhouette_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

# 算法目录：与前端 modelCatalog 对齐；family 决定训练分派
ALGORITHMS = [
    {"id": "linear", "name": "线性回归", "type": "数值预测", "scene": "预测产量、停机时长", "accent": "blue", "family": "regression"},
    {"id": "tree", "name": "决策树", "type": "分类/回归", "scene": "可解释规则与阈值分析", "accent": "teal", "family": "tree"},
    {"id": "forest", "name": "随机森林", "type": "分类/回归", "scene": "稳健的集成预测与重要性", "accent": "violet", "family": "forest"},
    {"id": "logistic", "name": "逻辑回归", "type": "二分类", "scene": "风险识别、是否判定", "accent": "amber", "family": "logistic"},
    {"id": "kmeans", "name": "KMeans 聚类", "type": "聚类", "scene": "样本分组与模式发现", "accent": "rose", "family": "clustering"},
    {"id": "isolation", "name": "Isolation Forest", "type": "异常检测", "scene": "离群点与异常识别", "accent": "indigo", "family": "anomaly"},
]

ALGORITHM_BY_ID = {item["id"]: item for item in ALGORITHMS}

# 各算法可服务的任务类型（候选算法过滤用）
SUPPORTED_FAMILIES = {
    "linear": {"regression"},
    "tree": {"classification", "regression"},
    "forest": {"classification", "regression"},
    "logistic": {"classification"},
    "kmeans": {"clustering"},
    "isolation": {"anomaly"},
}

TASK_TYPE_LABELS = {
    "regression": "回归预测",
    "classification": "二分类",
    "clustering": "聚类",
    "anomaly": "异常检测",
}

MIN_SAMPLE_COUNT = 50
TEST_SIZE = 0.25
RANDOM_STATE = 42
MAX_IMPORTANCE_FEATURES = 10


class ModelingEngineError(ValueError):
    """建模引擎业务错误（含面向用户的中文说明）。"""


def round4(value: float | None) -> float | None:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return round(float(value), 4)


def prepare_dataset(
    df: pd.DataFrame,
    task_type: str,
    target_field: str = "",
    features: list[str] | None = None,
) -> dict:
    """DataFrame → 训练/测试数据 + 编码器/填充策略元数据。

    返回 dict：
    - X_train/X_test/y_train/y_test：无监督任务 y 全为 None
    - encoders: {列: {原始值: 编码}}
    - impute_policy: {列: "median"|"most_frequent"|None}
    - feature_names: 进入模型的列序（推理时按此对齐）
    - sample_count/train_count/test_count
    - target_type: "binary"|"continuous"|None（披露自动二分类）
    """
    if df is None or df.empty:
        raise ModelingEngineError("训练集为空，请检查取数 SQL")
    df = df.copy()
    # 全空列删除
    df = df.dropna(axis=1, how="all")

    # 目标列必须先于特征子集化取出（features 列表通常不含目标列）
    target_type = None
    if task_type in ("classification", "regression"):
        if not target_field:
            raise ModelingEngineError("分类/回归任务必须指定目标列 target_field")
        if target_field not in df.columns:
            raise ModelingEngineError(f"目标列 {target_field} 在数据集中不存在")
        y_raw = df.pop(target_field)
        # 数值目标且唯一值 ≤ 2 → 自动视为二分类
        if task_type == "regression" and y_raw.nunique(dropna=True) <= 2:
            task_type = "classification"
            target_type = "binary"
        elif task_type == "classification":
            target_type = "binary"
            if pd.api.types.is_numeric_dtype(y_raw):
                y_raw = y_raw.astype("int")
        else:
            target_type = "continuous"
            y_raw = pd.to_numeric(y_raw, errors="coerce")

    # 特征子集化（目标列已取出，features 中若含目标列则忽略）
    if features:
        feature_set = [col for col in features if col != target_field]
        missing = [col for col in feature_set if col not in df.columns]
        if missing:
            raise ModelingEngineError(f"特征列在数据集中不存在：{', '.join(missing)}")
        df = df[list(feature_set)].copy()

    # 类别列编码（fit 于全量；推理时按 encoders 反向还原）。
    # pandas 3.0 默认字符串为 str dtype，is_string_dtype 兼容 object 与 str 两种。
    encoders: dict[str, dict] = {}
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]) or isinstance(df[col].dtype, pd.CategoricalDtype):
            clean = df[col].fillna("__missing__").astype(str)
            codes = sorted(clean.unique())
            encoders[col] = {str(value): idx for idx, value in enumerate(codes)}
            df[col] = clean.map(encoders[col]).fillna(0).astype(int)

    # 缺失填充：数值中位数、类别众数
    impute_policy: dict[str, str | None] = {}
    for col in df.columns:
        if df[col].isna().any():
            if pd.api.types.is_numeric_dtype(df[col]):
                strategy = "median"
            else:
                strategy = "most_frequent"
            impute_policy[col] = strategy
            imputer = SimpleImputer(strategy=strategy)
            df[col] = imputer.fit_transform(df[[col]])[:, 0]

    feature_names = list(df.columns)
    if not feature_names:
        raise ModelingEngineError("没有可用的特征列")

    # 无监督任务：缩放特征
    scaler = None
    if task_type in ("clustering", "anomaly"):
        scaler = StandardScaler()
        df_scaled = pd.DataFrame(scaler.fit_transform(df), columns=feature_names)
    else:
        df_scaled = df

    sample_count = int(len(df))
    if sample_count < MIN_SAMPLE_COUNT:
        raise ModelingEngineError(f"样本过少（{sample_count} 行），至少需要 {MIN_SAMPLE_COUNT} 行")

    result: dict = {
        "encoders": encoders,
        "impute_policy": impute_policy,
        "feature_names": feature_names,
        "sample_count": sample_count,
        "target_type": target_type,
        "scaler": scaler,
        "task_type": task_type,
        "features": features or feature_names,
    }

    if task_type in ("clustering", "anomaly"):
        result.update({"X_train": df_scaled, "X_test": df_scaled, "y_train": None, "y_test": None})
        result["train_count"] = sample_count
        result["test_count"] = 0
        return result

    y = y_raw.astype(float)
    X_train, X_test, y_train, y_test = train_test_split(
        df_scaled, y, test_size=TEST_SIZE, random_state=RANDOM_STATE,
        stratify=y if task_type == "classification" and y.nunique() == 2 else None,
    )
    result.update({
        "X_train": X_train, "X_test": X_test, "y_train": y_train, "y_test": y_test,
        "train_count": int(len(X_train)), "test_count": int(len(X_test)),
    })
    return result


def train_model(task_type: str, algorithm: str, X_train, y_train, sample_count: int):
    """按算法与任务类型分派训练，返回 sklearn 估计器。"""
    family = ALGORITHM_BY_ID.get(algorithm)
    if family is None:
        raise ModelingEngineError(f"未知算法：{algorithm}")
    if task_type not in SUPPORTED_FAMILIES[algorithm]:
        raise ModelingEngineError(
            f"算法「{family['name']}」不支持{task_type}任务，请选择其他候选算法"
        )

    if algorithm == "linear":
        model = LinearRegression()
    elif algorithm == "tree":
        model = DecisionTreeRegressor(random_state=RANDOM_STATE) if task_type == "regression" else DecisionTreeClassifier(random_state=RANDOM_STATE)
    elif algorithm == "forest":
        model = RandomForestRegressor(n_estimators=100, random_state=RANDOM_STATE) if task_type == "regression" else RandomForestClassifier(n_estimators=100, random_state=RANDOM_STATE)
    elif algorithm == "logistic":
        model = LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)
    elif algorithm == "kmeans":
        n_clusters = max(2, min(3, sample_count // 100))
        model = KMeans(n_clusters=n_clusters, n_init=10, random_state=RANDOM_STATE)
    elif algorithm == "isolation":
        model = IsolationForest(n_estimators=100, contamination="auto", random_state=RANDOM_STATE)
    else:  # pragma: no cover - 已在 family 校验拦截
        raise ModelingEngineError(f"未知算法：{algorithm}")

    if task_type in ("clustering", "anomaly"):
        model.fit(X_train)
    else:
        model.fit(X_train, y_train)
    return model


def evaluate_model(task_type: str, algorithm: str, model, X_test, y_test, sample_count: int) -> dict:
    """按任务类型计算评估指标；分类附混淆矩阵，百分比指标按 % 返回。"""
    metrics: dict = {}
    if task_type == "classification":
        y_pred = model.predict(X_test)
        metrics["accuracy"] = round(float(accuracy_score(y_test, y_pred)) * 100, 1)
        metrics["precision"] = round(float(precision_score(y_test, y_pred, zero_division=0)) * 100, 1)
        metrics["recall"] = round(float(recall_score(y_test, y_pred, zero_division=0)) * 100, 1)
        metrics["f1"] = round4(f1_score(y_test, y_pred, zero_division=0))
        if hasattr(model, "predict_proba"):
            try:
                proba = model.predict_proba(X_test)[:, 1]
                metrics["auc"] = round4(roc_auc_score(y_test, proba))
            except ValueError:
                metrics["auc"] = None
        matrix = confusion_matrix(y_test, y_pred).tolist()
        metrics["confusionMatrix"] = matrix
    elif task_type == "regression":
        y_pred = model.predict(X_test)
        metrics["r2"] = round4(r2_score(y_test, y_pred))
        metrics["mae"] = round4(mean_absolute_error(y_test, y_pred))
        metrics["rmse"] = round4(float(np.sqrt(mean_squared_error(y_test, y_pred))))
    elif task_type == "clustering":
        labels = model.labels_
        if len(set(labels)) > 1 and sample_count > 1:
            sample = X_test
            if len(sample) > 5000:  # 轮廓系数 O(n²)，大样本抽样近似
                sample = sample.sample(5000, random_state=RANDOM_STATE)
                labels_sample = model.predict(sample)
            else:
                labels_sample = labels[: len(sample)]
            metrics["silhouette"] = round4(silhouette_score(sample, labels_sample))
        metrics["clusterCount"] = int(model.n_clusters)
    elif task_type == "anomaly":
        labels = model.predict(X_test)
        anomaly_rate = round(float(np.mean(labels == -1)) * 100, 1)
        metrics["anomalyRate"] = anomaly_rate
        metrics["contamination"] = anomaly_rate / 100
    return metrics


def extract_importance(algorithm: str, model, feature_names: list[str]) -> list[dict]:
    """特征重要性：树模型用 feature_importances_，线性模型用系数绝对值归一化。

    聚类/异常检测无原生重要性，返回空列表（前端展示指标而非重要性）。
    """
    if algorithm in ("tree", "forest") and hasattr(model, "feature_importances_"):
        raw = np.asarray(model.feature_importances_, dtype=float)
        total = raw.sum()
        if total <= 0:
            return []
        items = [{"name": name, "importance": round(float(value / total) * 100, 1)}
                 for name, value in zip(feature_names, raw, strict=True)]
    elif algorithm in ("linear", "logistic") and hasattr(model, "coef_"):
        coefs = np.asarray(model.coef_, dtype=float).ravel()
        scale = np.abs(coefs).sum()
        if scale <= 0:
            return []
        items = [{"name": name, "importance": round(float(abs(value) / scale) * 100, 1)}
                 for name, value in zip(feature_names, coefs, strict=True)]
    else:
        return []
    items.sort(key=lambda item: item["importance"], reverse=True)
    return items[:MAX_IMPORTANCE_FEATURES]


def encode_input_record(record: dict, encoders: dict, feature_names: list[str]) -> list:
    """推理输入行 → 模型特征向量（类别编码还原 + 缺省 0）。"""
    row: list = []
    for col in feature_names:
        value = record.get(col)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            row.append(0)
            continue
        encoder = encoders.get(col)
        if encoder is not None:
            row.append(encoder.get(str(value), 0))
        else:
            try:
                row.append(float(value))
            except (TypeError, ValueError):
                row.append(0)
    return row


def summarize_metric(task_type: str, algorithm: str, metrics: dict) -> str:
    """生成任务级展示摘要，如「AUC 0.986」。"""
    if task_type == "classification":
        return f"AUC {metrics.get('auc') or '—'}"
    if task_type == "regression":
        return f"R² {metrics.get('r2') or '—'}"
    if task_type == "clustering":
        return f"轮廓系数 {metrics.get('silhouette') or '—'}"
    if task_type == "anomaly":
        return f"异常占比 {metrics.get('anomalyRate') or '—'}%"
    return ""
