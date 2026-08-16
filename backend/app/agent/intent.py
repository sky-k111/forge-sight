"""可审计的第一层意图规划器。

这里先用确定性规则完成问题边界识别，让复合问题在没有额外 LLM 调用时也能稳定拆解；
后续可以在同一输出契约下叠加模型规划器，但不能让模型直接决定安全边界。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agent.ids import new_id


ANALYSIS_VERBS = (
    "分析", "统计", "比较", "对比", "查找", "查询", "找出", "查看", "计算", "汇总", "排名",
    "排行", "趋势", "相关", "分布", "占比", "生成", "识别",
)
BUSINESS_TERMS = (
    "数据", "指标", "报表", "图表", "数据库", "数据表", "表格", "字段", "良率", "产量", "质量",
    "缺陷", "不良", "设备", "停机", "报警", "库存", "工序", "产线", "产品", "工单",
    "订单", "物料", "趋势", "同比", "环比", "平均", "最大", "最小", "多少",
)
SMALLTALK_PATTERNS = (
    re.compile(r"^(?:你好|您好|嗨|哈喽|hello|hi|早上好|下午好|晚上好)[！!。，,.\s]*$", re.IGNORECASE),
    re.compile(r"^(?:谢谢(?:你|啦|了)?|感谢(?:你|啦)?|辛苦了|好的|好|明白了|知道了|再见|拜拜)[！!。，,.\s]*$", re.IGNORECASE),
    re.compile(
        r"(?:你是谁|你叫什么|介绍一下你自己|你能做什么|你会什么|你有什么能力|"
        r"你能帮我什么|怎么称呼你|你的角色|你的职责|怎么使用|如何使用|可以问什么)"
    ),
    re.compile(r"(?:讲个笑话|聊聊天|今天天气|心情怎么样|你好吗)"),
    re.compile(r"(?:想.*聊|压力|心情|难过|焦虑|烦恼|陪我|表现不好)"),
)
_CLOCK_QUERY_PATTERN = re.compile(
    r"^(?:请问|麻烦问一下)?(?:现在|当前|今天)?\s*(?:几点(?:了)?|几时|时间(?:是.*)?)?[？?！!。\s]*$"
)
_REPORT_TERMS = ("周报", "月报", "专题报告", "报告", "报表")
_REPORT_REQUEST_PATTERNS = (
    # Creation verbs may be separated from the report noun by a template or
    # audience qualifier, for example "准备一份生产周报".
    re.compile(
        r"(?:生成|创建|制作|编制|撰写|产出|出|做|弄|准备|安排|提供|发送|发)"
        r".{0,12}(?:周报|月报|专题报告|报告|报表)"
    ),
    # Natural request openings cover turns such as "给我一份周报" and
    # "可以帮我做个库存周报吗" without requiring one fixed wording.
    re.compile(
        r"(?:给我|我要|需要|想要|来一份|帮我)"
        r".{0,12}(?:周报|月报|专题报告|报告|报表)"
    ),
    # Polite prefixes must be followed by an actual request verb; otherwise
    # phrases such as "请解释报告里的指标" would be mistaken for creation.
    re.compile(
        r"(?:请|能不能|可以)(?:给|帮|生成|创建|制作|编制|撰写|出|做|弄|准备|安排|提供|发送|发)"
        r".{0,12}(?:周报|月报|专题报告|报告|报表)"
    ),
)
_REPORT_ANALYSIS_ACTIONS = (
    "分析", "统计", "比较", "对比", "查找", "查询", "找出", "查看", "计算", "汇总",
    "排名", "排行", "识别",
)
OUT_OF_SCOPE_PATTERNS = (
    re.compile(r"(?:天气|下雨|下雪|气温|空气质量|穿什么衣服|带伞)"),
    re.compile(r"(?:新闻|股票|彩票|星座|电影|音乐|旅游攻略)"),
)
DEPENDENCY_PREFIXES = ("然后", "再", "并进一步", "基于此", "根据上述", "对其中", "并分析其")
OBJECT_TERMS = ("工序", "产线", "设备", "产品", "工单", "订单", "物料", "库存", "缺陷", "报警")
REFERENCE_TERMS = ("它", "其", "这个", "该对象", "上述对象", "其中对象")
VAGUE_TIME_TERMS = ("最近", "近期", "近来", "这段时间", "前阵子")
TIME_OPTIONS = (
    {"label": "最近 7 天", "value": "最近 7 天"},
    {"label": "最近 30 天", "value": "最近 30 天"},
    {"label": "本月至今", "value": "本月至今"},
)

_EXPLICIT_TIME_RE = re.compile(
    r"(?:最近|近)\s*(?:\d+|一|两|三|四|五|六|七|八|九|十|半|一个)\s*(?:天|日|周|个月|月)"
    r"|(?:本周|这周|这个月|本月|这个季度|本季度|今年|今天|昨天|上周|上月)"
    r"|20\d{2}[-年/]\d{1,2}(?:[-月/]\d{1,2})?"
)
_OBJECT_REFERENCE_RE = re.compile(
    r"(?:该对象|上述对象|其中对象|它|其(?!他|余|中)|这个(?!月|周|季度|年度|年份|星期|礼拜|天|时段))"
)

_DIMENSION_PATTERNS = {
    "产品": re.compile(r"(?:按|各|每个|不同)(?:个)?产品|产品(?:维度|分组)"),
    "产线": re.compile(r"(?:按|各|每条|不同)(?:条)?产线|产线(?:维度|分组)"),
    "工序": re.compile(r"(?:按|各|每道|不同)(?:道)?工序|工序(?:维度|分组)"),
    "设备": re.compile(r"(?:按|各|每台|不同)(?:台)?设备|设备(?:维度|分组)"),
    "缺陷": re.compile(r"(?:按|各|每种|不同)(?:种)?缺陷|缺陷(?:类型|维度|分组)"),
}


def _has_analysis_verb(text: str) -> bool:
    return any(term in text for term in ANALYSIS_VERBS)


def _is_report_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().rstrip("。.!！?？")
    if not normalized or not any(term in normalized for term in _REPORT_TERMS):
        return False
    if not any(pattern.search(normalized) for pattern in _REPORT_REQUEST_PATTERNS):
        return False
    # A request that also contains a concrete analysis action should remain an
    # analysis task; the report wording is only its requested output format.
    return not any(term in normalized for term in _REPORT_ANALYSIS_ACTIONS)


def is_report_request(text: str) -> bool:
    """Return whether a turn explicitly asks the Agent to create a report."""

    return _is_report_request(text)


def _is_known_conversation(text: str) -> bool:
    return _is_report_request(text) or bool(_CLOCK_QUERY_PATTERN.fullmatch(text)) or any(
        pattern.search(text) for pattern in (*SMALLTALK_PATTERNS, *OUT_OF_SCOPE_PATTERNS)
    )


def is_deterministic_conversation(text: str) -> bool:
    """Whether a fast, model-free ForgeSight response fully covers this turn."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return not normalized or _is_known_conversation(normalized)


