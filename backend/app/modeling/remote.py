"""Source-scoped remote inference assets; weights always stay at the provider."""

import math
import json
import time
from collections import Counter
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from jsonschema import Draft202012Validator, ValidationError, SchemaError
from referencing import Registry
from pydantic import BaseModel, Field

from app.db.models import RemoteModelAsset, RemoteModelCall, PlatformUser, DataSource
from app.db.platform import PlatformSession
from app.gateway.executor import execute_readonly
from app.modeling.mcp_client import ModelMCPClient, RemoteModelError, check_endpoint
from app.tenancy.service import ensure_source_access


def context_for_user(user_id, source_id):
    from app.tenancy.service import resolve_tenant_context
    with PlatformSession() as session:
        user = session.get(PlatformUser, user_id) if user_id else None
        source = session.get(DataSource, source_id)
        if not user or not user.enabled or not source:
            raise HTTPException(403, "需要有效账号及数据源权限")
        return resolve_tenant_context({"id": user.id, "role": user.role}, source.tenant_id)


def find_models(question, source_id, task_type, viewer_id):
    from app.modeling.service import _semantic_terms
    if not viewer_id:
        return []
    try:
        context = context_for_user(viewer_id, source_id)
    except HTTPException:
        return []
    matches = []
    terms = _semantic_terms(question)
    for asset in list_assets(context, source_id):
        config = asset["config"]
        if asset["status"] != "published" or (task_type and task_type != config["taskType"]):
            continue
        target = _semantic_terms(config["name"] + " " + config["description"])
        score = len(terms & target) / max(1, min(len(terms), len(target)))
        if score >= .12:
            matches.append({"modelId": asset["id"], "taskId": asset["id"], "name": asset["name"],
                            "version": 1, "remoteVersion": config["version"], "taskType": config["taskType"],
                            "matchScore": round(score, 4), "executionType": "mcp", "scope": "published",
                            "status": "published", "features": list(config["fieldMapping"].values())})
    return matches


class RemoteConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sourceId: int = Field(gt=0)
    endpoint: str = Field(max_length=500)
    credentialRef: str = Field(default="", max_length=100)
    toolName: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    taskType: str = Field(default="regression", pattern="^(regression|classification|clustering|anomaly)$")
    description: str = Field(min_length=1, max_length=1000)
    datasetSql: str = Field(min_length=1, max_length=10000)
    fieldMapping: dict[str, str] = Field(min_length=1, max_length=100)
    recordsArgument: str = Field(default="records", min_length=1, max_length=80)
    outputKey: str = Field(default="predictions", min_length=1, max_length=80)


def can_configure(row, context):
    return context.can_administer or bool(context.user_id and row.owner_id == context.user_id)


def serialize(row, context=None):
    payload = {"id": row.id, "name": row.name, "sourceId": row.source_id,
               "status": row.status, "executionType": "mcp", "config": row.config_json,
               "validation": row.validation_json}
    if context is not None:
        payload["canEdit"] = can_configure(row, context)
    return payload


def require_asset_configuration(row, context):
    if not can_configure(row, context):
        raise HTTPException(403, "只能配置本人接入的远程模型，管理员可管理企业全部模型")


def get_asset(session, model_id, context):
    row = session.query(RemoteModelAsset).filter_by(id=model_id, tenant_id=context.tenant_id).first()
    if row is None:
        raise HTTPException(404, "远程模型不存在")
    ensure_source_access(session, row.source_id, context.tenant_id)
    return row


def list_assets(context, source_id):
    with PlatformSession() as session:
        ensure_source_access(session, source_id, context.tenant_id)
        rows = session.query(RemoteModelAsset).filter_by(tenant_id=context.tenant_id, source_id=source_id).all()
        return [serialize(row, context) for row in rows]


def save_asset(config, context, model_id=None):
    check_endpoint(config.endpoint)
    with PlatformSession() as session:
        ensure_source_access(session, config.sourceId, context.tenant_id)
        if model_id:
            row = get_asset(session, model_id, context)
            require_asset_configuration(row, context)
            if row.status == "published":
                raise RemoteModelError("请先停用已发布模型，再修改配置并重新验证")
        else:
            row = RemoteModelAsset(id="mcp-" + uuid4().hex, tenant_id=context.tenant_id,
                                   owner_id=context.user_id or "", source_id=config.sourceId)
            session.add(row)
        row.name, row.source_id = config.name, config.sourceId
        row.config_json, row.validation_json, row.status = config.model_dump(), {}, "draft"
        session.commit()
        return serialize(row, context)


def project_records(records, mapping):
    if not isinstance(records, list) or not 1 <= len(records) <= 200:
        raise RemoteModelError("每次调用需要 1–200 条记录")
    projected = []
    for record in records:
        if not isinstance(record, dict) or any(field not in record for field in mapping.values()):
            raise RemoteModelError("数据缺少映射字段，请检查字段映射与 SQL")
        projected.append({remote: record[local] for remote, local in mapping.items()})
    projected = jsonable_encoder(projected)
    try:
        if len(json.dumps(projected, allow_nan=False).encode()) > 1_000_000:
            raise RemoteModelError("输入超过 1 MB 限制")
    except (ValueError, TypeError) as exc:
        raise RemoteModelError("输入包含无法序列化的值") from exc
    return projected


def validate_schema(schema, value):
    # Never retrieve remote $refs from an untrusted provider schema.
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except (ValidationError, SchemaError) as exc:
        raise RemoteModelError("模型数据不符合工具 JSON Schema") from exc
    except Exception as exc:
        raise RemoteModelError("无法解析工具契约；请使用自包含 JSON Schema") from exc


