"""钉钉账号绑定 API。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from starlette.responses import RedirectResponse

from app.agent.ids import new_id
from app.auth.deps import require_user
from app.config import settings
from app.db.models import DataSource, DingTalkBinding, DingTalkOAuthState, ExternalChatConversation
from app.db.platform import PlatformSession
from app.integrations.dingtalk_chat import allowed_source_ids

router = APIRouter(prefix="/api/dingtalk", tags=["dingtalk"])


class DingTalkSourceSelection(BaseModel):
    sourceId: int = Field(ge=1)


def _allowed_source_ids(session, user_id: str, now: datetime | None = None) -> set[int]:
    """兼容 API 内部调用；实际权限规则由机器人入口与 API 共用。"""

    return allowed_source_ids(session, user_id, now)


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
        request_headers["Content-Type"] = "application/json"

    request = UrlRequest(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        detail = body or exc.reason or "unknown"
        raise HTTPException(status_code=502, detail=f"钉钉接口调用失败：{detail}") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail=f"钉钉接口网络异常：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="钉钉接口返回了无法解析的 JSON") from exc


def _exchange_user_access_token(incoming_code: str) -> dict:
    if not settings.dingtalk_client_id:
        raise HTTPException(status_code=500, detail="DINGTALK_CLIENT_ID 未配置")
    if not settings.dingtalk_client_secret:
        raise HTTPException(status_code=500, detail="DINGTALK_CLIENT_SECRET 未配置")

    return _http_json(
        "POST",
        "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        payload={
            "clientId": settings.dingtalk_client_id,
            "clientSecret": settings.dingtalk_client_secret,
            "code": incoming_code,
            "grantType": "authorization_code",
        },
    )


def _get_current_dingtalk_user(user_access_token: str) -> dict:
    return _http_json(
        "GET",
        "https://api.dingtalk.com/v1.0/contact/users/me",
        headers={
            "x-acs-dingtalk-access-token": user_access_token,
        },
    )


def _frontend_redirect(path: str, status: str, message: str) -> RedirectResponse:
    safe_path = path if path and path.startswith("/") else "/login"
    query = urlencode({"dingtalkBind": status, "message": message})
    frontend_base = settings.public_app_url.rstrip("/")
    target = f"{frontend_base}{safe_path}" if frontend_base else safe_path
    return RedirectResponse(url=f"{target}?{query}", status_code=302)


@router.get("/binding/me")
def get_my_binding(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        binding = (
            session.query(DingTalkBinding)
            .filter(DingTalkBinding.user_id == user["id"])
            .first()
        )
        if binding is None:
            return {"bound": False}
        return {
            "bound": binding.bind_status == "bound",
            "bindStatus": binding.bind_status,
            "corpId": binding.corp_id,
            "unionId": binding.union_id,
            "openId": binding.open_id,
            "sourceId": binding.source_id,
            "mobile": binding.mobile,
            "displayName": binding.display_name,
            "avatar": binding.avatar,
            "lastAuthAt": binding.last_auth_at.isoformat() if binding.last_auth_at else None,
        }


@router.get("/binding/sources")
def list_binding_sources(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        allowed_ids = _allowed_source_ids(session, user["id"])
        sources = (
            session.query(DataSource)
            .filter(DataSource.id.in_(allowed_ids))
            .order_by(DataSource.is_default.desc(), DataSource.id)
            .all()
            if allowed_ids
            else []
        )
        binding = session.query(DingTalkBinding).filter_by(user_id=user["id"]).first()
        return {
            "selectedSourceId": binding.source_id if binding else None,
            "items": [
                {"id": source.id, "name": source.name, "type": source.type, "status": source.status}
                for source in sources
            ],
        }


@router.put("/binding/source")
def select_binding_source(
    payload: DingTalkSourceSelection,
    user: dict = Depends(require_user),
) -> dict:
    with PlatformSession() as session:
        binding = session.query(DingTalkBinding).filter_by(user_id=user["id"]).with_for_update().first()
        if binding is None or binding.bind_status != "bound":
            raise HTTPException(status_code=409, detail="请先完成钉钉账号绑定")
        if payload.sourceId not in _allowed_source_ids(session, user["id"]):
            raise HTTPException(status_code=403, detail="该数据源不在当前账号授权范围内")
        source = session.get(DataSource, payload.sourceId)
        if source is None:
            raise HTTPException(status_code=404, detail="数据源不存在")
        binding.source_id = source.id
        binding.updated_at = datetime.now()
        session.commit()
        return {"ok": True, "sourceId": source.id, "sourceName": source.name}


@router.delete("/binding/me")
def unbind_my_dingtalk(user: dict = Depends(require_user)) -> dict:
    with PlatformSession() as session:
        binding = session.query(DingTalkBinding).filter_by(user_id=user["id"]).with_for_update().first()
        if binding is None or binding.bind_status == "unbound":
            return {"ok": True, "bound": False}
        binding.bind_status = "unbound"
        binding.source_id = None
        binding.updated_at = datetime.now()
        session.query(ExternalChatConversation).filter_by(
            channel="dingtalk",
            platform_user_id=user["id"],
            status="active",
        ).update({"status": "archived", "updated_at": datetime.now()})
        session.commit()
        return {"ok": True, "bound": False}


@router.get("/oauth/start")
def start_oauth_bind(
    redirectPath: str = Query(default="/"),
    user: dict = Depends(require_user),
) -> dict:
    if not settings.dingtalk_client_id:
        raise HTTPException(status_code=500, detail="DINGTALK_CLIENT_ID 未配置")
    if not settings.dingtalk_redirect_uri:
        raise HTTPException(status_code=500, detail="DINGTALK_REDIRECT_URI 未配置")

    state_token = secrets.token_urlsafe(24)

    record = DingTalkOAuthState(
        id=new_id("dos"),
        user_id=user["id"],
        state_token=state_token,
        redirect_path=redirectPath or "/",
        purpose="bind",
        expires_at=datetime.now() + timedelta(seconds=settings.dingtalk_oauth_state_ttl_seconds),
        created_at=datetime.now(),
    )

    with PlatformSession() as session:
        session.add(record)
        session.commit()

    auth_url = ""
    if settings.dingtalk_authorize_url_template:
        auth_url = settings.dingtalk_authorize_url_template.format(
            client_id=settings.dingtalk_client_id,
            redirect_uri=quote(settings.dingtalk_redirect_uri, safe=""),
            state=state_token,
        )

    return {
        "ok": True,
        "state": state_token,
        "clientId": settings.dingtalk_client_id,
        "redirectUri": settings.dingtalk_redirect_uri,
        "authUrl": auth_url,
        "message": "已生成钉钉授权链接。",
    }


@router.get("/oauth/callback")
def oauth_callback(
    state: str,
    authCode: str | None = Query(default=None),
    code: str | None = Query(default=None),
):
    redirect_path = "/login"

    try:
        incoming_code = authCode or code
        if not incoming_code:
            return _frontend_redirect(redirect_path, "failed", "缺少 authCode/code")

        now = datetime.now()

        with PlatformSession() as session:
            state_row = (
                session.query(DingTalkOAuthState)
                .filter(DingTalkOAuthState.state_token == state)
                .first()
            )
            if state_row is None:
                return _frontend_redirect(redirect_path, "failed", "无效的 state")
            if state_row.consumed_at is not None:
                redirect_path = state_row.redirect_path or "/login"
                return _frontend_redirect(redirect_path, "failed", "state 已被使用")
            if state_row.expires_at < now:
                redirect_path = state_row.redirect_path or "/login"
                return _frontend_redirect(redirect_path, "failed", "state 已过期")
            if not state_row.user_id:
                redirect_path = state_row.redirect_path or "/login"
                return _frontend_redirect(redirect_path, "failed", "state 未关联平台用户，无法执行绑定")

            target_user_id = state_row.user_id
            redirect_path = state_row.redirect_path or "/login"

            state_row.consumed_at = now
            session.commit()

        token_body = _exchange_user_access_token(incoming_code)
        user_access_token = token_body.get("accessToken")
        corp_id = token_body.get("corpId") or ""

        if not user_access_token:
            return _frontend_redirect(redirect_path, "failed", "钉钉未返回 accessToken")

        profile = _get_current_dingtalk_user(user_access_token)

        union_id = profile.get("unionId")
        open_id = profile.get("openId")
        mobile = profile.get("mobile")
        display_name = profile.get("nick") or ""
        avatar = profile.get("avatarUrl")

        if not union_id and not open_id:
            return _frontend_redirect(redirect_path, "failed", "钉钉未返回 unionId/openId，无法建立绑定关系")

        with PlatformSession() as session:
            identity_filters = []
            if union_id:
                identity_filters.append(and_(DingTalkBinding.corp_id == corp_id, DingTalkBinding.union_id == union_id))
            if open_id:
                identity_filters.append(and_(DingTalkBinding.corp_id == corp_id, DingTalkBinding.open_id == open_id))

            binding = (
                session.query(DingTalkBinding)
                .filter(DingTalkBinding.user_id == target_user_id)
                .first()
            )

            existing_identity_binding = None
            if identity_filters:
                existing_identity_binding = (
                    session.query(DingTalkBinding)
                    .filter(or_(*identity_filters))
                    .first()
                )

            if existing_identity_binding and existing_identity_binding.user_id != target_user_id:
                return _frontend_redirect(redirect_path, "failed", "该钉钉账号已绑定到其他平台用户")

            if binding is None:
                binding = existing_identity_binding

            if binding is None:
                binding = DingTalkBinding(
                    id=new_id("dtb"),
                    user_id=target_user_id,
                    corp_id=corp_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(binding)

            binding.user_id = target_user_id
            binding.corp_id = corp_id
            binding.union_id = union_id
            binding.open_id = open_id
            binding.mobile = mobile
            binding.display_name = display_name
            binding.avatar = avatar
            binding.bind_status = "bound"
            binding.last_auth_at = now
            binding.updated_at = now

            session.commit()

        return _frontend_redirect(redirect_path, "success", "钉钉账号绑定成功")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "钉钉绑定失败"
        return _frontend_redirect(redirect_path, "failed", detail)
    except Exception as exc:  # noqa: BLE001
        return _frontend_redirect(redirect_path, "failed", f"钉钉绑定失败：{exc}")
