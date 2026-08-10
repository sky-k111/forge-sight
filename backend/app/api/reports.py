"""阶段六报告中心 API。"""

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.reports import service
from app.reports.contracts import ReportCreate, ReportPublish, ReportReviewAction, SubscriptionCreate

router = APIRouter(prefix="/api/v1", tags=["reports"])


def _raise(exc: Exception):
    if isinstance(exc, service.ReportNotFound):
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    if isinstance(exc, service.ReportConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


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
):
    return {"items": service.list_reports(owner_id=ownerId, status=statusValue, source_id=sourceId)}


@router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
def create_report(payload: ReportCreate):
    try:
        return service.create_report(payload)
    except Exception as exc:
        _raise(exc)


@router.get("/reports/{report_id}")
def get_report(report_id: str):
    try:
        return service.get_report(report_id)
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
def export_report(report_id: str, format: str = Query(default="html", pattern=r"^(html|pdf)$")):
    try:
        document = service.export_html(report_id)
    except Exception as exc:
        _raise(exc)
    # 第一版由浏览器打印为 PDF，保证中文字体与图表渲染一致。
    return HTMLResponse(
        document,
        headers={"Content-Disposition": f'inline; filename="{report_id}.html"'},
    )


@router.get("/report-subscriptions")
def list_subscriptions(ownerId: str = Query(default="local-test-user", min_length=1, max_length=64)):
    return {"items": service.list_subscriptions(ownerId)}


@router.post("/report-subscriptions", status_code=status.HTTP_201_CREATED)
def create_subscription(payload: SubscriptionCreate):
    try:
        return service.create_subscription(payload)
    except Exception as exc:
        _raise(exc)


@router.get("/report-integrations")
def report_integrations():
    return service.integration_status()