def check_predictions(result, config, count):
    values = result.get(config["outputKey"])
    if not isinstance(values, list) or len(values) != count:
        raise RemoteModelError("预测结果必须是与输入逐条对应的数组")
    for value in values:
        if config["taskType"] == "regression":
            valid = type(value) in (float, int) and math.isfinite(value)
        elif config["taskType"] == "anomaly":
            valid = type(value) is bool
        else:
            valid = type(value) in (str, int, bool) and len(str(value)) <= 200
        if not valid:
            raise RemoteModelError("预测值类型与任务类型不一致")
    return values


def invoke(model_id, context, records=None, *, validate=False, source_id=None):
    data_origin = "source-query" if records is None else "sample"
    with PlatformSession() as session:
        row = get_asset(session, model_id, context)
        if validate:
            require_asset_configuration(row, context)
        if source_id is not None and row.source_id != source_id:
            raise HTTPException(404, "模型不属于当前授权数据源")
        config, status = dict(row.config_json), row.status
        source = ensure_source_access(session, row.source_id, context.tenant_id)
    if not validate and status != "published":
        raise RemoteModelError("模型尚未发布，不能用于业务预测")
    if validate and status == "published":
        raise RemoteModelError("请先停用已发布模型，再重新验证")
    started, outcome, count = time.monotonic(), "failed", 0
    try:
        if records is None:
            query = execute_readonly(config["datasetSql"], action="远程模型读取特征", user_name=context.user_id or "", source=source)
            if not query.ok:
                raise RemoteModelError("特征查询失败，请检查 SQL 和只读权限")
            records = [dict(zip(query.columns, values)) if not isinstance(values, dict) else values for values in query.rows[:200]]
        projected = project_records(records, config["fieldMapping"])
        count = len(projected)
        with ModelMCPClient(config["endpoint"], config["credentialRef"]) as client:
            tool = next((tool for tool in client.tools() if tool.get("name") == config["toolName"]), None)
            if not tool:
                raise RemoteModelError("配置的预测工具不存在")
            annotations = tool.get("annotations")
            if not isinstance(annotations, dict) or annotations.get("readOnlyHint") is not True:
                raise RemoteModelError("仅支持明确声明 readOnlyHint 的预测工具")
            schema = tool.get("inputSchema", {})
            if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
                raise RemoteModelError("工具输入必须声明对象及记录数组参数")
            if config["recordsArgument"] not in schema.get("properties", {}):
                raise RemoteModelError("工具未声明配置的记录数组参数")
            if not isinstance(schema.get("required", []), list) or any(
                item != config["recordsArgument"] for item in schema.get("required", [])
            ):
                raise RemoteModelError("工具需要额外参数，请使用统一预测适配接口")
            if not validate and (row.validation_json.get("inputSchema") != schema
                                 or row.validation_json.get("outputSchema") != tool.get("outputSchema")):
                raise RemoteModelError("远程工具契约已变化，请停用并重新验证")
            arguments = {config["recordsArgument"]: projected}
            validate_schema(schema, arguments)
            output = client.call(config["toolName"], arguments)
            if tool.get("outputSchema"):
                validate_schema(tool["outputSchema"], output)
            values = check_predictions(output, config, count)
        if validate:
            with PlatformSession() as session:
                current = get_asset(session, model_id, context)
                if current.config_json != config or current.status != status:
                    raise RemoteModelError("配置已变化，请重新验证")
                current.validation_json = {"sampleCount": count, "inputSchema": schema,
                                           "outputSchema": tool.get("outputSchema"), "kind": "contract-only"}
                if current.status != "published":
                    current.status = "validated"
                session.commit()
        outcome = "validated" if validate else "succeeded"
        card = {"modelId": model_id, "modelVersion": config["version"], "executionType": "mcp",
                "taskId": model_id, "algorithmName": "MCP · " + config["name"], "targetLabel": config["name"], "factors": [],
                "taskType": config["taskType"], "sampleCount": count, "predictions": values,
                "dataOrigin": data_origin,
                "kind": "prediction", "scope": "published" if not validate else "validation",
                "headline": f"远程模型已完成 {count} 条预测" if not validate else f"{count} 条接口样例验证通过（不代表准确率验证）"}
        if config["taskType"] == "regression":
            card["stats"] = {"mean": sum(values) / count, "min": min(values), "max": max(values)}
        elif config["taskType"] == "anomaly":
            card["kind"] = "anomaly"
            card["stats"] = {"anomalyCount": sum(values), "share": round(sum(values)*100/count, 1)}
        else:
            card["kind"] = "group"
            card["groups"] = [{"cluster": str(label), "size": size, "share": round(size*100/count, 1)} for label, size in Counter(values).items()]
        return card
    finally:
        with PlatformSession() as session:
            if validate and outcome == "failed":
                current = get_asset(session, model_id, context)
                if current.config_json == config and current.status == status:
                    current.status, current.validation_json = "draft", {}
            session.add(RemoteModelCall(id=uuid4().hex, model_id=model_id, user_id=context.user_id or "",
                                       status=outcome, record_count=count, duration_ms=int((time.monotonic()-started)*1000)))
            session.commit()


def transition(model_id, context, target):
    with PlatformSession() as session:
        row = get_asset(session, model_id, context)
        if target == "published" and row.status != "validated":
            raise RemoteModelError("发布前必须完成接口样例验证")
        row.status = target
        session.commit()
        return serialize(row, context)
