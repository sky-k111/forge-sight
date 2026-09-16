"""建模服务：任务创建 → 取数 → 训练 → 落盘 → 登记 → 治理，全链路审计。

训练为同步执行（样本 ≤ 1 万行，秒级）。模块级引用 PlatformSession / write_audit，
便于测试用 conftest monkeypatch 替换。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import or_

from app.agent.ids import new_id
from app.audit.service import write_audit
from app.config import settings
from app.db.models import DataSource, GovernedModel, ModelPredictionTrace, ModelingEvaluation, ModelingInferenceLog, ModelingTask
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly
from app.modeling import engine
from app.modeling.engine import (
    ALGORITHM_BY_ID,
    TASK_TYPE_LABELS,
    build_business_explanation,
    diagnose_dataset,
    encode_input_record,
    evaluate_model,
    extract_importance,
    prepare_dataset,
    summarize_metric,
    train_model,
    tune_model,
)

STATUS_LABELS = {
    "draft": ("草稿", "gray"),
    "training": ("训练中", "blue"),
    "trained": ("已训练", "green"),
    "failed": ("训练失败", "red"),
}
SOURCE_LABELS = {"agent": "Agent 创建", "manual": "人工创建"}
MODEL_STATUS_LABELS = {
    "pending": ("待复核", "gray"),
    "personal": ("个人自用", "amber"),
    "reviewing": ("复核中", "blue"),
    "published": ("已发布", "green"),
    "rejected": ("已驳回", "red"),
    "retired": ("已下线", "gray"),
}
# 各治理动作的合法来源状态（比全局迁移表更贴合页面按钮语义）：
# 复核 = 一个动作完成判定（pending/personal/reviewing 待定，rejected 可重新复核通过）；
# 发布 = 仅复核中可发布（强制经过复核流程）；管理员免审直发走 publish_model_direct；
# 下线 = 已发布/已驳回可下线。
REVIEW_ALLOWED_FROM = {"pending", "personal", "reviewing", "rejected"}
PUBLISH_ALLOWED_FROM = {"reviewing"}
DIRECT_PUBLISH_ALLOWED_FROM = {"pending", "personal", "reviewing"}
RETIRE_ALLOWED_FROM = {"published", "rejected", "personal"}

TASK_STATUSES = set(STATUS_LABELS)
MODEL_STATUSES = set(MODEL_STATUS_LABELS)


def _personal_active(row: GovernedModel, now: datetime | None = None) -> bool:
    """个人档是否仍在有效期：状态 personal 且未过期。"""
    if row.status != "personal":
        return False
    now = now or datetime.now()
    return row.personal_expires_at is None or row.personal_expires_at > now


def _gate_publishable(model: GovernedModel) -> bool:
    """机器质量门禁：效果不达标的模型任何角色都不可发布/自用。"""
    quality = (model.explanation_json or {}).get("qualityAssessment") or {}
    return quality.get("publishable") is not False


def _item_gate_ok(item: dict) -> bool:
    quality = item.get("qualityAssessment") or {}
    return quality.get("publishable") is not False


def _enforce_personal_quota(session, owner_id: str) -> None:
    """个人档配额：活跃个人模型超过上限时，最旧的自动下线。"""
    quota = max(1, settings.modeling_personal_quota)
    now = datetime.now()
    rows = (
        session.query(GovernedModel)
        .filter_by(owner_id=owner_id, status="personal")
        .filter(
            (GovernedModel.personal_expires_at.is_(None))
            | (GovernedModel.personal_expires_at > now)
        )
        .order_by(GovernedModel.created_at.desc())
        .all()
    )
    for row in rows[quota:]:
        row.status = "retired"
        row.retired_at = now


class ModelingError(ValueError):
    """建模业务错误，API 层转为 400/409。"""


class ModelNotFoundError(ModelingError):
    """任务/模型不存在，API 层转为 404。"""


class TaskNotTrainedError(ModelingError):
    """任务尚未训练或模型产物缺失，API 层转为 409。"""


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _task_dict(row: ModelingTask) -> dict:
    status_label, tone = STATUS_LABELS.get(row.status, (row.status, "gray"))
    selected = row.selected_model
    return {
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "sourceLabel": SOURCE_LABELS.get(row.source, row.source),
        "owner": row.owner_id,
        "taskType": row.task_type,
        "taskTypeLabel": TASK_TYPE_LABELS.get(row.task_type, row.task_type),
        "status": row.status,
        "statusLabel": status_label,
        "tone": tone,
        "updatedAt": _iso(row.updated_at),
        "goal": row.goal,
        "dataset": row.dataset_name or "未命名数据集",
        "datasetLabel": row.dataset_name,
        "sampleCount": row.sample_count,
        "sampleSummary": f"{row.sample_count} 条样本" if row.sample_count else "",
        "targetLabel": row.target_label,
        "targetField": row.target_field,
        "features": list(row.features_json or []),
        "candidates": list(row.candidates_json or []),
        "selectedModel": selected,
        "selectedModelName": ALGORITHM_BY_ID[selected]["name"] if selected in ALGORITHM_BY_ID else None,
        "metric": row.metric or None,
        "splitStrategy": row.split_strategy,
        "timeField": row.time_field,
        "groupField": row.group_field,
        "diagnostics": row.diagnostics_json or None,
        "diagnosedAt": _iso(row.diagnosed_at),
        "runId": row.run_id,
    }


def _model_dict(row: GovernedModel) -> dict:
    status_label, tone = MODEL_STATUS_LABELS.get(row.status, (row.status, "gray"))
    algorithm = ALGORITHM_BY_ID.get(row.algorithm, {})
    with PlatformSession() as session:
        task = session.get(ModelingTask, row.task_id)
        dataset = task.dataset_name if task is not None else ""
    return {
        "id": row.id,
        "taskId": row.task_id,
        "name": row.name,
        "version": row.version,
        "trainingRevision": row.training_revision,
        "artifactFormatVersion": row.artifact_format_version,
        "decisionThreshold": float(row.decision_threshold) if row.decision_threshold is not None else None,
        "thresholdPolicy": row.threshold_policy_json or {},
        "tuning": row.tuning_json or {},
        "explanation": row.explanation_json or {},
        "qualityAssessment": (row.explanation_json or {}).get("qualityAssessment") or None,
        "algorithm": row.algorithm,
        "algorithmName": algorithm.get("name", row.algorithm),
        "owner": row.owner_id,
        "dataset": dataset,
        "metric": _metric_summary(row.metrics_json or {}),
        "metrics": row.metrics_json or {},
        "status": row.status,
        "statusLabel": status_label,
        "tone": tone,
        "calls": row.call_count,
        "directPublish": bool(row.direct_publish),
        "personalExpiresAt": _iso(row.personal_expires_at),
        "personalActive": _personal_active(row),
        "publishedAt": _iso(row.published_at),
        "retiredAt": _iso(row.retired_at),
        "reviewedBy": row.reviewed_by,
        "updatedAt": _iso(row.updated_at),
    }


def _metric_summary(metrics: dict) -> str:
    for key in ("auc", "r2", "silhouette"):
        if metrics.get(key) is not None:
            return f"{key.upper()} {metrics[key]}"
    if metrics.get("anomalyRate") is not None:
        return f"异常占比 {metrics['anomalyRate']}%"
    return ""


def list_algorithms() -> list[dict]:
    return list(engine.ALGORITHMS)


def _find_source(source_id: int) -> DataSource:
    with PlatformSession() as session:
        source = session.get(DataSource, int(source_id))
    if source is None:
        raise ModelingError(f"建模数据源 {source_id} 不存在")
    return source


def _looks_like_identifier(field_name: str) -> bool:
    normalized = str(field_name or "").strip().lower()
    return normalized == "id" or normalized.endswith("_id") or normalized.startswith("id_")


def _validate_target_preview(payload, result) -> None:
    """在创建任务时就验证目标列，避免用户到训练阶段才发现选中了 ID/文本列。"""
    if payload.taskType not in {"classification", "regression"}:
        return
    target = str(payload.targetField or "").strip()
    if not target:
        raise ModelingError("分类或回归任务必须指定预测目标")
    if _looks_like_identifier(target):
        raise ModelingError(f"预测目标「{target}」是记录标识，不能用作建模目标")
    if target not in result.columns:
        raise ModelingError(f"预测目标「{target}」不在取数结果中")
    if payload.taskType != "regression":
        return
    target_index = result.columns.index(target)
    values = [row[target_index] for row in result.rows if row[target_index] is not None]
    if not values:
        raise ModelingError(f"回归目标「{target}」没有可用数值")
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.isna().any():
        raise ModelingError(f"回归目标「{target}」包含非数值内容，请选择数量、时长或金额字段")


def _find_task(task_id: str) -> ModelingTask:
    with PlatformSession() as session:
        task = session.get(ModelingTask, task_id)
    if task is None:
        raise ModelNotFoundError("建模任务不存在")
    return task


def create_task(payload) -> tuple[dict, bool]:
    """创建建模任务；source=agent 时对提案 SQL 做白名单预检。同 runId 幂等。"""
    if payload.runId:
        with PlatformSession() as session:
            existing = session.query(ModelingTask).filter_by(run_id=payload.runId).first()
            if existing is not None:
                return _task_dict(existing), True

    # 预检取数 SQL：校验 + 审计，失败即拒绝（红线：LLM 生成的 SQL 必须过校验器）
    source = _find_source(payload.sourceId)
    check = execute_readonly(
        payload.datasetSql, action="建模取数预检", user_name=payload.ownerId, source=source
    )
    if not check.ok:
        raise ModelingError(f"取数 SQL 未通过安全校验：{check.error}")
    _validate_target_preview(payload, check)

    task = ModelingTask(
        id=new_id("mt"),
        title=payload.title,
        source=payload.source,
        source_id=payload.sourceId,
        owner_id=payload.ownerId,
        goal=payload.goal,
        task_type=payload.taskType,
        dataset_name=payload.datasetName,
        dataset_sql=payload.datasetSql,
        target_field=payload.targetField,
        target_label=payload.targetLabel,
        features_json=list(payload.features),
        candidates_json=list(payload.candidates),
        split_strategy=payload.splitStrategy,
        time_field=payload.timeField,
        group_field=payload.groupField,
        run_id=payload.runId,
        status="draft",
    )
    with PlatformSession() as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    write_audit(action="创建建模任务", resource=task.id, result="允许",
                detail=f"{task.title}（{TASK_TYPE_LABELS.get(task.task_type, task.task_type)}）",
                user_name=payload.ownerId, role="业务人员")
    return _task_dict(task), False


def list_tasks(owner_id: str = "", status: str = "") -> list[dict]:
    with PlatformSession() as session:
        query = session.query(ModelingTask).order_by(ModelingTask.updated_at.desc())
        if owner_id:
            query = query.filter_by(owner_id=owner_id)
        if status:
            query = query.filter_by(status=status)
        rows = query.all()
    return [_task_dict(row) for row in rows]


def get_task(task_id: str) -> dict:
    return _task_dict(_find_task(task_id))


def _training_frame(task: ModelingTask, user_id: str, action: str) -> pd.DataFrame:
    source = _find_source(task.source_id)
    result = execute_readonly(task.dataset_sql, action=action, user_name=user_id, source=source)
    if not result.ok:
        raise ModelingError(f"取数失败：{result.error}")
    if result.truncated:
        raise ModelingError(f"训练数据超过 {settings.sql_max_rows} 行上限，请缩小取数范围")
    return pd.DataFrame(result.rows, columns=result.columns)


def _diagnose_frame(task: ModelingTask, frame: pd.DataFrame) -> dict:
    return diagnose_dataset(
        frame,
        task.task_type,
        target_field=task.target_field,
        features=list(task.features_json or []),
        split_strategy=task.split_strategy,
        time_field=task.time_field,
        group_field=task.group_field,
    )


def diagnose_task(task_id: str, user_id: str = "local-test-user", *, refresh: bool = True) -> dict:
    task = _find_task(task_id)
    if not refresh and task.diagnostics_json:
        return task.diagnostics_json
    frame = _training_frame(task, user_id, "建模数据体检")
    diagnostics = _diagnose_frame(task, frame)
    with PlatformSession() as session:
        row = session.get(ModelingTask, task_id)
        row.diagnostics_json = diagnostics
        row.diagnosed_at = datetime.now()
        row.sample_count = diagnostics.get("sampleCount") or row.sample_count
        session.commit()
    write_audit(
        action="建模数据体检", resource=task_id,
        result="拒绝" if diagnostics["status"] == "blocked" else "允许",
        detail=f"数据准备度 {diagnostics['score']} 分，{len(diagnostics['blockers'])} 个阻断项，{len(diagnostics['warnings'])} 个提醒",
        user_name=user_id, role="业务人员",
    )
    return diagnostics


def _storage_dir() -> str:
    directory = settings.modeling_storage_dir
    if not os.path.isabs(directory):
        directory = os.path.join(os.path.dirname(__file__), "..", "..", directory)
    os.makedirs(directory, exist_ok=True)
    return os.path.abspath(directory)


def _save_model_artifact(model, model_id: str, schema: dict) -> tuple[str, str]:
    """Persist a self-contained preprocessing + estimator bundle."""
    path = os.path.join(_storage_dir(), f"{model_id}.joblib")
    joblib.dump({"formatVersion": 2, "pipeline": model, "schema": schema}, path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return path, digest.hexdigest()


def _load_model_artifact(model_id: str) -> dict:
    path = os.path.join(_storage_dir(), f"{model_id}.joblib")
    if not os.path.exists(path):
        raise TaskNotTrainedError("模型文件缺失，请重新训练")
    payload = joblib.load(path)
    if payload.get("formatVersion") == 2 and payload.get("pipeline") is not None:
        return payload
    # Backward-compatible reader for artifacts produced before pipeline bundling.
    return {
        "formatVersion": 1,
        "model": payload["model"],
        "scaler": payload.get("scaler"),
        "schema": {},
    }


def _render_training_script(task: ModelingTask, algorithm: str, tuning: dict, prepared: dict, task_type: str) -> str:
    """由任务契约与最优配置确定性生成可复现建模脚本（随模型版本冻结）。"""
    params = tuning.get("bestParams") or {}
    split = (prepared.get("split") or {})
    name = ALGORITHM_BY_ID.get(algorithm, {}).get("name", algorithm)
    return "\n".join([
        f"# ForgeSight 可复现建模脚本 · {name}",
        "# 取数 SQL 已通过只读 SQL 校验与执行网关，训练与推理共用同一列序。",
        f"SQL = {task.dataset_sql!r}",
        f"features = {list(prepared.get('feature_names') or [])!r}",
        f"target = {task.target_field!r}",
        f"split = {split.get('strategy', task.split_strategy)!r}  # {split.get('label', '')}",
        f"hyperparams = {params!r}",
        "",
        "from app.modeling.engine import _model_pipeline",
        "",
        f"model = _model_pipeline(task_type={task_type!r}, algorithm={algorithm!r}, X_train=X_train, sample_count=len(X_train))",
        "model.set_params(**{f'estimator__{key}': value for key, value in hyperparams.items()})",
        f"model.fit(X_train{', y_train' if task_type in ('regression', 'classification') else ''})",
        "predictions = model.predict(X_new)",
    ])


def _recommended_model(task_type: str, evaluated: list[dict]) -> str:
    """按任务类型挑选指标最优的算法。"""
    key = {"classification": "auc", "regression": "r2",
           "clustering": "silhouette", "anomaly": None}[task_type]
    if key is None:  # 异常检测无统一最优指标，取第一个完成训练的
        return evaluated[0]["algorithm"]
    fallback = {"classification": "f1"}.get(task_type)
    best = None
    best_score = -1.0
    for item in evaluated:
        score = item["metrics"].get(key)
        if score is None and fallback:
            score = item["metrics"].get(fallback)
        if score is not None and float(score) > best_score:
            best_score = float(score)
            best = item["algorithm"]
    return best or evaluated[0]["algorithm"]


def _assess_model_quality(task_type: str, metrics: dict) -> dict:
    """把技术指标翻译为发布门槛，避免“训练完成”被误认为“可投入使用”。"""
    if task_type == "classification":
        auc = metrics.get("auc")
        f1 = metrics.get("f1")
        if auc is None and f1 is None:
            return {
                "status": "blocked", "label": "缺少有效评估", "publishable": False,
                "reason": "分类模型没有可用于判断泛化能力的 AUC 或 F1。",
                "nextAction": "检查目标标签、类别分布和测试集划分后重新训练。",
            }
        if (auc is not None and float(auc) < 0.55) or (f1 is not None and float(f1) < 0.25):
            return {
                "status": "blocked", "label": "效果不足", "publishable": False,
                "reason": "模型在隔离测试集上的区分能力接近随机或关键类别识别能力过低。",
                "nextAction": "补充有效特征、扩大样本覆盖并检查标签质量后重新评估。",
            }
        if (auc is not None and float(auc) >= 0.65) and (f1 is None or float(f1) >= 0.40):
            return {
                "status": "ready", "label": "达到复核门槛", "publishable": True,
                "reason": "隔离测试集指标达到系统建议的基础复核门槛。",
                "nextAction": "由管理员结合误报、漏报成本复核阈值后发布。",
            }
        return {
            "status": "warning", "label": "需要业务复核", "publishable": True,
            "reason": "模型具备一定区分能力，但指标尚不足以自动判定适合生产使用。",
            "nextAction": "结合误报、漏报成本和真实业务样本进行人工验收。",
        }

    if task_type == "regression":
        r2 = metrics.get("r2")
        if r2 is None or float(r2) <= 0:
            return {
                "status": "blocked", "label": "效果不足", "publishable": False,
                "reason": "R² 不高于 0，模型在隔离测试集上没有优于直接使用平均值的基线。",
                "nextAction": "补充与目标有关的工况、时间和设备状态特征，或重新定义预测目标。",
            }
        if float(r2) >= 0.30:
            return {
                "status": "ready", "label": "达到复核门槛", "publishable": True,
                "reason": "模型已明显优于平均值基线，可进入业务复核。",
                "nextAction": "由管理员核对误差范围与适用场景后发布。",
            }
        return {
            "status": "warning", "label": "需要业务复核", "publishable": True,
            "reason": "模型优于平均值基线，但解释度较低，暂不建议直接用于关键决策。",
            "nextAction": "先用于辅助判断，并补充特征和样本继续评估。",
        }

    if task_type == "clustering":
        score = metrics.get("silhouette")
        if score is None or float(score) <= 0.15:
            return {
                "status": "blocked", "label": "分群不清晰", "publishable": False,
                "reason": "轮廓系数过低，当前分群结构缺少清晰边界。",
                "nextAction": "重新选择特征、缩放方式或聚类数量后再训练。",
            }
        return {
            "status": "ready" if float(score) >= 0.35 else "warning",
            "label": "达到复核门槛" if float(score) >= 0.35 else "需要业务复核",
            "publishable": True,
            "reason": "分群结构已形成，但仍需确认各群是否具有可解释的业务含义。",
            "nextAction": "由业务人员检查各群画像和样本代表性。",
        }

    return {
        "status": "warning", "label": "需要业务复核", "publishable": True,
        "reason": "异常检测缺少天然真值，异常占比不能单独证明模型有效。",
        "nextAction": "抽样复核异常记录并确认误报成本后再发布。",
    }


def _next_training_revision(session, task_id: str) -> int:
    latest = (
        session.query(ModelingEvaluation.training_revision)
        .filter_by(task_id=task_id)
        .order_by(ModelingEvaluation.training_revision.desc())
        .first()
    )
    return int(latest[0]) + 1 if latest else 1


def _next_model_version(session, task_id: str, algorithm: str) -> int:
    latest = (
        session.query(GovernedModel.version)
        .filter_by(task_id=task_id, algorithm=algorithm)
        .order_by(GovernedModel.version.desc())
        .first()
    )
    return int(latest[0]) + 1 if latest else 1


def train_task(task_id: str, strategy: str, candidate_ids: list[str], manual_model_id: str | None,
               user_id: str, optimization_level: str = "fast") -> dict:
    """同步训练：取数 → 预处理 → 候选算法逐一训练/评估 → 落盘 → 登记 → 审计。"""
    task = _find_task(task_id)
    if task.status == "training":
        raise ModelingError("任务正在训练中，请稍候")

    # 候选算法确定：auto 用任务候选（可被请求覆盖），manual 只训练指定算法
    if strategy == "manual":
        if manual_model_id not in ALGORITHM_BY_ID:
            raise ModelingError("未指定有效的训练算法")
        candidates = [manual_model_id]
    else:
        candidates = list(candidate_ids) or list(task.candidates_json or [])
    if not candidates:
        raise ModelingError("请至少选择一个候选算法")

    with PlatformSession() as session:
        claimed = (
            session.query(ModelingTask)
            .filter(ModelingTask.id == task_id, ModelingTask.status != "training")
            .update({ModelingTask.status: "training"}, synchronize_session=False)
        )
        if claimed != 1:
            session.rollback()
            raise ModelingError("任务正在训练中，请稍候")
        session.commit()

    started = time.perf_counter()
    try:
        df = _training_frame(task, user_id, "建模取数")
        diagnostics = _diagnose_frame(task, df)
        if diagnostics["status"] == "blocked":
            raise ModelingError("训练数据未通过体检：" + "；".join(diagnostics["blockers"]))
        prepared = prepare_dataset(
            df,
            task.task_type,
            target_field=task.target_field,
            features=list(task.features_json or []),
            split_strategy=task.split_strategy,
            time_field=task.time_field,
            group_field=task.group_field,
        )
        effective_task_type = prepared["task_type"]

        evaluated: list[dict] = []
        fitted_models: dict[str, object] = {}
        for algorithm in candidates:
            if effective_task_type not in engine.SUPPORTED_FAMILIES[algorithm]:
                continue  # 不适用任务类型的候选算法自动跳过
            model, tuning = tune_model(
                effective_task_type, algorithm, prepared, optimization_level=optimization_level,
            )
            fitted_models[algorithm] = model
            threshold = tuning.get("decisionThreshold")
            metrics = evaluate_model(effective_task_type, algorithm, model,
                                     prepared["X_test"], prepared["y_test"],
                                     prepared["sample_count"], decision_threshold=threshold)
            importance = extract_importance(algorithm, model, prepared["feature_names"])
            explanation = build_business_explanation(
                effective_task_type, algorithm, model, prepared["feature_names"], metrics,
                importance, decision_threshold=threshold,
            )
            quality = _assess_model_quality(effective_task_type, metrics)
            explanation["qualityAssessment"] = quality
            if effective_task_type == "clustering":
                explanation["clusterProfiles"] = engine.cluster_profiles(
                    model, prepared["X_train"], prepared["feature_names"],
                )
            if algorithm == "kmeans":
                estimator = model.named_steps.get("estimator")
                if estimator is not None:
                    tuning.setdefault("bestParams", {})["n_clusters"] = int(estimator.n_clusters)
            explanation["script"] = _render_training_script(
                task, algorithm, tuning, prepared, effective_task_type,
            )
            evaluated.append({
                "algorithm": algorithm,
                "name": ALGORITHM_BY_ID[algorithm]["name"],
                "family": ALGORITHM_BY_ID[algorithm]["family"],
                "metrics": metrics,
                "featureImportance": importance,
                "tuning": tuning,
                "explanation": explanation,
                "qualityAssessment": quality,
            })

        if not evaluated:
            raise ModelingError("没有与任务类型匹配的候选算法")

        # 落盘 + 登记：每个算法一个模型注册记录
        model_ids: list[str] = []
        with PlatformSession() as session:
            task = session.get(ModelingTask, task_id)  # 重新附着，避免修改分离对象
            training_revision = _next_training_revision(session, task_id)
            for item in evaluated:
                model_id = new_id("mdl")
                model = fitted_models[item["algorithm"]]
                version = _next_model_version(session, task_id, item["algorithm"])
                artifact_schema = {
                    "taskType": effective_task_type,
                    "featureNames": prepared["feature_names"],
                    "targetField": task.target_field,
                    "targetMapping": prepared["target_mapping"],
                    "split": prepared["split"],
                    "decisionThreshold": item["tuning"].get("decisionThreshold"),
                    "thresholdPolicy": item["tuning"].get("thresholdPolicy") or {},
                }
                path, checksum = _save_model_artifact(model, model_id, artifact_schema)
                session.add(GovernedModel(
                    id=model_id,
                    task_id=task_id,
                    name=f"{task.title}·{item['name']}",
                    version=version,
                    training_revision=training_revision,
                    artifact_format_version=2,
                    artifact_checksum=checksum,
                    decision_threshold=item["tuning"].get("decisionThreshold"),
                    threshold_policy_json=item["tuning"].get("thresholdPolicy") or {},
                    tuning_json=item["tuning"],
                    explanation_json=item["explanation"],
                    algorithm=item["algorithm"],
                    # 两级授权：机器门禁通过即开个人自用档；未通过只能等管理员处置
                    status="personal" if _item_gate_ok(item) else "pending",
                    personal_expires_at=(
                        datetime.now() + timedelta(days=settings.modeling_personal_ttl_days)
                        if _item_gate_ok(item) else None
                    ),
                    owner_id=user_id,
                    metrics_json=item["metrics"],
                    features_json=prepared["features"],
                    model_path=path,
                    feature_names_json=prepared["feature_names"],
                    feature_encoders_json=prepared["encoders"],
                    feature_importance_json=item["featureImportance"],
                ))
                model_ids.append(model_id)

            _enforce_personal_quota(session, user_id)
            recommended = _recommended_model(effective_task_type, evaluated)
            best = next(item for item in evaluated if item["algorithm"] == recommended)
            quality_assessment = best["qualityAssessment"]
            task.task_type = effective_task_type
            task.selected_model = recommended
            task.status = "trained"
            task.sample_count = prepared["sample_count"]
            task.metric = summarize_metric(effective_task_type, recommended, best["metrics"])
            task.error_json = None
            task.diagnostics_json = diagnostics
            task.diagnosed_at = datetime.now()
            task.trained_at = datetime.now()
            session.add(ModelingEvaluation(
                id=new_id("mev"),
                task_id=task_id,
                training_revision=training_revision,
                model_ids_json=model_ids,
                result_json={
                    "trainingRevision": training_revision,
                    "modelIds": model_ids,
                    "models": evaluated,
                    "recommendedModel": recommended,
                    "qualityAssessment": quality_assessment,
                    "data": {
                        "sampleCount": prepared["sample_count"],
                        "trainCount": prepared["train_count"],
                        "testCount": prepared["test_count"],
                        "features": prepared["features"],
                        "targetField": task.target_field,
                        "missingPolicy": prepared["impute_policy"],
                        "encodedFeatures": prepared["feature_names"],
                        "targetMapping": prepared["target_mapping"],
                        "split": prepared["split"],
                        "leakageWarnings": prepared["leakage_warnings"],
                        "diagnostics": diagnostics,
                    },
                },
            ))
            session.commit()
    except ModelingError as exc:
        with PlatformSession() as session:
            task = session.get(ModelingTask, task_id)
            task.status = "failed"
            task.error_json = {"reason": str(exc)}
            session.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - 训练异常转为业务失败并披露
        with PlatformSession() as session:
            task = session.get(ModelingTask, task_id)
            task.status = "failed"
            task.error_json = {"reason": f"训练异常：{type(exc).__name__}"}
            session.commit()
        raise ModelingError(f"训练失败：{type(exc).__name__}: {exc}") from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit(action="建模训练", resource=task_id, result="允许",
                detail=f"训练 {len(evaluated)} 个候选模型，耗时 {duration_ms}ms",
                user_name=user_id, role="业务人员")

    evaluation = _evaluation_dict(task_id)
    return {"task": _task_dict(task), "results": evaluation}


def _evaluation_dict(task_id: str, revision: int | None = None) -> dict:
    with PlatformSession() as session:
        query = session.query(ModelingEvaluation).filter_by(task_id=task_id)
        if revision is not None:
            query = query.filter_by(training_revision=revision)
        row = query.order_by(ModelingEvaluation.training_revision.desc()).first()
    if row is None:
        raise TaskNotTrainedError("该任务尚未完成训练")
    return row.result_json


def get_evaluation(task_id: str) -> dict:
    return _evaluation_dict(task_id)


def list_evaluations(task_id: str) -> list[dict]:
    _find_task(task_id)
    with PlatformSession() as session:
        rows = (
            session.query(ModelingEvaluation)
            .filter_by(task_id=task_id)
            .order_by(ModelingEvaluation.training_revision.desc())
            .all()
        )
    return [row.result_json for row in rows]


def _serving_model_for(task: ModelingTask, algorithm: str, requester_id: str) -> GovernedModel:
    """推理取模型：已发布生产版优先；否则 requester 本人未过期的个人档。"""
    with PlatformSession() as session:
        row = (
            session.query(GovernedModel)
            .filter_by(task_id=task.id, algorithm=algorithm, status="published")
            .order_by(GovernedModel.version.desc())
            .first()
        )
        if row is None:
            # A new training run may recommend another pending algorithm. Keep
            # serving the task's current published model until promotion.
            row = (
                session.query(GovernedModel)
                .filter_by(task_id=task.id, status="published")
                .order_by(GovernedModel.published_at.desc(), GovernedModel.version.desc())
                .first()
            )
        if row is None:
            personal = (
                session.query(GovernedModel)
                .filter_by(task_id=task.id, owner_id=requester_id, status="personal")
                .order_by(GovernedModel.created_at.desc())
                .all()
            )
            row = next((item for item in personal if _personal_active(item)), None)
    if row is None:
        raise TaskNotTrainedError("该任务尚无可用模型：发布版未就绪，且您没有可用的个人档模型")
    return row


def predict(task_id: str, records: list[dict], user_id: str) -> dict:
    """按任务推荐模型推理：编码还原 → 预测 → 留痕与计数。"""
    task = _find_task(task_id)
    if task.status != "trained" or not task.selected_model:
        raise TaskNotTrainedError("该任务尚未完成训练，无法推理")
    model_row = _serving_model_for(task, task.selected_model, user_id)
    artifact = _load_model_artifact(model_row.id)
    feature_names = list(model_row.feature_names_json or [])
    encoders = model_row.feature_encoders_json or {}
    if artifact["formatVersion"] == 2:
        model = artifact["pipeline"]
        X = pd.DataFrame(records).reindex(columns=feature_names)
    else:
        model = artifact["model"]
        rows = [encode_input_record(record, encoders, feature_names) for record in records]
        scaler = artifact.get("scaler")
        X = scaler.transform(rows) if scaler is not None else np.asarray(rows, dtype=float)
        X = pd.DataFrame(X, columns=feature_names)

    predictions = []
    if task.task_type in ("classification", "regression"):
        target_mapping = artifact.get("schema", {}).get("targetMapping") or {}
        inverse_mapping = {int(value): key for key, value in target_mapping.items()}
        proba = None
        if task.task_type == "classification" and hasattr(model, "predict_proba"):
            raw_probability = model.predict_proba(X)[:, 1]
            proba = [round(float(p), 4) for p in raw_probability]
            threshold = float(model_row.decision_threshold) if model_row.decision_threshold is not None else 0.5
            raw = (raw_probability >= threshold).astype(int)
        else:
            raw = model.predict(X)
        for index, record in enumerate(records):
            pred = raw[index]
            item = {
                "record": record,
                "prediction": bool(pred) if task.task_type == "classification" else round(float(pred), 4),
            }
            if proba is not None:
                item["probability"] = proba[index]
            if task.task_type == "classification" and inverse_mapping:
                item["predictionLabel"] = inverse_mapping.get(int(pred), str(pred))
            predictions.append(item)
    elif task.task_type == "anomaly":
        labels = model.predict(X)
        for index, record in enumerate(records):
            predictions.append({
                "record": record,
                "prediction": "异常" if labels[index] == -1 else "正常",
                "isAnomaly": bool(labels[index] == -1),
            })
    else:  # clustering
        labels = model.predict(X)
        for index, record in enumerate(records):
            predictions.append({
                "record": record,
                "prediction": f"簇 {int(labels[index]) + 1}",
                "cluster": int(labels[index]),
            })

    with PlatformSession() as session:
        model_row = session.get(GovernedModel, model_row.id)
        model_row.call_count += 1
        session.add(ModelingInferenceLog(
            id=new_id("mil"),
            task_id=task_id,
            model_id=model_row.id,
            input_json={"records": records},
            output_json={"predictions": predictions},
        ))
        session.commit()
    write_audit(action="模型推理", resource=model_row.id, result="允许",
                detail=f"模型 {model_row.name} 推理 {len(records)} 条记录",
                user_name=user_id, role="业务人员")
    return {
        "taskId": task_id,
        "modelId": model_row.id,
        "modelVersion": model_row.version,
        "scope": "personal" if model_row.status == "personal" else "published",
        "modelStatus": model_row.status,
        "decisionThreshold": float(model_row.decision_threshold) if model_row.decision_threshold is not None else None,
        "predictions": predictions,
    }


def run_prediction(task_id: str, user_id: str, limit: int = 200) -> dict:
    """预测编排：按任务契约取最近数据 → 模型推理 → 聚合为业务可读结果卡。

    仅使用发布版或 requester 本人的个人档模型；推理留痕由 predict 统一记录。
    """
    task = _find_task(task_id)
    if task.status != "trained" or not task.selected_model:
        raise TaskNotTrainedError("该任务尚未完成训练，无法预测")
    model_row = _serving_model_for(task, task.selected_model, user_id)
    feature_names = list(model_row.feature_names_json or [])
    frame = _training_frame(task, user_id, "模型预测取数")
    if frame.empty:
        raise TaskNotTrainedError("当前数据源没有可用于预测的记录")
    recent = frame.tail(max(1, min(int(limit), 500)))
    records = recent.reindex(columns=feature_names).to_dict("records")
    result = predict(task_id, records, user_id)
    predictions = result["predictions"]
    card = {
        "kind": "prediction",
        "taskId": task_id,
        "modelId": result["modelId"],
        "modelVersion": result["modelVersion"],
        "scope": result["scope"],
        "taskType": task.task_type,
        "algorithmName": ALGORITHM_BY_ID.get(model_row.algorithm, {}).get("name", model_row.algorithm),
        "targetLabel": task.target_label or task.target_field,
        "sampleCount": len(predictions),
        "factors": [
            {"name": item.get("name"), "importance": item.get("importance")}
            for item in (model_row.feature_importance_json or [])[:3]
        ],
    }
    if task.task_type == "regression":
        values = [item["prediction"] for item in predictions if isinstance(item["prediction"], (int, float))]
        mean = round(sum(values) / len(values), 2) if values else None
        card["headline"] = f"基于最近 {len(values)} 条记录，{card['targetLabel']}预测均值 {mean if mean is not None else '—'}"
        card["stats"] = {"mean": mean, "min": round(min(values), 2) if values else None, "max": round(max(values), 2) if values else None}
    elif task.task_type == "classification":
        positive = sum(1 for item in predictions if item["prediction"])
        share = round(positive * 100 / len(predictions), 1) if predictions else 0.0
        card["headline"] = f"最近 {len(predictions)} 条记录中 {positive} 条被判定为「{card['targetLabel']}」（{share}%）"
        card["stats"] = {"positive": positive, "share": share, "threshold": result.get("decisionThreshold")}
    elif task.task_type == "clustering":
        groups: dict[int, int] = {}
        for item in predictions:
            groups[item.get("cluster", -1)] = groups.get(item.get("cluster", -1), 0) + 1
        card["kind"] = "group"
        card["headline"] = f"最近 {len(predictions)} 条记录被自动分为 {len(groups)} 群"
        card["groups"] = [
            {"cluster": cluster + 1, "size": size, "share": round(size * 100 / len(predictions), 1)}
            for cluster, size in sorted(groups.items())
        ]
    else:  # anomaly
        flagged = [item for item in predictions if item.get("isAnomaly")]
        card["kind"] = "anomaly"
        card["headline"] = f"最近 {len(predictions)} 条记录中 {len(flagged)} 条偏离正常模式"
        card["stats"] = {"anomalyCount": len(flagged), "share": round(len(flagged) * 100 / len(predictions), 1) if predictions else 0.0}
    _record_prediction_trace(task, model_row, user_id, card, recent)
    return card


def _record_prediction_trace(task, model_row, user_id: str, card: dict, recent) -> None:
    """回归/分类的聚合预测落一条留痕，供偏差监控回填实际值对比。"""
    if task.task_type == "regression":
        metric_kind, value = "mean", card.get("stats", {}).get("mean")
    elif task.task_type == "classification":
        metric_kind, value = "share", card.get("stats", {}).get("share")
    else:
        return
    if value is None:
        return
    period_start = period_end = None
    if task.time_field and task.time_field in recent.columns:
        times = pd.to_datetime(recent[task.time_field], errors="coerce").dropna()
        if len(times):
            period_start, period_end = times.min().to_pydatetime(), times.max().to_pydatetime()
    try:
        from app.tenancy.service import tenant_id_for_source

        with PlatformSession() as session:
            tenant_id = tenant_id_for_source(session, task.source_id)
            session.add(ModelPredictionTrace(
                id=new_id("mpt"), tenant_id=tenant_id, task_id=task.id, model_id=model_row.id,
                user_id=user_id, scope=card.get("scope", "published"), metric_kind=metric_kind,
                period_start=period_start, period_end=period_end,
                predicted_value=float(value), sample_count=int(card.get("sampleCount") or 0),
            ))
            session.commit()
    except Exception:  # noqa: BLE001 - 留痕失败不影响预测交付
        pass


def _actual_for_window(task, frame, trace) -> float | None:
    """按留痕的时间窗（或全量近期数据）计算实际值：回归取均值、分类取正例占比。"""
    window = frame
    if trace.period_start is not None and trace.period_end is not None and task.time_field and task.time_field in frame.columns:
        times = pd.to_datetime(frame[task.time_field], errors="coerce")
        window = frame[(times >= trace.period_start) & (times <= trace.period_end)]
    if task.target_field not in window.columns:
        return None
    values = pd.to_numeric(window[task.target_field], errors="coerce").dropna()
    if not len(values):
        return None
    if trace.metric_kind == "share":
        return float((values > 0.5).mean() * 100) if values.max() <= 1 else None
    return float(values.mean())


def drift_report(tenant_id: str) -> dict:
    """偏差监控：自动回填已结束窗口的实际值，并按偏差率给出模型健康状态。"""
    from app.tenancy.service import tenant_source_ids

    with PlatformSession() as session:
        source_ids = tenant_source_ids(session, tenant_id)
        tasks = session.query(ModelingTask).filter(ModelingTask.source_id.in_(source_ids)).all() if source_ids else []
        task_by_id = {task.id: task for task in tasks}
        traces = (
            session.query(ModelPredictionTrace)
            .filter_by(tenant_id=tenant_id)
            .order_by(ModelPredictionTrace.created_at.desc())
            .all()
        )
        traces = [trace for trace in traces if trace.task_id in task_by_id]
        models = {
            row.id: row
            for row in session.query(GovernedModel).filter(
                GovernedModel.task_id.in_(list(task_by_id) or ["-none-"])
            ).all()
        }
        # 自动回填：仅处理实际值缺失的留痕，按任务分组避免重复取数
        pending = [trace for trace in traces if trace.actual_value is None]
        by_task: dict[str, list] = {}
        for trace in pending:
            by_task.setdefault(trace.task_id, []).append(trace)
        for task_id, task_traces in by_task.items():
            task = task_by_id[task_id]
            try:
                frame = _training_frame(task, "system", "偏差监控回填")
            except Exception:  # noqa: BLE001 - 取数失败保留未回填状态，下次再试
                continue
            for trace in task_traces:
                actual = _actual_for_window(task, frame, trace)
                if actual is not None:
                    trace.actual_value = float(actual)
                    trace.filled_at = datetime.now()
            session.commit()

    items = []
    grouped: dict[str, list] = {}
    for trace in traces:
        grouped.setdefault(trace.model_id, []).append(trace)
    for model_id, model_traces in grouped.items():
        model = models.get(model_id)
        if model is None:
            continue
        deviations = []
        for trace in model_traces:
            if trace.actual_value is None:
                continue
            actual = float(trace.actual_value)
            predicted = float(trace.predicted_value)
            base = max(abs(actual), 1e-6) if trace.metric_kind == "mean" else 100.0
            deviations.append(abs(actual - predicted) / base * 100)
        avg_deviation = round(sum(deviations) / len(deviations), 1) if deviations else None
        max_deviation = round(max(deviations), 1) if deviations else None
        status = "pending" if avg_deviation is None else ("drift" if avg_deviation >= 25 else "watch" if avg_deviation >= 10 else "normal")
        items.append({
            "modelId": model_id,
            "modelName": model.name,
            "modelVersion": model.version,
            "modelStatus": model.status,
            "taskId": model.task_id,
            "taskTitle": task_by_id[model.task_id].title if model.task_id in task_by_id else "",
            "metricKind": model_traces[0].metric_kind,
            "traces": len(model_traces),
            "filled": len(deviations),
            "avgDeviation": avg_deviation,
            "maxDeviation": max_deviation,
            "status": status,
            "lastPredictedAt": model_traces[0].created_at.strftime("%m-%d %H:%M"),
        })
    items.sort(key=lambda item: (item["status"] != "drift", -(item["avgDeviation"] or 0)))
    return {
        "items": items,
        "summary": {
            "models": len(items),
            "drift": sum(1 for item in items if item["status"] == "drift"),
            "watch": sum(1 for item in items if item["status"] == "watch"),
            "pending": sum(1 for item in items if item["status"] == "pending"),
        },
        "thresholds": {"watch": 10, "drift": 25},
    }


def list_models(status: str = "", task_id: str = "") -> list[dict]:
    with PlatformSession() as session:
        query = session.query(GovernedModel).order_by(GovernedModel.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        if task_id:
            query = query.filter_by(task_id=task_id)
        rows = query.all()
    return [_model_dict(row) for row in rows]


def model_version_history(task_id: str) -> dict:
    task = _find_task(task_id)
    return {
        "task": _task_dict(task),
        "models": list_models(task_id=task_id),
        "evaluations": list_evaluations(task_id),
    }


def _semantic_terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(text or "").lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    terms = {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}
    terms.update(re.findall(r"[a-z0-9_]{2,}", normalized))
    return terms


def find_reusable_models(question: str, source_id: int, task_type: str = "", viewer_id: str | None = None) -> list[dict]:
    """Discover reusable models for Agent reuse; never returns pending artifacts.

    候选范围带查看者身份：已发布模型对全租户可见；个人档仅所有者本人且未过期时可见。
    """
    query_terms = _semantic_terms(question)
    with PlatformSession() as session:
        scope = (
            or_(
                GovernedModel.status == "published",
                (GovernedModel.status == "personal") & (GovernedModel.owner_id == viewer_id),
            )
            if viewer_id
            else GovernedModel.status == "published"
        )
        rows = (
            session.query(GovernedModel, ModelingTask)
            .join(ModelingTask, ModelingTask.id == GovernedModel.task_id)
            .filter(scope, ModelingTask.source_id == source_id)
            .all()
        )
    matches = []
    for model, task in rows:
        if task_type and task.task_type != task_type:
            continue
        if model.status == "personal" and not _personal_active(model):
            continue
        document = " ".join((task.title, task.goal, task.target_label, task.target_field, " ".join(task.features_json or [])))
        model_terms = _semantic_terms(document)
        overlap = len(query_terms & model_terms)
        score = overlap / max(1, min(len(query_terms), len(model_terms)))
        if score < 0.12:
            continue
        matches.append({
            "modelId": model.id, "taskId": task.id, "name": model.name,
            "version": model.version, "algorithm": model.algorithm,
            "taskType": task.task_type, "targetField": task.target_field,
            "targetLabel": task.target_label, "features": list(model.features_json or []),
            "metric": _metric_summary(model.metrics_json or {}), "matchScore": round(score, 4),
            "decisionThreshold": float(model.decision_threshold) if model.decision_threshold is not None else None,
            "scope": "personal" if model.status == "personal" else "published",
            "status": model.status,
        })
    matches.sort(key=lambda item: (item["matchScore"], item["version"]), reverse=True)
    return matches[:3]


def _get_model(model_id: str) -> GovernedModel:
    with PlatformSession() as session:
        row = session.get(GovernedModel, model_id)
    if row is None:
        raise ModelNotFoundError("模型不存在")
    return row


def _transition(model_id: str, target: str, user_id: str, action_label: str,
                allowed_from: set[str]) -> dict:
    model = _get_model(model_id)
    if model.status not in allowed_from:
        raise ModelingError(f"当前状态「{MODEL_STATUS_LABELS.get(model.status, model.status)[0]}」不允许{action_label}")
    with PlatformSession() as session:
        row = session.get(GovernedModel, model_id)
        row.status = target
        if target == "published":
            row.published_at = datetime.now()
        elif target == "retired":
            row.retired_at = datetime.now()
        session.commit()
    write_audit(action=action_label, resource=model_id, result="允许",
                detail=f"模型 {row.name} → {MODEL_STATUS_LABELS[target][0]}", user_name=user_id,
                role="管理人员")
    return _model_dict(row)


def review_model(model_id: str, decision: str, comment: str, user_id: str) -> dict:
    """复核只形成发布建议；通过后仍需显式发布，避免未验收模型被业务调用。"""
    target = "reviewing" if decision == "pass" else "rejected"
    _get_model(model_id)
    with PlatformSession() as session:
        row = session.get(GovernedModel, model_id)
        row.reviewed_by = user_id
        row.reviewed_at = datetime.now()
        session.commit()
    return _transition(model_id, target, user_id, "模型复核", REVIEW_ALLOWED_FROM)


def publish_model(model_id: str, user_id: str) -> dict:
    model = _get_model(model_id)
    if model.status not in PUBLISH_ALLOWED_FROM:
        raise ModelingError(f"当前状态「{MODEL_STATUS_LABELS.get(model.status, model.status)[0]}」不允许模型发布")
    quality = (model.explanation_json or {}).get("qualityAssessment") or {}
    if quality.get("publishable") is False:
        raise ModelingError(f"模型效果未达到发布门槛：{quality.get('reason') or '请改进数据或特征后重新训练'}")
    now = datetime.now()
    with PlatformSession() as session:
        # 一个任务只能有一个生产模型；发布新版时原子下线旧版，支持稳定回滚审计。
        active_rows = (
            session.query(GovernedModel)
            .filter_by(task_id=model.task_id, status="published")
            .all()
        )
        for active in active_rows:
            active.status = "retired"
            active.retired_at = now
        row = session.get(GovernedModel, model_id)
        row.status = "published"
        row.published_at = now
        row.retired_at = None
        session.commit()
    write_audit(
        action="模型发布", resource=model_id, result="允许",
        detail=f"模型 {row.name} v{row.version} 已发布，旧生产版本已自动下线",
        user_name=user_id, role="管理人员",
    )
    return _model_dict(row)


def submit_for_review(model_id: str, user_id: str) -> dict:
    """所有者把个人档送进复核队列：personal → pending。"""
    return _transition(model_id, "pending", user_id, "提交复核", {"personal"})


def publish_model_direct(model_id: str, user_id: str) -> dict:
    """管理员免审直发：机器门禁不可跳过；动作单独留痕并可筛。"""
    if settings.require_review_for_admin_publish:
        raise ModelingError("当前企业已启用四眼原则，管理员直发被禁止，请走复核流程")
    model = _get_model(model_id)
    if model.status not in DIRECT_PUBLISH_ALLOWED_FROM:
        raise ModelingError(f"当前状态「{MODEL_STATUS_LABELS.get(model.status, model.status)[0]}」不允许直发")
    if not _gate_publishable(model):
        quality = (model.explanation_json or {}).get("qualityAssessment") or {}
        raise ModelingError(f"模型效果未达到发布门槛：{quality.get('reason') or '请改进数据或特征后重新训练'}")
    now = datetime.now()
    with PlatformSession() as session:
        active_rows = session.query(GovernedModel).filter_by(task_id=model.task_id, status="published").all()
        for active in active_rows:
            active.status = "retired"
            active.retired_at = now
        row = session.get(GovernedModel, model_id)
        row.status = "published"
        row.published_at = now
        row.retired_at = None
        row.direct_publish = True
        session.commit()
    write_audit(
        action="模型直发（免审）", resource=model_id, result="允许",
        detail=f"模型 {row.name} v{row.version} 由管理员免审直发（机器门禁已通过）",
        user_name=user_id, role="管理人员",
    )
    return _model_dict(row)


def purge_retired_artifacts(grace_days: int | None = None) -> dict:
    """存储保留策略：retired 超过宽限期的工件文件物理清除，元数据与指纹永留。"""
    grace = settings.modeling_artifact_grace_days if grace_days is None else grace_days
    cutoff = datetime.now() - timedelta(days=grace)
    purged = 0
    with PlatformSession() as session:
        rows = (
            session.query(GovernedModel)
            .filter_by(status="retired")
            .filter(GovernedModel.retired_at.isnot(None), GovernedModel.retired_at < cutoff)
            .all()
        )
        for row in rows:
            if row.model_path and os.path.exists(row.model_path):
                os.remove(row.model_path)
                purged += 1
            row.model_path = ""
        session.commit()
    return {"purged": purged, "graceDays": grace}


def retire_model(model_id: str, user_id: str) -> dict:
    return _transition(model_id, "retired", user_id, "模型下线", RETIRE_ALLOWED_FROM)


def rollback_model(model_id: str, user_id: str) -> dict:
    target = _get_model(model_id)
    if target.status != "retired":
        raise ModelingError("仅可回滚到已下线且产物仍完整的历史版本")
    if not target.model_path or not os.path.exists(target.model_path):
        raise TaskNotTrainedError("历史模型文件缺失，无法回滚")
    now = datetime.now()
    with PlatformSession() as session:
        active_rows = session.query(GovernedModel).filter_by(task_id=target.task_id, status="published").all()
        for active in active_rows:
            active.status = "retired"
            active.retired_at = now
        row = session.get(GovernedModel, model_id)
        row.status = "published"
        row.published_at = now
        row.retired_at = None
        task = session.get(ModelingTask, target.task_id)
        task.selected_model = row.algorithm
        session.commit()
    write_audit(
        action="模型回滚", resource=model_id, result="允许",
        detail=f"恢复 {row.name} v{row.version} 为生产版本", user_name=user_id, role="管理人员",
    )
    return _model_dict(row)


def create_threshold_variant(model_id: str, threshold: float, reason: str, user_id: str) -> dict:
    source = _get_model(model_id)
    if source.status not in {"published", "reviewing", "retired"}:
        raise ModelingError("模型需先完成评估，才能创建阈值版本")
    source_task = _find_task(source.task_id)
    if source_task.task_type != "classification":
        raise ModelingError("仅分类模型支持业务阈值调整")
    with PlatformSession() as session:
        version = _next_model_version(session, source.task_id, source.algorithm)
        variant = GovernedModel(
            id=new_id("mdl"), task_id=source.task_id, name=source.name, version=version,
            training_revision=source.training_revision,
            artifact_format_version=source.artifact_format_version,
            artifact_checksum=source.artifact_checksum,
            algorithm=source.algorithm, status="reviewing", owner_id=user_id,
            metrics_json=source.metrics_json or {}, features_json=source.features_json or [],
            model_path=source.model_path, feature_names_json=source.feature_names_json or [],
            feature_encoders_json=source.feature_encoders_json or {},
            feature_importance_json=source.feature_importance_json or [],
            decision_threshold=threshold,
            threshold_policy_json={
                "source": "manual_business_review", "reason": reason,
                "basedOnModelId": source.id, "previousThreshold": float(source.decision_threshold or 0.5),
            },
            tuning_json=source.tuning_json or {}, explanation_json=source.explanation_json or {},
            reviewed_by=user_id, reviewed_at=datetime.now(),
        )
        session.add(variant)
        session.commit()
        session.refresh(variant)
    write_audit(
        action="调整模型阈值", resource=variant.id, result="允许",
        detail=f"基于 {source.id} 创建 v{variant.version}，阈值 {threshold:.2f}；{reason}",
        user_name=user_id, role="管理人员",
    )
    return _model_dict(variant)
