"""站内消息与管理员公告 API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound

from app.audit.service import write_audit
from app.auth.deps import current_user_display, require_user
from app.db.platform import PlatformSession
from app.messages import service
from app.messages.contracts import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementWithdraw,
)

router = APIRouter(prefix="/api/v1", tags=["messages"])

INBOX_RESPONSE_EXAMPLE = {
    "items": [
        {
            "id": "msg_example",
            "type": "weekly_report",
            "sourceLabel": "周报 Agent",
            "title": "生产经营周报",
            "summaryLines": ["本周生产稳定。", "请关注质量波动。"],
            "priority": "normal",
            "isPinned": False,
            "status": "published",
            "isRead": False,
            "readAt": None,
            "publishedAt": "2026-08-10T09:00:00",
            "expiresAt": None,
            "primaryAction": {
                "type": "open_report",
                "label": "查看周报",
                "resourceId": "rpt_example",
                "versionId": "rpv_example",
            },
            "secondaryAction": None,
            "renderMode": "plain_text",
        }
    ],
    "nextCursor": None,
}


def _identity_keys(user: dict) -> set[str]:
    """Return only the stable identity namespace used by recipient rows."""

    return {str(user["id"])} if user.get("id") else set()


def _scope_identity_keys(user: dict) -> set[str]:
    """Keep policy resolution in the same stable identity namespace."""

    return _identity_keys(user)


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以管理公告")


def _audit(user: dict, *, action: str, resource: str, result: str = "允许", detail: str = ""):
    user_name, role = current_user_display(user)
    write_audit(
        action=action,
        resource=resource,
        result=result,
        detail=detail,
        user_name=user_name,
        role=role,
    )


def _raise_domain(exc: Exception):
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=404, detail="消息不存在或无权访问") from exc
    if isinstance(exc, service.AnnouncementConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (KeyError, NoResultFound)):
        if isinstance(exc, NoResultFound):
            raise HTTPException(status_code=404, detail="公告不存在") from exc
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    raise exc


@router.get(
    "/messages",
    responses={
        200: {
            "description": "当前用户的稳定分页消息卡片",
            "content": {"application/json": {"example": INBOX_RESPONSE_EXAMPLE}},
        }
    },
)
def list_messages(
    message_type: str | None = Query(
        default=None, alias="type", pattern=r"^(weekly_report|admin_announcement|monitoring_alert)$"
    ),
    read_value: bool | None = Query(default=None, alias="read"),
    priority: str | None = Query(default=None, pattern=r"^(normal|high|urgent)$"),
    keyword: str = Query(default="", max_length=100),
    published_from: datetime | None = Query(default=None, alias="publishedFrom"),
    published_to: datetime | None = Query(default=None, alias="publishedTo"),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_user),
):
    try:
        with PlatformSession() as session:
            return service.list_user_messages(
                session,
                identity_keys=_identity_keys(user),
                scope_identity_keys=_scope_identity_keys(user),
                message_type=message_type,
                read=read_value,
                priority=priority,
                keyword=keyword,
                published_from=published_from,
                published_to=published_to,
                cursor=cursor,
                limit=limit,
            )
    except Exception as exc:
        _raise_domain(exc)


@router.get("/messages/unread-count")
def get_unread_count(user: dict = Depends(require_user)):
    with PlatformSession() as session:
        return {
            "count": service.unread_count(
                session,
                _identity_keys(user),
                scope_identity_keys=_scope_identity_keys(user),
            )
        }


@router.get("/messages/{message_id}")
def get_message(message_id: str, user: dict = Depends(require_user)):
    try:
        with PlatformSession() as session:
            result = service.get_user_message(
                session,
                message_id,
                _identity_keys(user),
                scope_identity_keys=_scope_identity_keys(user),
                mark_read=True,
            )
            session.commit()
        _audit(user, action="读取站内消息", resource=f"message:{message_id}")
        return result
    except Exception as exc:
        _raise_domain(exc)


@router.post("/messages/{message_id}/read")
def read_message(message_id: str, user: dict = Depends(require_user)):
    try:
        with PlatformSession() as session:
            result = service.mark_message_read(
                session, message_id, _identity_keys(user)
            )
            session.commit()
        return result
    except Exception as exc:
        _raise_domain(exc)


@router.get("/announcements")
def get_announcements(
    status_value: str | None = Query(
        default=None,
        alias="status",
        pattern=r"^(draft|published|withdrawn|expired)$",
    ),
    user: dict = Depends(require_user),
):
    _require_admin(user)
    with PlatformSession() as session:
        return {"items": service.list_announcements(session, status=status_value)}


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
def create_announcement(
    payload: AnnouncementCreate, user: dict = Depends(require_user)
):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = service.create_announcement(
                session,
                title=payload.title,
                body=payload.body,
                priority=payload.priority,
                is_pinned=payload.isPinned,
                audience=payload.audience,
                created_by=user["username"],
                valid_from=payload.validFrom,
                expires_at=payload.expiresAt,
            )
            result = service.announcement_dict(session, row)
            announcement_id = row.id
            session.commit()
        _audit(
            user,
            action="创建公告草稿",
            resource=f"announcement:{announcement_id}",
        )
        return result
    except Exception as exc:
        _raise_domain(exc)


@router.patch("/announcements/{announcement_id}")
def update_announcement(
    announcement_id: str,
    payload: AnnouncementUpdate,
    user: dict = Depends(require_user),
):
    _require_admin(user)
    changes = {
        {
            "isPinned": "is_pinned",
            "validFrom": "valid_from",
            "expiresAt": "expires_at",
        }.get(key, key): value
        for key, value in payload.model_dump(exclude_unset=True).items()
    }
    try:
        with PlatformSession() as session:
            row = service.update_announcement(session, announcement_id, **changes)
            result = service.announcement_dict(session, row)
            session.commit()
        _audit(user, action="修改公告草稿", resource=f"announcement:{announcement_id}")
        return result
    except Exception as exc:
        _raise_domain(exc)


@router.post("/announcements/{announcement_id}/publish")
def publish_announcement(announcement_id: str, user: dict = Depends(require_user)):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = service.publish_announcement(
                session, announcement_id, published_by=user["username"]
            )
            result = service.announcement_dict(session, row)
            session.commit()
        _audit(user, action="发布管理员公告", resource=f"announcement:{announcement_id}")
        return result
    except Exception as exc:
        _raise_domain(exc)


@router.post("/announcements/{announcement_id}/withdraw")
def withdraw_announcement(
    announcement_id: str,
    payload: AnnouncementWithdraw,
    user: dict = Depends(require_user),
):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = service.withdraw_announcement(
                session,
                announcement_id,
                withdrawn_by=user["username"],
                reason=payload.reason,
            )
            result = service.announcement_dict(session, row)
            session.commit()
        _audit(
            user,
            action="撤回管理员公告",
            resource=f"announcement:{announcement_id}",
            detail="已记录撤回原因",
        )
        return result
    except Exception as exc:
        _raise_domain(exc)
