"""飞书账号 OAuth 绑定 API；只保存稳定身份，不保存用户 token。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from starlette.responses import RedirectResponse

from app.agent.ids import new_id
from app.audit.service import write_audit
from app.auth.deps import require_user
from app.config import settings
from app.db.models import (
    DataSource,
    ExternalChatConversation,
    FeishuBinding,
    FeishuOAuthState,
    PlatformUser,
)
from app.db.platform import PlatformSession
from app.integrations.dingtalk_chat import allowed_source_ids

router = APIRouter(prefix="/api/feishu", tags=["feishu"])


class FeishuSourceSelection(BaseModel):
    sourceId: int = Field(ge=1)


def _http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = UrlRequest(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        detail = body or exc.reason or "unknown"
        raise HTTPException(status_code=502, detail=f"飞书接口调用失败：{detail}") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail=f"飞书接口网络异常：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="飞书接口返回了无法解析的 JSON") from exc


def _provider_data(body: dict, operation: str) -> dict:
    code = body.get("code", 0)
    if code not in (0, "0", None):
        raise HTTPException(
            status_code=502,
            detail=f"{operation}失败：{body.get('msg') or body.get('message') or code}",
        )
    data = body.get("data")
    return data if isinstance(data, dict) else body


def _exchange_user_access_token(incoming_code: str) -> dict:
    if not settings.feishu_app_id.strip():
        raise HTTPException(status_code=500, detail="FEISHU_APP_ID 未配置")
    if not settings.feishu_app_secret.strip():
        raise HTTPException(status_code=500, detail="FEISHU_APP_SECRET 未配置")
    body = _http_json(
        "POST",
        "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        payload={
            "grant_type": "authorization_code",
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
            "code": incoming_code,
            "redirect_uri": settings.feishu_redirect_uri,
        },
    )
    return _provider_data(body, "飞书授权码换取 user_access_token")


def _get_current_feishu_user(user_access_token: str) -> dict:
    body = _http_json(
        "GET",
        "https://open.feishu.cn/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {user_access_token}"},
    )
    return _provider_data(body, "获取飞书用户信息")


def _frontend_redirect(path: str, status: str, message: str) -> RedirectResponse:
    safe_path = path if path.startswith("/") and not path.startswith("//") else "/login"
    query = urlencode({"feishuBind": status, "message": message})
    frontend_base = settings.public_app_url.rstrip("/")
    target = f"{frontend_base}{safe_path}" if frontend_base else safe_path
    return RedirectResponse(url=f"{target}?{query}", status_code=302)


def _binding_payload(binding: FeishuBinding | None) -> dict:
    if binding is None:
        return {"bound": False}
    return {
        "bound": binding.bind_status == "bound",
        "bindStatus": binding.bind_status,
        "tenantKey": binding.tenant_key,
        "unionId": binding.union_id,
        "openId": binding.open_id,
        "employeeId": binding.employee_id,
        "sourceId": binding.source_id,
        "mobile": binding.mobile,
        "email": binding.email,
        "displayName": binding.display_name,
        "avatar": binding.avatar,
        "lastAuthAt": binding.last_auth_at.isoformat() if binding.last_auth_at else None,
    }


@router.get("/binding/me")
def get_my_binding(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        return _binding_payload(
            session.query(FeishuBinding).filter_by(user_id=user["id"]).first()
        )


@router.get("/binding/sources")
def list_binding_sources(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        allowed_ids = allowed_source_ids(session, user["id"])
        sources = (
            session.query(DataSource)
            .filter(DataSource.id.in_(allowed_ids))
            .order_by(DataSource.is_default.desc(), DataSource.id)
            .all()
            if allowed_ids
            else []
        )
        binding = session.query(FeishuBinding).filter_by(user_id=user["id"]).first()
        return {
            "selectedSourceId": binding.source_id if binding else None,
            "items": [
                {"id": source.id, "name": source.name, "type": source.type, "status": source.status}
                for source in sources
            ],
        }


@router.put("/binding/source")
def select_binding_source(
    payload: FeishuSourceSelection,
    user: dict = Depends(require_user),
) -> dict:
    with PlatformSession() as session:
        binding = (
            session.query(FeishuBinding)
            .filter_by(user_id=user["id"])
            .with_for_update()
            .first()
        )
        if binding is None or binding.bind_status != "bound":
            raise HTTPException(status_code=409, detail="请先完成飞书账号绑定")
        if payload.sourceId not in allowed_source_ids(session, user["id"]):
            raise HTTPException(status_code=403, detail="该数据源不在当前账号授权范围内")
        source = session.get(DataSource, payload.sourceId)
        if source is None:
            raise HTTPException(status_code=404, detail="数据源不存在")
        binding.source_id = source.id
        binding.updated_at = datetime.now()
        session.commit()
        return {"ok": True, "sourceId": source.id, "sourceName": source.name}


@router.delete("/binding/me")
def unbind_my_feishu(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        binding = (
            session.query(FeishuBinding)
            .filter_by(user_id=user["id"])
            .with_for_update()
            .first()
        )
        if binding is None or binding.bind_status == "unbound":
            return {"ok": True, "bound": False}
        binding.bind_status = "unbound"
        binding.source_id = None
        binding.updated_at = datetime.now()
        session.query(ExternalChatConversation).filter_by(
            channel="feishu",
            platform_user_id=user["id"],
            status="active",
        ).update({"status": "archived", "updated_at": datetime.now()})
        session.commit()
    write_audit(
        "解绑飞书账号",
        resource=user["id"],
        result="已记录",
        user_name=user["username"],
        role=user["role"],
    )
    return {"ok": True, "bound": False}


@router.get("/oauth/start")
def start_oauth_bind(
    redirectPath: str = Query(default="/login"),
    user: dict = Depends(require_user),
) -> dict:
    if not settings.feishu_app_id.strip():
        raise HTTPException(status_code=500, detail="FEISHU_APP_ID 未配置")
    if not settings.feishu_app_secret.strip():
        raise HTTPException(status_code=500, detail="FEISHU_APP_SECRET 未配置")
    if not settings.feishu_redirect_uri.strip():
        raise HTTPException(status_code=500, detail="FEISHU_REDIRECT_URI 未配置")

    state_token = secrets.token_urlsafe(24)
    safe_path = redirectPath if redirectPath.startswith("/") and not redirectPath.startswith("//") else "/login"
    record = FeishuOAuthState(
        id=new_id("fos"),
        user_id=user["id"],
        state_token=state_token,
        redirect_path=safe_path,
        purpose="bind",
        expires_at=datetime.now()
        + timedelta(seconds=max(60, settings.feishu_oauth_state_ttl_seconds)),
        created_at=datetime.now(),
    )
    with PlatformSession() as session:
        session.add(record)
        session.commit()

    auth_url = f"{settings.feishu_authorize_url.rstrip('?')}?{urlencode({
        'app_id': settings.feishu_app_id,
        'redirect_uri': settings.feishu_redirect_uri,
        'state': state_token,
    })}"
    return {
        "ok": True,
        "state": state_token,
        "appId": settings.feishu_app_id,
        "redirectUri": settings.feishu_redirect_uri,
        "authUrl": auth_url,
        "message": "已生成飞书授权链接。",
    }


@router.get("/oauth/callback")
def oauth_callback(state: str, code: str | None = Query(default=None)):
    redirect_path = "/login"
    try:
        if not code:
            return _frontend_redirect(redirect_path, "failed", "缺少授权码 code")
        now = datetime.now()
        with PlatformSession() as session:
            state_row = session.query(FeishuOAuthState).filter_by(state_token=state).first()
            if state_row is None:
                return _frontend_redirect(redirect_path, "failed", "无效的 state")
            redirect_path = state_row.redirect_path or "/login"
            if state_row.consumed_at is not None:
                return _frontend_redirect(redirect_path, "failed", "state 已被使用")
            if state_row.expires_at < now:
                return _frontend_redirect(redirect_path, "failed", "state 已过期")
            if not state_row.user_id:
                return _frontend_redirect(redirect_path, "failed", "state 未关联平台用户")
            target_user_id = state_row.user_id
            state_row.consumed_at = now
            session.commit()

        token_payload = _exchange_user_access_token(code)
        user_access_token = str(token_payload.get("access_token") or "")
        if not user_access_token:
            return _frontend_redirect(redirect_path, "failed", "飞书未返回 user_access_token")
        profile = _get_current_feishu_user(user_access_token)
        tenant_key = str(profile.get("tenant_key") or "")
        union_id = profile.get("union_id")
        open_id = profile.get("open_id")
        if not union_id and not open_id:
            return _frontend_redirect(redirect_path, "failed", "飞书未返回 union_id/open_id")

        with PlatformSession() as session:
            identity_filters = []
            if union_id:
                identity_filters.append(
                    and_(FeishuBinding.tenant_key == tenant_key, FeishuBinding.union_id == union_id)
                )
            if open_id:
                identity_filters.append(
                    and_(FeishuBinding.tenant_key == tenant_key, FeishuBinding.open_id == open_id)
                )
            binding = session.query(FeishuBinding).filter_by(user_id=target_user_id).first()
            existing_identity = (
                session.query(FeishuBinding).filter(or_(*identity_filters)).first()
                if identity_filters
                else None
            )
            if existing_identity and existing_identity.user_id != target_user_id:
                return _frontend_redirect(redirect_path, "failed", "该飞书账号已绑定到其他平台用户")
            if binding is None:
                binding = existing_identity
            if binding is None:
                binding = FeishuBinding(id=new_id("fsb"), user_id=target_user_id)
                session.add(binding)
            binding.user_id = target_user_id
            binding.tenant_key = tenant_key
            binding.union_id = union_id
            binding.open_id = open_id
            binding.employee_id = profile.get("user_id")
            binding.mobile = profile.get("mobile")
            binding.email = profile.get("email") or profile.get("enterprise_email")
            binding.display_name = str(profile.get("name") or profile.get("en_name") or "")
            binding.avatar = profile.get("avatar_url") or profile.get("avatar_big")
            binding.bind_status = "bound"
            binding.last_auth_at = now
            binding.updated_at = now
            session.commit()
            platform_user = session.get(PlatformUser, target_user_id)

        write_audit(
            "绑定飞书账号",
            resource=target_user_id,
            result="允许",
            user_name=platform_user.username if platform_user else target_user_id,
            role=platform_user.role if platform_user else "business",
        )
        return _frontend_redirect(redirect_path, "success", "飞书账号绑定成功")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "飞书绑定失败"
        return _frontend_redirect(redirect_path, "failed", detail)
    except Exception:  # noqa: BLE001 - OAuth 回调必须返回可理解的前端状态
        return _frontend_redirect(redirect_path, "failed", "飞书绑定失败，请稍后重试")