def classify_request_kind(
    text: str,
    conversation_context: list[dict[str, str]] | None = None,
) -> str:
    """Classify locally; reserve the analysis worker for data-bearing turns."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return "conversation"
    # A bare report request does not contain a metric, period, or data scope.
    # Keep it in the conversation path so it can ask for the missing report
    # parameters instead of falling through to semantic retrieval and returning
    # a misleading no-data error.
    if _is_report_request(normalized):
        return "conversation"
    if _has_analysis_verb(normalized) or any(term in normalized for term in BUSINESS_TERMS):
        return "analysis"
    if _is_known_conversation(normalized):
        return "conversation"
    history = conversation_context or []
    previous_user_text = next(
        (
            str(item.get("content") or "")
            for item in reversed(history[-6:])
            if item.get("role") == "user"
        ),
        "",
    )
    previous_business_turn = bool(previous_user_text) and (
        _has_analysis_verb(previous_user_text)
        or any(term in previous_user_text for term in BUSINESS_TERMS)
    )
    references_previous_turn = bool(_OBJECT_REFERENCE_RE.search(normalized)) or any(
        marker in normalized
        for marker in (*VAGUE_TIME_TERMS, "继续", "接着", "上一个", "刚才", "前面", "昨天")
    )
    references_previous_turn = references_previous_turn or normalized.rstrip("？?!！").endswith("呢")
    if previous_business_turn and references_previous_turn:
        return "analysis"
    # Ambiguous natural language is decided by one small routing call. This keeps
    # ordinary conversation fast without silently downgrading short business asks.
    return "unknown"


def split_request_segments(question: str) -> list[str]:
    """Split mixed conversation/analysis input without breaking one business clause."""
    normalized = re.sub(r"\s+", " ", str(question or "")).strip()
    if not normalized:
        return []
    coarse = [
        part.strip(" ，,。；;？?！!")
        for part in re.split(r"[；;？?\n]+", normalized)
        if part.strip(" ，,。；;？?！!")
    ]
    segments: list[str] = []
    boundary = re.compile(
        r"[，,]\s*(?=(?:你好|您好|嗨|谢谢|感谢|请|帮我|麻烦|然后|再|另外|"
        r"分析|统计|比较|对比|查找|找出|查看|计算|汇总|生成|识别))"
    )
    for part in coarse:
        segments.extend(item.strip(" ，,") for item in boundary.split(part) if item.strip(" ，,"))
    return segments or [normalized]


def build_request_route(
    question: str,
    model_labels: dict[int, str] | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a route contract for conversation, analysis, report, or mixed input.

    ``report`` is deliberately a model-owned semantic label.  The deterministic
    classifier still handles explicit report wording, while the model router can
    use the same contract for natural requests such as “把最近一周的经营情况
    整理成一份总结”。
    """
    segments = split_request_segments(question)
    labels = model_labels or {}
    classified = []
    for index, segment in enumerate(segments):
        local_kind = classify_request_kind(segment, conversation_context)
        kind = labels.get(index, local_kind)
        if kind not in {"analysis", "conversation", "report"}:
            kind = "unknown"
        classified.append({"index": index, "text": segment, "kind": kind})

    analysis_parts = [item["text"] for item in classified if item["kind"] == "analysis"]
    conversation_parts = [item["text"] for item in classified if item["kind"] == "conversation"]
    report_parts = [item["text"] for item in classified if item["kind"] == "report"]
    unknown_parts = [item for item in classified if item["kind"] == "unknown"]
    route_kind = (
        "mixed"
        if analysis_parts and (conversation_parts or report_parts)
        else "analysis"
        if analysis_parts
        else "report"
        if report_parts
        else "conversation"
    )
    return {
        "kind": route_kind,
        "segments": classified,
        "unknownSegments": unknown_parts,
        "reportSegments": [item for item in classified if item["kind"] == "report"],
        "analysisQuestion": "；".join(analysis_parts),
        "conversationResponse": build_conversation_response(conversation_parts, mixed=bool(analysis_parts)),
    }


