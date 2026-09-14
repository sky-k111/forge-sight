"""Deterministic utility answers that never enter the data-analysis toolchain."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.db.models import DataSource, DataTable, Tenant
from app.db.platform import PlatformSession

DEFAULT_TIMEZONE = "Asia/Shanghai"

_TIME_PATTERNS = (
    re.compile(r"(?:现在|当前)?(?:是)?几点(?:钟)?"),
    re.compile(r"(?:现在|当前)(?:的)?时间"),
    re.compile(r"今天(?:是)?(?:几号|几月几日|什么日期|星期几|周几)"),
    re.compile(r"(?:当前|现在)(?:是)?(?:几号|星期几|周几)"),
)
_SCOPE_PATTERNS = (
    re.compile(r"(?:当前|现在)(?:是|在|使用|连接)?(?:哪个|什么)?(?:企业|租户|企业空间|数据源|数据库)"),
    re.compile(r"(?:我|我们)(?:在|属于|正在使用)(?:哪个|什么)(?:企业|租户|数据源|数据库)"),
    re.compile(r"(?:我|我们)?(?:当前|现在)?(?:问|查|查询|分析)?(?:的)?数据(?:是|来自|用的|使用的)?(?:哪个|什么)(?:数据源|数据库)"),
    re.compile(r"(?:当前|现在)(?:的)?(?:分析范围|数据范围)"),
)
_FRESHNESS_PATTERNS = (
    re.compile(r"(?:数据|数据源)(?:更新|同步|覆盖|截止|到)(?:到)?(?:什么时候|哪天|哪一天|几号|哪里)"),
    re.compile(r"(?:最新|最近)(?:数据|数据日期|更新时间|同步时间)"),
    re.compile(r"数据(?:新不新|是不是最新|有多新)"),
)


def utility_kinds(text: str) -> set[str]:
    """Return the deterministic utility capabilities requested by one segment."""

    normalized = re.sub(r"\s+", "", str(text or ""))
    kinds: set[str] = set()
    if any(pattern.search(normalized) for pattern in _TIME_PATTERNS):
        kinds.add("clock")
    if any(pattern.search(normalized) for pattern in _SCOPE_PATTERNS):
        kinds.add("scope")
    if any(pattern.search(normalized) for pattern in _FRESHNESS_PATTERNS):
        kinds.add("freshness")
    return kinds


def is_system_utility_request(text: str) -> bool:
    return bool(utility_kinds(text))


def _clock_answer(now: datetime) -> str:
    weekdays = "一二三四五六日"
    return (
        f"现在是 {now:%Y年%m月%d日 %H:%M}，星期{weekdays[now.weekday()]}"
        f"（{DEFAULT_TIMEZONE}）。"
    )


def _scope_answer(source: DataSource | None, tenant: Tenant | None) -> str:
    if source is None:
        return "当前会话尚未绑定可用数据源，因此暂时没有可执行的企业数据分析范围。"
    tenant_name = tenant.name if tenant else "当前企业空间"
    access = "只读" if source.username else "受控"
    return (
        f"当前企业空间是“{tenant_name}”，正在使用数据源“{source.name}”"
        f"（{source.type.upper()} · {source.db_name} · {access}访问）。"
    )


def _freshness_answer(source: DataSource | None, latest_data: str, scanned_at: datetime | None) -> str:
    if source is None:
        return "当前会话尚未绑定数据源，无法判断数据覆盖时间。"
    parts = []
    if latest_data:
        parts.append(f"已登记业务表的数据日期最晚覆盖到 {latest_data}")
    else:
        parts.append("当前元数据中没有可确认的业务数据截止日期")
    if scanned_at:
        parts.append(f"资源目录最近扫描于 {scanned_at:%Y-%m-%d %H:%M}")
    return "；".join(parts) + "。数据截止日期和目录扫描时间含义不同，分析结果会继续披露实际查询时间范围。"


def build_system_utility_response(
    text: str,
    source_id: int | None,
    *,
    now: datetime | None = None,
) -> str:
    """Resolve utility questions from trusted clock and platform metadata only."""

    kinds = utility_kinds(text)
    if not kinds:
        return ""
    current = now or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    answers: list[str] = []
    if "clock" in kinds:
        answers.append(_clock_answer(current))

    if kinds & {"scope", "freshness"}:
        with PlatformSession() as session:
            source = session.query(DataSource).filter_by(id=source_id).first() if source_id else None
            tenant = session.query(Tenant).filter_by(id=source.tenant_id).first() if source else None
            latest_data = ""
            scanned_at = None
            if source:
                latest_data = (
                    session.query(func.max(DataTable.data_updated))
                    .filter(
                        DataTable.source_id == source.id,
                        DataTable.registered.is_(True),
                        DataTable.data_updated != "",
                    )
                    .scalar()
                    or ""
                )
                scanned_at = session.query(func.max(DataTable.scanned_at)).filter_by(
                    source_id=source.id,
                    registered=True,
                ).scalar()
        if "scope" in kinds:
            answers.append(_scope_answer(source, tenant))
        if "freshness" in kinds:
            answers.append(_freshness_answer(source, latest_data, scanned_at))
    return "\n\n".join(answers)
