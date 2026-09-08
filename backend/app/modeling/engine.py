"""Leakage-safe sklearn training primitives for ForgeSight modeling.

The engine owns three production boundaries: split rows before preprocessing is
fitted, serialize preprocessing together with the estimator, and disclose the
split policy plus likely target-leakage signals in every evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
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
from sklearn.model_selection import (
    GridSearchCV,
    GroupKFold,
    GroupShuffleSplit,
    KFold,
    StratifiedKFold,
    TimeSeriesSplit,
    cross_val_predict,
    train_test_split,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

ALGORITHMS = [
    {"id": "linear", "name": "线性回归", "type": "数值预测", "scene": "预测产量、停机时长", "accent": "blue", "family": "regression"},
    {"id": "tree", "name": "决策树", "type": "分类/回归", "scene": "可解释规则与阈值分析", "accent": "teal", "family": "tree"},
    {"id": "forest", "name": "随机森林", "type": "分类/回归", "scene": "稳健的集成预测与重要性", "accent": "violet", "family": "forest"},
    {"id": "logistic", "name": "逻辑回归", "type": "二分类", "scene": "风险识别、是否判定", "accent": "amber", "family": "logistic"},
    {"id": "kmeans", "name": "KMeans 聚类", "type": "聚类", "scene": "样本分组与模式发现", "accent": "rose", "family": "clustering"},
    {"id": "isolation", "name": "Isolation Forest", "type": "异常检测", "scene": "离群点与异常识别", "accent": "indigo", "family": "anomaly"},
]
ALGORITHM_BY_ID = {item["id"]: item for item in ALGORITHMS}
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

_GROUP_NAMES = (
    "work_order_id", "workorder_id", "order_id", "batch_id", "lot_id",
    "工单id", "工单编号", "批次id", "批次号",
)
_TIME_NAMES = (
    "stat_date", "record_date", "inspection_date", "production_date", "output_date",
    "event_time", "created_at", "timestamp", "date", "datetime", "日期", "时间",
)


class ModelingEngineError(ValueError):
    """A user-facing modeling validation error."""


def round4(value: float | None) -> float | None:
    if value is None or not np.isfinite(float(value)):
        return None
    return round(float(value), 4)


def _find_named_column(columns: list[str], candidates: tuple[str, ...]) -> str:
    lowered = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    for column in columns:
        normalized = str(column).lower()
        if any(candidate.lower() in normalized for candidate in candidates):
            return str(column)
    return ""


def _safe_stratify(y: pd.Series | None) -> pd.Series | None:
    if y is None or y.nunique(dropna=True) != 2:
        return None
    counts = y.value_counts()
    return y if not counts.empty and int(counts.min()) >= 2 else None


def _target_values(y: pd.Series, task_type: str) -> tuple[pd.Series, str, dict[str, int]]:
    if task_type == "classification":
        values = sorted(str(value) for value in y.dropna().unique())
        if len(values) != 2:
            raise ModelingEngineError(f"二分类目标必须恰好包含 2 个类别，当前为 {len(values)} 个")
        mapping = {value: index for index, value in enumerate(values)}
        return y.astype(str).map(mapping).astype(int), "binary", mapping
    numeric = pd.to_numeric(y, errors="coerce")
    if numeric.isna().any():
        invalid_count = int(numeric.isna().sum())
        raise ModelingEngineError(
            f"回归目标包含 {invalid_count} 个无法转换为数值的内容，"
            "请确认目标是数量、时长、金额等数值字段，而不是 ID 或编号"
        )
    return numeric.astype(float), "continuous", {}


def _leakage_warnings(X: pd.DataFrame, y: pd.Series | None) -> list[str]:
    warnings: list[str] = []
    if y is None:
        return warnings
    y_numeric = pd.to_numeric(y, errors="coerce")
    for column in X.columns:
        series = X[column]
        if series.nunique(dropna=False) == len(series):
            warnings.append(f"特征「{column}」近似唯一标识，可能记住样本而非业务规律")
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric.notna() & y_numeric.notna()
        if int(valid.sum()) >= 10 and numeric[valid].nunique() > 1 and y_numeric[valid].nunique() > 1:
            correlation = abs(float(numeric[valid].corr(y_numeric[valid])))
            if np.isfinite(correlation) and correlation >= 0.995:
                warnings.append(f"特征「{column}」与目标几乎完全相关（|r|={correlation:.3f}），请检查标签泄漏")
    return warnings


def diagnose_dataset(
    df: pd.DataFrame,
    task_type: str,
    target_field: str = "",
    features: list[str] | None = None,
    *,
    split_strategy: str = "auto",
    time_field: str = "",
    group_field: str = "",
) -> dict:
    """Return a business-readable, JSON-safe training readiness report."""
    if df is None or df.empty:
        return {
            "status": "blocked", "score": 0, "sampleCount": 0,
            "blockers": ["训练数据为空"], "warnings": [], "checks": [],
        }
    frame = df.copy()
    columns = [str(column) for column in frame.columns]
    selected_time = time_field or _find_named_column(columns, _TIME_NAMES)
    selected_group = group_field or _find_named_column(columns, _GROUP_NAMES)
    feature_names = list(features or [column for column in columns if column != target_field])
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    sample_count = int(len(frame))
    if sample_count < MIN_SAMPLE_COUNT:
        blockers.append(f"有效样本仅 {sample_count} 条，至少需要 {MIN_SAMPLE_COUNT} 条")
    checks.append({
        "key": "sample_count", "label": "样本规模", "status": "pass" if sample_count >= MIN_SAMPLE_COUNT else "fail",
        "value": sample_count, "message": f"共读取 {sample_count:,} 条训练记录",
    })

    missing_target = 0
    class_distribution: list[dict[str, Any]] = []
    if task_type in {"classification", "regression"}:
        if not target_field or target_field not in frame.columns:
            blockers.append(f"目标字段「{target_field or '未指定'}」不存在")
        else:
            target = frame[target_field]
            missing_target = int(target.isna().sum())
            if missing_target:
                ratio = missing_target / sample_count
                message = f"目标字段缺失 {missing_target} 条（{ratio:.1%}）"
                (blockers if ratio >= 0.2 else warnings).append(message)
            usable_target = target.dropna()
            regression_invalid = 0
            if task_type == "regression" and usable_target.nunique() > 2:
                numeric_target = pd.to_numeric(usable_target, errors="coerce")
                regression_invalid = int(numeric_target.isna().sum())
                if regression_invalid:
                    blockers.append(
                        f"回归目标含 {regression_invalid} 条非数值，请选择数量、时长或金额字段"
                    )
            if task_type == "classification" or usable_target.nunique() <= 2:
                counts = usable_target.astype(str).value_counts()
                class_distribution = [
                    {"label": str(label), "count": int(count), "ratio": round(float(count / max(1, len(usable_target))), 4)}
                    for label, count in counts.items()
                ]
                if len(counts) != 2:
                    blockers.append(f"二分类目标需要 2 个类别，当前为 {len(counts)} 个")
                elif int(counts.min()) < 10:
                    blockers.append(f"少数类别仅 {int(counts.min())} 条，无法稳定评估")
                elif float(counts.min() / counts.sum()) < 0.1:
                    warnings.append(f"类别分布失衡，少数类别占比仅 {float(counts.min() / counts.sum()):.1%}")
            checks.append({
                "key": "target", "label": "预测目标", "status": "fail" if any("目标" in item or "类别" in item for item in blockers) else ("warn" if missing_target else "pass"),
                "value": target_field,
                "message": (
                    f"回归目标含 {regression_invalid} 条非数值"
                    if regression_invalid
                    else f"目标字段缺失 {missing_target} 条"
                    if missing_target
                    else "目标字段完整"
                ),
            })

    missing_features: list[dict[str, Any]] = []
    constant_features: list[str] = []
    usable_features = [column for column in feature_names if column in frame.columns]
    for column in usable_features:
        ratio = float(frame[column].isna().mean())
        if ratio > 0:
            missing_features.append({"name": column, "ratio": round(ratio, 4)})
        if frame[column].nunique(dropna=True) <= 1:
            constant_features.append(column)
    high_missing = [item for item in missing_features if item["ratio"] >= 0.4]
    if high_missing:
        warnings.append("高缺失特征：" + "、".join(f"{item['name']} {item['ratio']:.0%}" for item in high_missing[:5]))
    if constant_features:
        warnings.append("无区分度特征：" + "、".join(constant_features[:5]))
    checks.append({
        "key": "features", "label": "输入特征", "status": "warn" if high_missing or constant_features else "pass",
        "value": len(usable_features), "message": f"{len(usable_features)} 个特征，{len(missing_features)} 个存在缺失",
    })

    y_for_leakage = None
    if target_field in frame.columns:
        raw = frame[target_field]
        try:
            effective_type = "classification" if task_type == "regression" and raw.nunique(dropna=True) <= 2 else task_type
            y_for_leakage = _target_values(raw.dropna(), effective_type)[0]
            aligned = frame.loc[raw.notna(), usable_features].reset_index(drop=True)
            leakage = _leakage_warnings(aligned, y_for_leakage.reset_index(drop=True))
            warnings.extend(leakage)
        except ModelingEngineError:
            leakage = []
    else:
        leakage = []
    checks.append({
        "key": "leakage", "label": "结果泄漏", "status": "warn" if leakage else "pass",
        "value": len(leakage), "message": leakage[0] if leakage else "未发现明显标签泄漏信号",
    })

    recommended = split_strategy
    if split_strategy == "auto":
        recommended = "group" if selected_group else ("time" if selected_time else "random")
    split_labels = {
        "random": "随机分层留出" if task_type == "classification" else "随机留出",
        "time": "按时间先后留出",
        "group": "按业务实体分组留出",
    }
    date_range = None
    if selected_time and selected_time in frame.columns:
        parsed = pd.to_datetime(frame[selected_time], errors="coerce").dropna()
        if not parsed.empty:
            date_range = {"field": selected_time, "start": parsed.min().isoformat(), "end": parsed.max().isoformat()}
    checks.append({
        "key": "split", "label": "验证方式", "status": "pass" if recommended in {"time", "group"} else "warn",
        "value": recommended, "message": split_labels.get(recommended, recommended),
    })

    score = max(0, 100 - len(blockers) * 35 - len(warnings) * 7)
    status = "blocked" if blockers else ("warning" if warnings else "ready")
    return {
        "status": status, "score": score, "sampleCount": sample_count,
        "usableSampleCount": sample_count - missing_target,
        "featureCount": len(usable_features), "targetField": target_field,
        "classDistribution": class_distribution, "missingFeatures": missing_features,
        "constantFeatures": constant_features, "blockers": blockers, "warnings": warnings,
        "checks": checks, "recommendedSplit": recommended,
        "recommendedSplitLabel": split_labels.get(recommended, recommended),
        "timeField": selected_time or None, "groupField": selected_group or None,
        "dateRange": date_range,
    }


def _split_indices(
    X: pd.DataFrame,
    y: pd.Series | None,
    *,
    strategy: str,
    time_values: pd.Series | None,
    group_values: pd.Series | None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    indices = np.arange(len(X))
    requested = strategy or "auto"
    selected = requested
    if requested == "auto":
        if group_values is not None and group_values.nunique(dropna=True) >= 2:
            selected = "group"
        elif time_values is not None and time_values.notna().sum() >= 2:
            selected = "time"
        else:
            selected = "random"

    if selected == "group":
        if group_values is None or group_values.nunique(dropna=True) < 2:
            raise ModelingEngineError("分组切分需要至少 2 个业务实体/工单")
        groups = group_values.fillna("__missing_group__").astype(str)
        splitter = GroupShuffleSplit(n_splits=1, test_size=TEST_SIZE, random_state=RANDOM_STATE)
        train_idx, test_idx = next(splitter.split(X, y, groups=groups))
        details = {
            "strategy": "group", "label": "按业务实体分组留出",
            "groupCount": int(groups.nunique()), "leakageControlled": True,
        }
    elif selected == "time":
        if time_values is None:
            raise ModelingEngineError("时间切分需要有效的时间字段")
        parsed = pd.to_datetime(time_values, errors="coerce")
        if parsed.notna().sum() < 2:
            raise ModelingEngineError("时间字段无法解析，不能执行时间切分")
        order = np.argsort(parsed.fillna(pd.Timestamp.min).to_numpy(), kind="stable")
        boundary = max(1, min(len(order) - 1, int(len(order) * (1 - TEST_SIZE))))
        train_idx, test_idx = order[:boundary], order[boundary:]
        details = {
            "strategy": "time", "label": "按时间先后留出",
            "trainEnd": parsed.iloc[train_idx].max().isoformat(),
            "testStart": parsed.iloc[test_idx].min().isoformat(),
            "leakageControlled": True,
        }
    elif selected == "random":
        stratify = _safe_stratify(y)
        train_idx, test_idx = train_test_split(
            indices, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=stratify,
        )
        details = {
            "strategy": "random",
            "label": "随机分层留出" if stratify is not None else "随机留出",
            "leakageControlled": False,
        }
    else:
        raise ModelingEngineError(f"不支持的切分策略：{selected}")
    return np.asarray(train_idx), np.asarray(test_idx), details


def prepare_dataset(
    df: pd.DataFrame,
    task_type: str,
    target_field: str = "",
    features: list[str] | None = None,
    *,
    split_strategy: str = "auto",
    time_field: str = "",
    group_field: str = "",
) -> dict:
    """Validate rows and split raw data before preprocessing is fitted."""
    if df is None or df.empty:
        raise ModelingEngineError("训练集为空，请检查取数 SQL")
    frame = df.copy().dropna(axis=1, how="all")
    if len(frame) < MIN_SAMPLE_COUNT:
        raise ModelingEngineError(f"样本过少（{len(frame)} 行），至少需要 {MIN_SAMPLE_COUNT} 行")

    y: pd.Series | None = None
    target_type: str | None = None
    target_mapping: dict[str, int] = {}
    effective_task_type = task_type
    if task_type in {"classification", "regression"}:
        if not target_field or target_field not in frame.columns:
            raise ModelingEngineError(f"目标列 {target_field or 'target_field'} 在数据集中不存在")
        valid_target = frame[target_field].notna()
        frame = frame.loc[valid_target].reset_index(drop=True)
        raw_target = frame.pop(target_field)
        if task_type == "regression" and raw_target.nunique(dropna=True) <= 2:
            effective_task_type = "classification"
        y, target_type, target_mapping = _target_values(raw_target, effective_task_type)

    all_columns = list(frame.columns)
    selected_group = group_field or _find_named_column(all_columns, _GROUP_NAMES)
    selected_time = time_field or _find_named_column(all_columns, _TIME_NAMES)
    if group_field and group_field not in frame.columns:
        raise ModelingEngineError(f"分组字段 {group_field} 在数据集中不存在")
    if time_field and time_field not in frame.columns:
        raise ModelingEngineError(f"时间字段 {time_field} 在数据集中不存在")
    groups = frame[selected_group].copy() if selected_group else None
    times = frame[selected_time].copy() if selected_time else None

    if features:
        feature_set = [column for column in features if column != target_field]
        missing = [column for column in feature_set if column not in frame.columns]
        if missing:
            raise ModelingEngineError(f"特征列在数据集中不存在：{', '.join(missing)}")
    else:
        feature_set = [column for column in frame.columns if column not in {selected_group, selected_time}]
    if not feature_set:
        raise ModelingEngineError("没有可用的特征列")
    X = frame[feature_set].copy()

    train_idx, test_idx, split = _split_indices(
        X, y, strategy=split_strategy, time_values=times, group_values=groups,
    )
    split["timeField"] = selected_time or None
    split["groupField"] = selected_group or None
    split["requestedStrategy"] = split_strategy
    return {
        "X_train": X.iloc[train_idx].reset_index(drop=True),
        "X_test": X.iloc[test_idx].reset_index(drop=True),
        "y_train": y.iloc[train_idx].reset_index(drop=True) if y is not None else None,
        "y_test": y.iloc[test_idx].reset_index(drop=True) if y is not None else None,
        "sample_count": int(len(X)), "train_count": int(len(train_idx)), "test_count": int(len(test_idx)),
        "task_type": effective_task_type, "target_type": target_type, "target_mapping": target_mapping,
        "features": feature_set, "feature_names": feature_set, "encoders": {}, "scaler": None,
        "impute_policy": {"numeric": "median", "categorical": "most_frequent", "unknownCategory": "ignore"},
        "split": split, "leakage_warnings": _leakage_warnings(X, y),
        "groups_train": groups.iloc[train_idx].reset_index(drop=True) if groups is not None else None,
        "times_train": times.iloc[train_idx].reset_index(drop=True) if times is not None else None,
    }


def _make_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    numeric = list(X.select_dtypes(include=[np.number, "bool"]).columns)
    categorical = [column for column in X.columns if column not in numeric]
    transformers = []
    if numeric:
        transformers.append((
            "numeric",
            Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]),
            numeric,
        ))
    if categorical:
        transformers.append((
            "categorical",
            Pipeline([
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
            ]),
            categorical,
        ))
    return ColumnTransformer(transformers=transformers, remainder="drop", verbose_feature_names_out=True)


def _estimator(task_type: str, algorithm: str, sample_count: int):
    if algorithm == "linear":
        return LinearRegression()
    if algorithm == "tree":
        if task_type == "regression":
            return DecisionTreeRegressor(random_state=RANDOM_STATE, min_samples_leaf=3)
        return DecisionTreeClassifier(random_state=RANDOM_STATE, min_samples_leaf=3, class_weight="balanced")
    if algorithm == "forest":
        if task_type == "regression":
            return RandomForestRegressor(n_estimators=200, random_state=RANDOM_STATE, n_jobs=1, min_samples_leaf=2)
        return RandomForestClassifier(
            n_estimators=200, random_state=RANDOM_STATE, n_jobs=1,
            min_samples_leaf=2, class_weight="balanced_subsample",
        )
    if algorithm == "logistic":
        return LogisticRegression(max_iter=2000, random_state=RANDOM_STATE, class_weight="balanced")
    if algorithm == "kmeans":
        return KMeans(n_clusters=max(2, min(4, sample_count // 100)), n_init=10, random_state=RANDOM_STATE)
    if algorithm == "isolation":
        return IsolationForest(n_estimators=200, contamination="auto", random_state=RANDOM_STATE, n_jobs=1)
    raise ModelingEngineError(f"未知算法：{algorithm}")


def _model_pipeline(task_type: str, algorithm: str, X_train: pd.DataFrame, sample_count: int) -> Pipeline:
    return Pipeline([
        ("preprocessor", _make_preprocessor(X_train)),
        ("estimator", _estimator(task_type, algorithm, sample_count)),
    ])


def train_model(task_type: str, algorithm: str, X_train, y_train, sample_count: int):
    family = ALGORITHM_BY_ID.get(algorithm)
    if family is None:
        raise ModelingEngineError(f"未知算法：{algorithm}")
    if task_type not in SUPPORTED_FAMILIES[algorithm]:
        raise ModelingEngineError(f"算法「{family['name']}」不支持{task_type}任务，请选择其他候选算法")
    if not isinstance(X_train, pd.DataFrame):
        X_train = pd.DataFrame(X_train)
    pipeline = _model_pipeline(task_type, algorithm, X_train, sample_count)
    pipeline.fit(X_train) if task_type in {"clustering", "anomaly"} else pipeline.fit(X_train, y_train)
    return pipeline


def _search_grid(task_type: str, algorithm: str, optimization_level: str) -> dict[str, list[Any]]:
    if optimization_level == "fast" or task_type in {"clustering", "anomaly"}:
        return {}
    balanced = {
        "logistic": {"estimator__C": [0.3, 1.0, 3.0]},
        "tree": {"estimator__max_depth": [4, 8, None], "estimator__min_samples_leaf": [3, 8]},
        "forest": {"estimator__max_depth": [6, None], "estimator__min_samples_leaf": [2, 5]},
    }
    thorough = {
        "logistic": {"estimator__C": [0.1, 0.3, 1.0, 3.0, 10.0]},
        "tree": {"estimator__max_depth": [3, 5, 8, 12, None], "estimator__min_samples_leaf": [2, 5, 10]},
        "forest": {
            "estimator__max_depth": [5, 8, 12, None],
            "estimator__min_samples_leaf": [1, 3, 6],
            "estimator__max_features": ["sqrt", 0.8],
        },
    }
    return (thorough if optimization_level == "thorough" else balanced).get(algorithm, {})


def _cv_policy(task_type: str, prepared: dict) -> tuple[Any, pd.Series | None, str]:
    strategy = (prepared.get("split") or {}).get("strategy", "random")
    y = prepared.get("y_train")
    if strategy == "group" and prepared.get("groups_train") is not None:
        groups = prepared["groups_train"].astype(str)
        splits = min(3, int(groups.nunique()))
        if splits >= 2:
            return GroupKFold(n_splits=splits), groups, f"{splits} 折业务实体分组验证"
    if strategy == "time" and len(prepared["X_train"]) >= 12:
        return TimeSeriesSplit(n_splits=3), None, "3 折时间前推验证"
    if task_type == "classification" and y is not None and int(y.value_counts().min()) >= 3:
        return StratifiedKFold(n_splits=3, shuffle=True, random_state=RANDOM_STATE), None, "3 折分层交叉验证"
    return KFold(n_splits=3, shuffle=True, random_state=RANDOM_STATE), None, "3 折交叉验证"


def _threshold_from_oof(model: Pipeline, X_train, y_train, cv, groups) -> tuple[float, dict[str, Any]]:
    try:
        probability = cross_val_predict(
            clone(model), X_train, y_train, cv=cv, groups=groups, method="predict_proba", n_jobs=1,
        )[:, 1]
        candidates = np.linspace(0.2, 0.8, 61)
        scored = [(float(threshold), float(f1_score(y_train, probability >= threshold, zero_division=0))) for threshold in candidates]
        threshold, score = max(scored, key=lambda item: item[1])
        return round(threshold, 3), {
            "source": "training_oof", "objective": "f1", "objectiveValue": round4(score),
            "message": "阈值由训练集交叉验证确定，测试集仅用于最终验收",
        }
    except (ValueError, TypeError):
        return 0.5, {
            "source": "default", "objective": "f1", "objectiveValue": None,
            "message": "当前数据无法稳定生成折外概率，使用默认阈值 0.5",
        }


def tune_model(task_type: str, algorithm: str, prepared: dict, optimization_level: str = "balanced") -> tuple[Pipeline, dict]:
    """Budgeted AutoML search that never uses the final holdout for selection."""
    X_train, y_train = prepared["X_train"], prepared.get("y_train")
    pipeline = _model_pipeline(task_type, algorithm, X_train, prepared["sample_count"])
    grid = _search_grid(task_type, algorithm, optimization_level)
    if not grid:
        pipeline.fit(X_train) if task_type in {"clustering", "anomaly"} else pipeline.fit(X_train, y_train)
        return pipeline, {
            "optimizationLevel": optimization_level, "searched": False, "candidateCount": 1,
            "bestParams": {}, "cvMetric": None, "cvScore": None,
            "decisionThreshold": 0.5 if task_type == "classification" else None,
            "thresholdPolicy": {"source": "default", "message": "快速模式使用算法默认参数"},
        }

    cv, groups, cv_label = _cv_policy(task_type, prepared)
    if task_type == "classification":
        minority_ratio = float(y_train.value_counts().min() / len(y_train))
        scoring = "average_precision" if minority_ratio < 0.2 else "roc_auc"
    else:
        scoring = "neg_mean_absolute_error"
    search = GridSearchCV(
        pipeline, grid, scoring=scoring, cv=cv, refit=True, n_jobs=1,
        error_score=np.nan, return_train_score=False,
    )
    candidate_count = int(np.prod([len(values) for values in grid.values()])) if grid else 1
    try:
        search.fit(X_train, y_train, groups=groups)
        score = float(search.best_score_) if np.isfinite(search.best_score_) else None
        if score is None:
            raise ValueError("all AutoML candidates returned invalid cross-validation scores")
        best = search.best_estimator_
    except (ValueError, TypeError) as exc:
        # Small or strongly time-ordered industrial datasets can make a CV fold
        # contain only one class.  AutoML search must degrade safely instead of
        # turning an otherwise trainable task into a failed run.
        best = _model_pipeline(task_type, algorithm, X_train, prepared["sample_count"])
        best.fit(X_train, y_train)
        threshold, threshold_policy = (0.5, {})
        if task_type == "classification" and hasattr(best, "predict_proba"):
            threshold, threshold_policy = _threshold_from_oof(best, X_train, y_train, cv, groups)
        return best, {
            "optimizationLevel": optimization_level,
            "searched": False,
            "candidateCount": candidate_count,
            "bestParams": {},
            "cvPolicy": cv_label,
            "cvMetric": scoring,
            "cvScore": None,
            "fallbackReason": str(exc),
            "decisionThreshold": threshold if task_type == "classification" else None,
            "thresholdPolicy": threshold_policy,
        }
    threshold, threshold_policy = (0.5, {})
    if task_type == "classification" and hasattr(best, "predict_proba"):
        threshold, threshold_policy = _threshold_from_oof(best, X_train, y_train, cv, groups)
    return best, {
        "optimizationLevel": optimization_level, "searched": True,
        "candidateCount": candidate_count, "bestParams": search.best_params_,
        "cvPolicy": cv_label, "cvMetric": scoring, "cvScore": round4(score),
        "decisionThreshold": threshold if task_type == "classification" else None,
        "thresholdPolicy": threshold_policy,
    }


def evaluate_model(
    task_type: str,
    algorithm: str,
    model,
    X_test,
    y_test,
    sample_count: int,
    *,
    decision_threshold: float | None = None,
) -> dict:
    metrics: dict[str, Any] = {}
    if task_type == "classification":
        if decision_threshold is not None and hasattr(model, "predict_proba"):
            y_pred = (model.predict_proba(X_test)[:, 1] >= decision_threshold).astype(int)
        else:
            y_pred = model.predict(X_test)
        metrics["accuracy"] = round(float(accuracy_score(y_test, y_pred)) * 100, 1)
        metrics["balancedAccuracy"] = round(float(balanced_accuracy_score(y_test, y_pred)) * 100, 1)
        metrics["precision"] = round(float(precision_score(y_test, y_pred, zero_division=0)) * 100, 1)
        metrics["recall"] = round(float(recall_score(y_test, y_pred, zero_division=0)) * 100, 1)
        metrics["f1"] = round4(f1_score(y_test, y_pred, zero_division=0))
        if hasattr(model, "predict_proba"):
            try:
                proba = model.predict_proba(X_test)[:, 1]
                metrics["auc"] = round4(roc_auc_score(y_test, proba))
                metrics["averagePrecision"] = round4(average_precision_score(y_test, proba))
            except ValueError:
                metrics["auc"] = None
                metrics["averagePrecision"] = None
        metrics["confusionMatrix"] = confusion_matrix(y_test, y_pred).tolist()
    elif task_type == "regression":
        y_pred = model.predict(X_test)
        metrics["r2"] = round4(r2_score(y_test, y_pred))
        metrics["mae"] = round4(mean_absolute_error(y_test, y_pred))
        metrics["rmse"] = round4(float(np.sqrt(mean_squared_error(y_test, y_pred))))
    elif task_type == "clustering":
        labels = model.predict(X_test)
        transformed = model.named_steps["preprocessor"].transform(X_test)
        if len(set(labels)) > 1 and len(X_test) > len(set(labels)):
            if len(X_test) > 5000:
                selected = np.random.default_rng(RANDOM_STATE).choice(len(X_test), 5000, replace=False)
                transformed, labels = transformed[selected], labels[selected]
            metrics["silhouette"] = round4(silhouette_score(transformed, labels))
        metrics["clusterCount"] = int(model.named_steps["estimator"].n_clusters)
    elif task_type == "anomaly":
        labels = model.predict(X_test)
        anomaly_rate = round(float(np.mean(labels == -1)) * 100, 1)
        metrics["anomalyRate"] = anomaly_rate
        metrics["contamination"] = anomaly_rate / 100
    return metrics


def transformed_feature_names(model, fallback: list[str]) -> list[str]:
    try:
        return [str(value) for value in model.named_steps["preprocessor"].get_feature_names_out()]
    except (AttributeError, KeyError, ValueError):
        return list(fallback)


def extract_importance(algorithm: str, model, feature_names: list[str]) -> list[dict]:
    estimator = model.named_steps.get("estimator") if hasattr(model, "named_steps") else model
    transformed = transformed_feature_names(model, feature_names)
    if algorithm in {"tree", "forest"} and hasattr(estimator, "feature_importances_"):
        raw = np.asarray(estimator.feature_importances_, dtype=float)
    elif algorithm in {"linear", "logistic"} and hasattr(estimator, "coef_"):
        coefficients = np.asarray(estimator.coef_, dtype=float)
        raw = np.abs(coefficients).mean(axis=0) if coefficients.ndim > 1 else np.abs(coefficients)
    else:
        return []
    if raw.sum() <= 0:
        return []
    grouped: defaultdict[str, float] = defaultdict(float)
    ordered_inputs = sorted(feature_names, key=len, reverse=True)
    for name, value in zip(transformed, raw, strict=False):
        suffix = name.split("__", 1)[-1]
        original = next((column for column in ordered_inputs if suffix == column or suffix.startswith(f"{column}_")), suffix)
        grouped[original] += float(abs(value))
    total = sum(grouped.values())
    items = [{"name": name, "importance": round(value * 100 / total, 1)} for name, value in grouped.items()]
    items.sort(key=lambda item: item["importance"], reverse=True)
    return items[:MAX_IMPORTANCE_FEATURES]


def build_business_explanation(
    task_type: str,
    algorithm: str,
    model,
    feature_names: list[str],
    metrics: dict,
    importance: list[dict],
    *,
    decision_threshold: float | None = None,
) -> dict:
    """Translate technical output into governed, non-causal business language."""
    top_names = [item["name"] for item in importance[:3]]
    if top_names:
        summary = f"模型判断主要参考{'、'.join(top_names)}。这些是预测贡献信号，不代表直接因果关系。"
    else:
        summary = "该算法没有可直接汇总的全局特征贡献，请结合分群结果或异常样本复核。"
    boundary = "仅适用于当前数据源、当前字段口径和训练数据覆盖范围；数据结构或业务流程变化后应重新评估。"
    threshold_note = None
    if task_type == "classification":
        matrix = metrics.get("confusionMatrix") or []
        false_positive = matrix[0][1] if len(matrix) == 2 and len(matrix[0]) == 2 else None
        false_negative = matrix[1][0] if len(matrix) == 2 and len(matrix[1]) == 2 else None
        threshold_note = {
            "threshold": decision_threshold if decision_threshold is not None else 0.5,
            "falsePositive": false_positive,
            "falseNegative": false_negative,
            "message": (
                f"当前阈值下测试集误报 {false_positive} 条、漏报 {false_negative} 条；"
                "上线前应由业务负责人确认更重视漏报还是误报。"
                if false_positive is not None else "上线前应由业务负责人确认误报与漏报成本。"
            ),
        }
    return {
        "summary": summary,
        "topFactors": importance[:5],
        "threshold": threshold_note,
        "applicabilityBoundary": boundary,
        "causalDisclaimer": "特征重要性反映模型预测贡献，不等同于业务因果结论。",
        "algorithm": algorithm,
    }


def encode_input_record(record: dict, encoders: dict, feature_names: list[str]) -> list:
    """Legacy helper retained for old v1 artifacts and compatibility tests."""
    row: list[Any] = []
    for column in feature_names:
        value = record.get(column)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            row.append(0)
            continue
        encoder = encoders.get(column)
        if encoder is not None:
            row.append(encoder.get(str(value), 0))
        else:
            try:
                row.append(float(value))
            except (TypeError, ValueError):
                row.append(0)
    return row


def summarize_metric(task_type: str, algorithm: str, metrics: dict) -> str:
    if task_type == "classification":
        return f"AUC {metrics.get('auc') if metrics.get('auc') is not None else '—'}"
    if task_type == "regression":
        return f"R² {metrics.get('r2') if metrics.get('r2') is not None else '—'}"
    if task_type == "clustering":
        return f"轮廓系数 {metrics.get('silhouette') if metrics.get('silhouette') is not None else '—'}"
    if task_type == "anomaly":
        return f"异常占比 {metrics.get('anomalyRate') if metrics.get('anomalyRate') is not None else '—'}%"
    return ""
