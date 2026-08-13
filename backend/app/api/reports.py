"""阶段六报告中心 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse

from app.audit.service import write_audit
from app.auth.deps import (
    current_user_display,
    effective_stable_user_id,
    optional_user,
    require_user,
)
from app.config import settings
from app.db.platform import PlatformSession
from app.db.models import Report, ReportVersion
from app.reports import service
from app.reports.files import (
    FileAccessDenied,
    FileNotReady,
    authorize_report_view,
    resolve_download,
)
from app.reports.contracts import ReportCreate, ReportPublish, ReportReviewAction, SubscriptionCreate

router = APIRouter(prefix="/api/v1", tags=["reports"])


def _raise(exc: Exception):
    if isinstance(exc, service.ReportNotFound):
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    if isinstance(exc, service.ReportConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _authorize_report(report_id: str, user: dict) -> None:
    with PlatformSession() as session:
        report = session.get(Report, report_id)
        if report is None:
            raise service.ReportNotFound(f"报告 {report_id} 不存在")
        version = session.get(ReportVersion, report.current_version_id)
        if version is None:
            raise service.ReportNotFound(f"报告 {report_id} 不存在")
        authorize_report_view(session, report, version, user)


@router.get("/report-templates")
def list_report_templates(sourceId: int | None = Query(default=None)):
    return {"items": service.list_templates(sourceId)}


@router.get("/report-templates/{template_id}/coverage")
def template_coverage(template_id: str, sourceId: int = Query(ge=1)):
    try:
        return service.get_template_coverage(template_id, sourceId)
    except Exception as exc:
        _raise(exc)


@router.get("/reports")
def list_reports(
    ownerId: str = Query(default="local-test-user", min_length=1, max_length=64),
    statusValue: str | None = Query(default=None, alias="status"),
    sourceId: int | None = Query(default=None),
    user: dict | None = Depends(optional_user),
):
    return {
        "items": service.list_reports(
            owner_id=effective_stable_user_id(ownerId, user),
            status=statusValue,
            source_id=sourceId,
        )
    }


@router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
def create_report(payload: ReportCreate, user: dict | None = Depends(optional_user)):
    try:
        return service.create_report(
            payload.model_copy(
                update={"ownerId": effective_stable_user_id(payload.ownerId, user)}
            )
        )
    except Exception as exc:
        _raise(exc)


@router.get("/reports/{report_id}")
def get_report(report_id: str, user: dict = Depends(require_user)):
    try:
        _authorize_report(report_id, user)
        return service.get_report(report_id, session_factory=PlatformSession)
    except FileAccessDenied as exc:
        raise HTTPException(status_code=404, detail="报告不存在或无权访问") from exc
    except Exception as exc:
        _raise(exc)


@router.post("/reports/{report_id}/submit-review")
def submit_review(report_id: str, payload: ReportReviewAction):
    try:
        return service.submit_review(report_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/reports/{report_id}/approve")
def approve_report(report_id: str, payload: ReportReviewAction):
    try:
        return service.approve_report(report_id, payload.userId)
    except Exception as exc:
        _raise(exc)


@router.post("/reports/{report_id}/publish")
def publish_report(report_id: str, payload: ReportPublish):
    try:
        return service.publish_report(
            report_id,
            payload.userId,
            push_to_feishu=payload.pushToFeishu,
        )
    except Exception as exc:
        _raise(exc)


@router.post("/reports/{report_id}/deliveries/feishu")
def push_report_to_feishu(report_id: str):
    try:
        return service.deliver_to_feishu(report_id)
    except Exception as exc:
        _raise(exc)


@router.get("/reports/{report_id}/export", response_class=HTMLResponse)
def export_report(
    report_id: str,
    format: str = Query(default="html", pattern=r"^html$"),
    user: dict = Depends(require_user),
):
    try:
        _authorize_report(report_id, user)
        document = service.export_html(report_id, session_factory=PlatformSession)
    except FileAccessDenied as exc:
        raise HTTPException(status_code=404, detail="报告不存在或无权访问") from exc
    except Exception as exc:
        _raise(exc)
    # 兼容旧 HTML 预览；正式 PDF/XLSX 统一通过 report-files 下载接口提供。
    return HTMLResponse(
        document,
        headers={"Content-Disposition": f'inline; filename="{report_id}.html"'},
    )


@router.get("/report-files/{file_id}/download")
def download_report_file(file_id: str, user: dict = Depends(require_user)):
    """Download a ready report file without exposing its storage URI."""

    user_name, role = current_user_display(user)
    try:
        with PlatformSession() as session:
            download = resolve_download(session, file_id, user, settings.report_storage_dir)
    except KeyError as exc:
        write_audit(
            action="下载周报文件",
            resource=f"report-file:{file_id}",
            result="拒绝",
            detail="reason=not_found",
            user_name=user_name,
            role=role,
        )
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except FileAccessDenied as exc:
        write_audit(
            action="下载周报文件",
            resource=f"report-file:{file_id}",
            result="拒绝",
            detail="reason=access_denied",
            user_name=user_name,
            role=role,
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotReady as exc:
        write_audit(
            action="下载周报文件",
            resource=f"report-file:{file_id}",
            result="拒绝",
            detail=f"reason=not_ready;status={exc.status}",
            user_name=user_name,
            role=role,
        )
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "fileStatus": exc.status, "error": exc.error},
        ) from exc

    write_audit(
        action="下载周报文件",
        resource=f"report:{download.report_id}/file:{download.file_id}",
        result="允许",
        detail=f"version={download.version_id};format={download.path.suffix.lstrip('.')}",
        user_name=user_name,
        role=role,
    )
    return FileResponse(
        path=download.path,
        media_type=download.mime_type,
        filename=download.filename,
    )


@router.get("/report-subscriptions")
def list_subscriptions(
    ownerId: str = Query(default="local-test-user", min_length=1, max_length=64),
    user: dict | None = Depends(optional_user),
):
    return {"items": service.list_subscriptions(effective_stable_user_id(ownerId, user))}


@router.post("/report-subscriptions", status_code=status.HTTP_201_CREATED)
def create_subscription(
    payload: SubscriptionCreate, user: dict | None = Depends(optional_user)
):
    try:
        return service.create_subscription(
            payload.model_copy(
                update={"ownerId": effective_stable_user_id(payload.ownerId, user)}
            )
        )
    except Exception as exc:
        _raise(exc)


@router.get("/report-integrations")
def report_integrations():
    return service.integration_status()

