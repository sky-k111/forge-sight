"""Administrator email-delivery operations."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import NoResultFound

from app.audit.service import write_audit
from app.auth.deps import current_user_display, require_user
from app.db.platform import PlatformSession
from app.delivery.service import DELIVERY_STATUSES, list_deliveries, retry_delivery, serialize_delivery


router = APIRouter(prefix="/api/v1/deliveries", tags=["deliveries"])


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以管理邮件投递")


@router.get("")
def get_deliveries(
    message_id: str | None = Query(default=None, alias="messageId", max_length=40),
    report_id: str | None = Query(default=None, alias="reportId", max_length=40),
    announcement_id: str | None = Query(default=None, alias="announcementId", max_length=40),
    content_type: str | None = Query(default=None, alias="contentType", pattern="^(report|announcement)$"),
    recipient_user_id: str | None = Query(default=None, alias="recipientUserId", max_length=40),
    status_value: str | None = Query(default=None, alias="status", max_length=24),
    created_from: datetime | None = Query(default=None, alias="createdFrom"),
    created_to: datetime | None = Query(default=None, alias="createdTo"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_user),
):
    _require_admin(user)
    if status_value is not None and status_value not in DELIVERY_STATUSES:
        raise HTTPException(status_code=400, detail="邮件投递状态无效")
    with PlatformSession() as session:
        return {
            "items": list_deliveries(
                session,
                message_id=message_id,
                report_id=report_id,
                announcement_id=announcement_id,
                content_type=content_type,
                recipient_user_id=recipient_user_id,
                status=status_value,
                created_from=created_from,
                created_to=created_to,
                limit=limit,
            )
        }


@router.post("/{delivery_id}/retry")
def retry_email_delivery(
    delivery_id: str,
    confirm_unknown: bool = Query(default=False, alias="confirmUnknown"),
    user: dict = Depends(require_user),
):
    _require_admin(user)
    try:
        with PlatformSession() as session:
            row = retry_delivery(
                session, delivery_id, confirm_unknown=confirm_unknown
            )
            result = serialize_delivery(row)
            session.commit()
    except NoResultFound as exc:
        raise HTTPException(status_code=404, detail="邮件投递不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    user_name, role = current_user_display(user)
    write_audit(
        action="人工重发邮件",
        resource=f"delivery:{delivery_id}",
        result="已记录",
        detail="复用原投递记录进入队列",
        user_name=user_name,
        role=role,
    )
    return result
