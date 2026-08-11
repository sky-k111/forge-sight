"""建模服务：任务创建 → 取数 → 训练 → 落盘 → 登记 → 治理，全链路审计。

训练为同步执行（样本 ≤ 1 万行，秒级）。模块级引用 PlatformSession / write_audit，
便于测试用 conftest monkeypatch 替换。
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import joblib
import numpy as np
import pandas as pd

from app.agent.ids import new_id
from app.audit.service import write_audit
from app.config import settings
from app.db.models import GovernedModel, ModelingEvaluation, ModelingInferenceLog, ModelingTask
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly
from app.modeling import engine
from app.modeling.engine import (
    ALGORITHM_BY_ID,
    TASK_TYPE_LABELS,
    encode_input_record,
    evaluate_model,
    extract_importance,
    prepare_dataset,
    summarize_metric,
    train_model,
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
    "reviewing": ("复核中", "blue"),
    "published": ("已发布", "green"),
    "rejected": ("已驳回", "red"),
    "retired": ("已下线", "gray"),
}
# 各治理动作的合法来源状态（比全局迁移表更贴合页面按钮语义）：
# 复核 = 一个动作完成判定（pending/reviewing 待定，rejected 可重新复核通过）；
# 发布 = 仅复核中可发布（强制经过复核流程）；下线 = 已发布/已驳回可下线。
REVIEW_ALLOWED_FROM = {"pending", "reviewing", "rejected"}
PUBLISH_ALLOWED_FROM = {"reviewing"}
RETIRE_ALLOWED_FROM = {"published", "rejected"}

TASK_STATUSES = set(STATUS_LABELS)
MODEL_STATUSES = set(MODEL_STATUS_LABELS)


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
    check = execute_readonly(
        payload.datasetSql, action="建模取数预检", user_name=payload.ownerId
    )
    if not check.ok:
        raise ModelingError(f"取数 SQL 未通过安全校验：{check.error}")

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


def _storage_dir() -> str:
    directory = settings.modeling_storage_dir
    if not os.path.isabs(directory):
        directory = os.path.join(os.path.dirname(__file__), "..", "..", directory)
    os.makedirs(directory, exist_ok=True)
    return os.path.abspath(directory)


def _save_model_artifact(model, scaler, model_id: str) -> str:
    path = os.path.join(_storage_dir(), f"{model_id}.joblib")
    joblib.dump({"model": model, "scaler": scaler}, path)
    return path


def _load_model_artifact(model_id: str) -> tuple[object, object]:
    path = os.path.join(_storage_dir(), f"{model_id}.joblib")
    if not os.path.exists(path):
        raise TaskNotTrainedError("模型文件缺失，请重新训练")
    payload = joblib.load(path)
    return payload["model"], payload["scaler"]


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


def train_task(task_id: str, strategy: str, candidate_ids: list[str], manual_model_id: str | None,
               user_id: str) -> dict:
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
        task = session.get(ModelingTask, task_id)
        task.status = "training"
        session.commit()

    started = time.perf_counter()
    try:
        result = execute_readonly(task.dataset_sql, action="建模取数", user_name=user_id)
        if not result.ok:
            raise ModelingError(f"取数失败：{result.error}")
        if result.truncated:
            raise ModelingError(f"训练数据超过 {settings.sql_max_rows} 行上限，请缩小取数范围")
        df = pd.DataFrame(result.rows, columns=result.columns)
        prepared = prepare_dataset(df, task.task_type, target_field=task.target_field,
                                   features=list(task.features_json or []))

        evaluated: list[dict] = []
        fitted_models: dict[str, object] = {}
        for algorithm in candidates:
            if task.task_type not in engine.SUPPORTED_FAMILIES[algorithm]:
                continue  # 不适用任务类型的候选算法自动跳过
            model = train_model(task.task_type, algorithm, prepared["X_train"],
                                prepared["y_train"], prepared["sample_count"])
            fitted_models[algorithm] = model
            metrics = evaluate_model(task.task_type, algorithm, model,
                                     prepared["X_test"], prepared["y_test"],
                                     prepared["sample_count"])
            importance = extract_importance(algorithm, model, prepared["feature_names"])
            evaluated.append({
                "algorithm": algorithm,
                "name": ALGORITHM_BY_ID[algorithm]["name"],
                "family": ALGORITHM_BY_ID[algorithm]["family"],
                "metrics": metrics,
                "featureImportance": importance,
            })

        if not evaluated:
            raise ModelingError("没有与任务类型匹配的候选算法")

        # 落盘 + 登记：每个算法一个模型注册记录
        model_ids: list[str] = []
        with PlatformSession() as session:
            task = session.get(ModelingTask, task_id)  # 重新附着，避免修改分离对象
            for item in evaluated:
                model_id = new_id("mdl")
                model = fitted_models[item["algorithm"]]
                path = _save_model_artifact(model, prepared["scaler"], model_id)
                session.add(GovernedModel(
                    id=model_id,
                    task_id=task_id,
                    name=f"{task.title}·{item['name']}",
                    version=1,
                    algorithm=item["algorithm"],
                    status="pending",
                    owner_id=user_id,
                    metrics_json=item["metrics"],
                    features_json=prepared["features"],
                    model_path=path,
                    feature_names_json=prepared["feature_names"],
                    feature_encoders_json=prepared["encoders"],
                    feature_importance_json=item["featureImportance"],
                ))
                model_ids.append(model_id)

            recommended = _recommended_model(task.task_type, evaluated)
            best = next(item for item in evaluated if item["algorithm"] == recommended)
            task.selected_model = recommended
            task.status = "trained"
            task.sample_count = prepared["sample_count"]
            task.metric = summarize_metric(task.task_type, recommended, best["metrics"])
            task.error_json = None
            task.trained_at = datetime.now()
            session.add(ModelingEvaluation(
                id=new_id("mev"),
                task_id=task_id,
                result_json={
                    "models": evaluated,
                    "recommendedModel": recommended,
                    "data": {
                        "sampleCount": prepared["sample_count"],
                        "trainCount": prepared["train_count"],
                        "testCount": prepared["test_count"],
                        "features": prepared["features"],
                        "targetField": task.target_field,
                        "missingPolicy": prepared["impute_policy"],
                        "encodedFeatures": prepared["feature_names"],
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


def _evaluation_dict(task_id: str) -> dict:
    with PlatformSession() as session:
        row = session.query(ModelingEvaluation).filter_by(task_id=task_id).first()
    if row is None:
        raise TaskNotTrainedError("该任务尚未完成训练")
    return row.result_json


def get_evaluation(task_id: str) -> dict:
    return _evaluation_dict(task_id)


def _latest_model_for(task: ModelingTask, algorithm: str) -> GovernedModel:
    with PlatformSession() as session:
        row = (
            session.query(GovernedModel)
            .filter_by(task_id=task.id, algorithm=algorithm)
            .order_by(GovernedModel.created_at.desc())
            .first()
        )
    if row is None:
        raise TaskNotTrainedError("该任务尚未完成训练")
    return row


def predict(task_id: str, records: list[dict], user_id: str) -> dict:
    """按任务推荐模型推理：编码还原 → 预测 → 留痕与计数。"""
    task = _find_task(task_id)
    if task.status != "trained" or not task.selected_model:
        raise TaskNotTrainedError("该任务尚未完成训练，无法推理")
    model_row = _latest_model_for(task, task.selected_model)
    model, scaler = _load_model_artifact(model_row.id)
    feature_names = list(model_row.feature_names_json or [])
    encoders = model_row.feature_encoders_json or {}

    rows = [encode_input_record(record, encoders, feature_names) for record in records]
    X = scaler.transform(rows) if scaler is not None else np.asarray(rows, dtype=float)
    X = pd.DataFrame(X, columns=feature_names)  # 带列名预测，与训练一致

    predictions = []
    if task.task_type in ("classification", "regression"):
        raw = model.predict(X)
        proba = None
        if task.task_type == "classification" and hasattr(model, "predict_proba"):
            proba = [round(float(p), 4) for p in model.predict_proba(X)[:, 1]]
        for index, record in enumerate(records):
            pred = raw[index]
            item = {
                "record": record,
                "prediction": bool(pred) if task.task_type == "classification" else round(float(pred), 4),
            }
            if proba is not None:
                item["probability"] = proba[index]
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
    return {"taskId": task_id, "predictions": predictions}


def list_models(status: str = "") -> list[dict]:
    with PlatformSession() as session:
        query = session.query(GovernedModel).order_by(GovernedModel.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        rows = query.all()
    return [_model_dict(row) for row in rows]


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
    """复核判定：通过 → published，驳回 → rejected；pending/reviewing/rejected 均可触发。"""
    target = "published" if decision == "pass" else "rejected"
    if target == "published":
        _get_model(model_id)  # 先验证存在（404 语义），再登记复核人
        with PlatformSession() as session:
            row = session.get(GovernedModel, model_id)
            row.reviewed_by = user_id
            session.commit()
    return _transition(model_id, target, user_id, "模型复核", REVIEW_ALLOWED_FROM)


def publish_model(model_id: str, user_id: str) -> dict:
    return _transition(model_id, "published", user_id, "模型发布", PUBLISH_ALLOWED_FROM)


def retire_model(model_id: str, user_id: str) -> dict:
    return _transition(model_id, "retired", user_id, "模型下线", RETIRE_ALLOWED_FROM)
