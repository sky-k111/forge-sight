"""Administrator operational metrics endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import require_user
from app.db.platform import PlatformSession
from app.operations import operational_snapshot

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/metrics")
def get_operational_metrics(user: dict = Depends(require_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以查看运行指标")
    with PlatformSession() as session:
        return operational_snapshot(session)
