"""周报领域状态与跨服务共享的不变量。"""

from enum import StrEnum


class ReportStatus(StrEnum):
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    CHECKING_DATA = "checking_data"
    PLANNING = "planning"
    RUNNING_SECTIONS = "running_sections"
    VALIDATING = "validating"
    COMPOSING = "composing"
    PUBLISHED = "published"
    DATA_INSUFFICIENT = "data_insufficient"
    PARTIAL = "partial"
    VALIDATION_FAILED = "validation_failed"
    GENERATION_FAILED = "generation_failed"


class ReportFileStatus(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class ReportVersionStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPOSING = "composing"
    PUBLISHED = "published"
    PARTIAL = "partial"
    VALIDATION_FAILED = "validation_failed"
    GENERATION_FAILED = "generation_failed"


class ReportSectionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class MessageStatus(StrEnum):
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class AnnouncementStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"


class InvalidReportTransition(ValueError):
    """报告状态转换不属于已定义工作流。"""


class PublishedVersionImmutable(ValueError):
    """调用方尝试修改已发布的版本。"""


_REPORT_TRANSITIONS: dict[str, frozenset[str]] = {
    "scheduled": frozenset({"queued"}),
    "queued": frozenset({"checking_data", "generation_failed"}),
    "checking_data": frozenset({"planning", "data_insufficient", "generation_failed"}),
    "planning": frozenset({"running_sections", "generation_failed"}),
    "running_sections": frozenset({"validating", "partial", "generation_failed"}),
    "validating": frozenset({"composing", "validation_failed", "partial"}),
    "composing": frozenset({"published", "generation_failed"}),
    "data_insufficient": frozenset({"queued"}),
    "partial": frozenset({"queued", "validating"}),
    "validation_failed": frozenset({"queued", "validating"}),
    "generation_failed": frozenset({"queued"}),
    "published": frozenset(),
}


def transition_report_status(current: str, target: str) -> str:
    """验证新链路状态转换并返回目标状态。"""

    if target not in _REPORT_TRANSITIONS.get(current, frozenset()):
        raise InvalidReportTransition(f"报告状态不能从 {current} 变更为 {target}")
    return target


def ensure_version_mutable(status: str) -> None:
    """在写入版本正文、快照或校验信息前执行不可变检查。"""

    if status == ReportStatus.PUBLISHED:
        raise PublishedVersionImmutable("已发布报告版本不可修改")
