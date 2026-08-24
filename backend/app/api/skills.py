"""受治理分析技能 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import effective_user_id, optional_user
from app.skills import service


router = APIRouter(prefix="/api/skills", tags=["analysis-skills"])


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except service.SkillNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except service.SkillValidation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _admin_identity(user: dict | None) -> str:
    if user and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可调整或发布分析技能")
    return effective_user_id("local-admin", user)


class SettingPayload(BaseModel):
    enabled: bool


class FeedbackPayload(BaseModel):
    runId: str
    userId: str | None = None
    rating: str
    correctionType: str = ""
    comment: str = Field(default="", max_length=2000)


@router.get("/settings")
def get_settings():
    return {**service.get_settings(), "experiment": service.experiment_summary()}


@router.patch("/settings")
def set_settings(payload: SettingPayload, user: dict | None = Depends(optional_user)):
    identity = _admin_identity(user)
    return service.update_settings(payload.enabled, updated_by=identity)


@router.get("")
def get_skills(
    status: str | None = Query(default=None),
    source_id: int | None = Query(default=None, alias="sourceId"),
    keyword: str = Query(default=""),
):
    return _call(service.list_skills, status=status, source_id=source_id, keyword=keyword)


@router.get("/{skill_id}")
def get_skill(skill_id: str):
    return _call(service.get_skill, skill_id)


@router.post("/feedback")
def submit_feedback(payload: FeedbackPayload, user: dict | None = Depends(optional_user)):
    return _call(
        service.record_feedback,
        payload.runId,
        user_id=effective_user_id(payload.userId, user),
        rating=payload.rating,
        comment=payload.comment,
        correction_type=payload.correctionType,
    )


@router.post("/{skill_id}/publish")
def publish_skill(skill_id: str, user: dict | None = Depends(optional_user)):
    return _call(service.review_skill, skill_id, action="publish", reviewer=_admin_identity(user))


@router.post("/{skill_id}/reject")
def reject_skill(skill_id: str, user: dict | None = Depends(optional_user)):
    return _call(service.review_skill, skill_id, action="reject", reviewer=_admin_identity(user))


@router.post("/{skill_id}/retire")
def retire_skill(skill_id: str, user: dict | None = Depends(optional_user)):
    return _call(service.review_skill, skill_id, action="retire", reviewer=_admin_identity(user))