def build_conversation_response(parts: list[str], *, mixed: bool = False) -> str:
    if not parts:
        return ""
    combined = "；".join(parts)
    if mixed:
        if re.search(r"(?:你好|您好|嗨|hello|hi)", combined, re.IGNORECASE):
            return "你好。"
        if re.search(r"(?:谢谢|感谢|辛苦了)", combined, re.IGNORECASE):
            return "不客气。"
        return "其中的非数据问题不属于当前企业数据底座的分析范围；下面继续处理你提出的数据问题。"
    return build_smalltalk_response(combined)


def build_smalltalk_response(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if _CLOCK_QUERY_PATTERN.fullmatch(normalized):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        return f"现在是北京时间 {now:%Y 年 %m 月 %d 日 %H:%M}。"
    if _is_report_request(normalized):
        return (
            "可以生成，但还需要报告类型、统计周期和数据范围。"
            "例如可以说“生成最近 7 天的质量周报”；也可以打开“周报中心”选择模板后创建。"
        )
    if re.search(r"(?:谢谢|感谢|辛苦了)", normalized, re.IGNORECASE):
        return "不客气。你可以继续告诉我想查看的生产、质量、设备或库存问题。"
    if re.search(r"(?:你是谁|你叫什么|介绍一下你自己|怎么称呼你|你的角色|你的职责)", normalized, re.IGNORECASE):
        return (
            "我是 ForgeSight，面向制造业数据底座的智能问析 Agent。"
            "我的职责是在只读边界内理解业务问题、查询企业数据、解释指标、选择合适图表，并保留可追溯证据。"
        )
    if re.search(r"(?:你能做什么|你会什么|你有什么能力|你能帮我什么|怎么使用|如何使用|可以问什么)", normalized, re.IGNORECASE):
        return (
            "我是 ForgeSight，负责制造业数据的只读问析。你可以直接问生产、质量、设备或库存问题，"
            "例如“比较各产线最近 7 天的产量与不良率”。我会识别指标和时间范围、查询已授权数据、"
            "选择合适图表并给出带证据的结论；信息不明确时会先向你确认。"
        )
    if any(pattern.search(normalized) for pattern in OUT_OF_SCOPE_PATTERNS):
        return "这个问题不属于当前企业数据底座的分析范围。我主要支持生产、质量、设备和库存数据的只读问析。"
    if re.search(r"(?:讲个笑话|聊聊天|心情怎么样|你好吗)", normalized, re.IGNORECASE):
        return "我主要负责企业制造数据的只读问析。要不从一个业务问题开始，例如“统计各产线最近 7 天的产量趋势”？"
    if re.search(r"(?:想.*聊|压力|心情|难过|焦虑|烦恼|陪我|表现不好)", normalized, re.IGNORECASE):
        return (
            "我在，你可以慢慢说说发生了什么。如果这件事和生产、质量、设备或库存工作有关，"
            "我也能帮你把情况整理成清晰、可验证的业务问题。"
        )
    return "你好，我是 ForgeSight。你可以直接用业务语言提问，例如“找出最近一个月不良数量最高的产品”。"


def split_questions(question: str) -> list[str]:
    """拆分彼此可以独立执行的问题，保留同一指标内的比较条件。"""
    normalized = re.sub(r"\s+", " ", question.strip())
    if not normalized:
        return []

    # 编号章节优先（如报告生成的长问题 "1. …。2. …。"）：按章节整体拆分。
    # 章节内的格式约束句（“统计范围固定为…”“除非章节明确要求比较…”“请给出图表”）
    # 不是独立分析动作，拆开会导致碎片任务在语义检索阶段必然 no_match。
    numbered = [
        part.strip()
        for part in re.split(r"\s+(?=\d+\.\s)", normalized)
        if part.strip()
    ]
    numbered_parts = [part for part in numbered if re.match(r"^\d+\.\s", part)]
    if len(numbered_parts) >= 2:
        return numbered_parts

    coarse = [part.strip(" ，,。；;？?") for part in re.split(r"[；;？?\n]+", normalized) if part.strip()]
    result: list[str] = []
    for part in coarse:
        # 只有连接词后的片段本身具有新的分析动作时才拆分，避免把
        # “分析良率并与标准比较”错误拆成两个缺少主语的任务。
        pieces = re.split(r"[，,]\s*(?=(?:然后|再|并且|同时|另外|此外)?[^，,]{0,12}(?:分析|统计|比较|对比|查找|找出|查看|计算|汇总|生成|识别))", part)
        result.extend(piece.strip(" ，,") for piece in pieces if piece.strip(" ，,"))
    return result or [normalized]


def classify_analysis_type(text: str) -> str:
    if any(term in text for term in ("趋势", "变化", "走势", "折线图", "趋势图")):
        return "trend"
    if any(term in text for term in ("组合图", "双轴图", "柱线图", "热力图", "矩阵图")):
        return "comparison"
    if any(term in text for term in ("最高", "最低", "排名", "排行", "Top", "top")):
        return "ranking"
    if any(term in text for term in ("相关", "关系", "是否影响")):
        return "correlation"
    if any(term in text for term in ("对比", "比较", "差异")):
        return "comparison"
    if any(term in text for term in ("异常", "离群")):
        return "anomaly"
    # 建模意图：训练/预测类问题与普通问析分流，走确定性建模提案（含聚类分群）
    if any(term in text for term in (
        "预测", "建模", "训练模型", "机器学习", "识别高风险", "识别高不良",
        "判断是否", "是否合格", "是否达标", "聚类", "分群", "模型",
    )):
        return "modeling"
    return "query"


def _has_object_reference(text: str) -> bool:
    return bool(_OBJECT_REFERENCE_RE.search(str(text or "")))


def _requested_chart(text: str) -> str | None:
    if any(term in text for term in ("组合图", "双轴图", "柱线图", "柱线组合图")):
        return "combo"
    if any(term in text for term in ("热力图", "热度图", "矩阵图")):
        return "heatmap"
    if any(term in text for term in ("折线图", "趋势图", "曲线图")):
        return "line"
    if any(term in text for term in ("柱状图", "条形图")):
        return "bar"
    if any(term in text for term in ("饼图", "环形图")):
        return "donut"
    if "散点图" in text:
        return "scatter"
    if any(term in text for term in ("表格展示", "用表格", "以表格")):
        return "table"
    return None


def _time_constraint(text: str) -> dict[str, Any] | None:
    current_month = re.search(r"(?:这个月|本月(?:至今)?)", text)
    previous_month = re.search(r"(?:上个月|上月)", text)
    if current_month and previous_month:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        current_start = today.replace(day=1)
        previous_start = (current_start - timedelta(days=1)).replace(day=1)
        return {
            "kind": "current_vs_previous_month",
            "label": "本月至今与上月",
            "timeGrain": "month",
            "referenceDate": today.isoformat(),
            "comparisonBasis": "本月至今对比上月整月",
            "periods": [
                {
                    "key": "current",
                    "label": f"{current_start:%Y-%m}（本月至今）",
                    "startInclusive": current_start.isoformat(),
                    "endExclusive": (today + timedelta(days=1)).isoformat(),
                },
                {
                    "key": "previous",
                    "label": f"{previous_start:%Y-%m}（上月）",
                    "startInclusive": previous_start.isoformat(),
                    "endExclusive": current_start.isoformat(),
                },
            ],
        }
    if current_month:
        return {"kind": "current_month", "label": "本月至今", "timeGrain": "day"}
    if re.search(r"(?:这周|本周)", text):
        return {"kind": "current_week", "label": "本周", "timeGrain": "day"}
    if re.search(r"(?:这个季度|本季度)", text):
        return {"kind": "current_quarter", "label": "本季度", "timeGrain": "month"}
    if "今年" in text:
        return {"kind": "current_year", "label": "今年", "timeGrain": "month"}
    recent = re.search(r"(?:最近|近)\s*(\d+|一|两|三|四|五|六|七|八|九|十)\s*(天|日|周|个月|月)", text)
    if recent:
        return {
            "kind": "recent",
            "label": recent.group(0),
            "amount": recent.group(1),
            "unit": recent.group(2),
            "timeGrain": "day" if recent.group(2) in {"天", "日", "周"} else "month",
        }
    return None


def _group_dimensions(text: str) -> list[str]:
    return [name for name, pattern in _DIMENSION_PATTERNS.items() if pattern.search(text)]


def _object_candidates(texts: list[str]) -> list[str]:
    """Return recent distinct business object types, newest mention first."""
    candidates: list[str] = []
    for text in reversed(texts):
        for term in OBJECT_TERMS:
            if term in text and term not in candidates:
                candidates.append(term)
    return candidates


def _replace_reference(text: str, replacement: str) -> str:
    return _OBJECT_REFERENCE_RE.sub(replacement, text)


def _replace_vague_time(text: str, replacement: str) -> str:
    resolved = text
    for term in sorted(VAGUE_TIME_TERMS, key=len, reverse=True):
        resolved = resolved.replace(term, replacement)
    return resolved


def _clarification_fields(
    question: str,
    parts: list[str],
    conversation_context: list[dict[str, str]],
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    history_texts = [
        str(item.get("content") or "")
        for item in conversation_context[-6:]
        if item.get("role") == "user"
    ]
    current_has_reference = _has_object_reference(question)
    # A dependency inside one compound question has a deterministic antecedent: the
    # immediately preceding task. Cross-turn pronouns are clarified when history
    # contains zero or multiple possible object types.
    internal_dependency = len(parts) > 1 and any(
        _has_object_reference(part) for part in parts[1:]
    )
    candidates = _object_candidates(history_texts)
    if current_has_reference and not internal_dependency and len(candidates) != 1:
        fields.append(
            {
                "id": "object",
                "label": "分析对象",
                "question": "问题中的“它/其”具体指哪个对象？",
                "options": [{"label": item, "value": item} for item in candidates[:5]],
                "allowCustom": True,
            }
        )
    vague_time = any(term in question for term in VAGUE_TIME_TERMS) and not _EXPLICIT_TIME_RE.search(question)
    if vague_time:
        fields.append(
            {
                "id": "timeRange",
                "label": "时间范围",
                "question": "“最近”需要按哪个时间范围统计？",
                "options": list(TIME_OPTIONS),
                "allowCustom": True,
            }
        )
    return fields


def build_intent_plan(
    question: str,
    conversation_context: list[dict[str, str]] | None = None,
    clarification: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build tasks plus a merged clarification request for unresolved references."""
    history = conversation_context or []
    answers = clarification or {}
    resolved_question = question.strip()
    if answers.get("object"):
        resolved_question = _replace_reference(resolved_question, answers["object"])
    if answers.get("timeRange"):
        resolved_question = _replace_vague_time(resolved_question, answers["timeRange"])

    parts = split_questions(resolved_question)
    fields = _clarification_fields(resolved_question, parts, history)
    fields = [field for field in fields if field["id"] not in answers]
    if fields:
        return {
            "tasks": [],
            "clarification": {
                "kind": "clarification",
                "question": f"为了准确执行，请确认以下 {len(fields)} 项信息。",
                "fields": fields,
                "allowCustom": True,
                "resumeNode": "intent.parse",
            },
        }

    multi = len(parts) > 1
    tasks = []
    history_candidates = _object_candidates(
        [str(item.get("content") or "") for item in history[-6:] if item.get("role") == "user"]
    )
    for index, text in enumerate(parts):
        has_reference = _has_object_reference(text)
        dependent = index > 0 and (
            text.startswith(DEPENDENCY_PREFIXES)
            or has_reference
            or any(word in text for word in ("上述", "其中"))
        )
        normalized = text
        if has_reference:
            if dependent:
                normalized = _replace_reference(text, "上一步结果中的对象")
            elif len(history_candidates) == 1:
                normalized = _replace_reference(text, history_candidates[0])
        tasks.append(
            {
                "id": new_id("task"),
                "order": index + 1,
                "title": text[:80],
                "normalizedQuestion": normalized,
                "originalQuestion": text,
                "mode": "dependent" if dependent else ("parallel" if multi else "single"),
                "analysisType": classify_analysis_type(normalized),
                "requestedChart": _requested_chart(normalized),
                "timeConstraint": _time_constraint(normalized),
                "groupBy": _group_dimensions(normalized),
                "dependsOn": [tasks[index - 1]["id"]] if dependent else [],
            }
        )
    return {"tasks": tasks, "clarification": None}


def plan_questions(question: str) -> list[dict]:
    """Backward-compatible task-only helper used by lightweight callers/tests."""
    return build_intent_plan(question)["tasks"]
