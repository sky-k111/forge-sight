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
from app.agent.system_utility import is_system_utility_request

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

# 弱量化词不单独触发数据分析：“多少/平均/最大/最小”常出现在通用句中，
# 必须配合真实业务实体（产量/良率/产线等）才说明是数据查询。否则“1+1等于多少”会被误判为分析。
_WEAK_BUSINESS_TERMS = ("多少", "平均", "最大", "最小")
_STRONG_BUSINESS_TERMS = tuple(t for t in BUSINESS_TERMS if t not in _WEAK_BUSINESS_TERMS)

# 真实业务实体/指标词：只有命中这些才说明用户在查询制造数据（而非常识/闲聊/概念解释）。
# 优先级最高；OEE 等英文词用 lower() 比较。
_STRONG_ENTITY_TERMS = (
    "良率", "产量", "不良", "缺陷", "库存", "设备", "停机", "报警", "工序", "产线",
    "产品", "工单", "订单", "物料", "合格", "产能", "产值", "交付", "排产", "班次",
    "车间", "报废", "返工", "合格率", "不良率", "缺陷率", "停机时长", "产能利用率", "oee",
    "生产", "制造", "维修", "故障", "品质", "销售", "卖出", "出货", "出库", "发货",
    "营收", "退货", "签收", "报废率",
)
# 这些词几乎只出现在数据分析语境里，单独出现即可判定为分析请求。
_STRONG_SIGNAL_TERMS = ("趋势", "走势", "同比", "环比", "分布", "占比", "构成", "排名", "排行", "波动", "变化")
# 数据指代词：较弱，需搭配 时间/维度/图表/查询动作 才说明是查询。
_GENERIC_DATA_WORDS = ("数据", "指标", "报表", "图表", "表格", "字段", "数据库", "数量", "占比", "构成")
# 真正的“取数/出图”动作，而非泛泛而谈。
_QUERY_VERBS = (
    "分析", "统计", "查询", "查看", "计算", "汇总", "查找", "找出",
    "对比", "比较", "展示", "看看", "看下", "出图", "画",
)
_DATA_SIGNAL_DIMS = ("工序", "产线", "设备", "产品", "工单", "订单", "物料", "库存", "缺陷", "不良", "报警", "车间", "班次")
_DATA_SIGNAL_CHARTS = ("柱状图", "折线图", "热力图", "饼图", "散点图", "组合图", "双轴图", "趋势图", "占比图", "图表")

# 生产语境兜底：用于「无明确业务实体词、但明显在问生产数量/线体/工位/工序」的问句
# （如“每条线做了几件”“这周各车间多少件”）。它只作安全红线，让这类问句不被降级成闲聊，
# 语义仍交给 LLM 判定。刻意用“每/各 + 产线/线/机台...”这类限定，避免“这衣服几件”之类误触发。
_MFG_CONTEXT_RE = re.compile(
    r"(?:每|各)\s*[\u4e00-\u9fa5]{0,3}(?:产线|线|机台|机|设备|工序|产品|工单|订单|物料|班次|车间)"
    r"|(?:几|多少)(?:件|台|单|批|道|个)(?:产品|工单|订单|物料|良品|不良)(?:的)?(?:产量|生产|合格|不良)?"
    r"|(?:哪?条线|哪个车间|哪道工序|几号机)(?:的|呢)?"
)

def _has_business_analysis_signal(text: str) -> bool:
    """是否为「真正的业务数据查询」，而不是常识/概念/闲聊。

    一个问句要进入分析工作流，必须命中真实业务实体（产量/良率/产线/设备/库存…），
    或是「数据/指标 + 时间/维度/图表/取数动作」的组合。泛泛的 分析/统计/比较/多少
    单独出现不构成分析（否则“5月有多少天”“比较苹果安卓哪个好”会被误判成查库）。
    """
    low = str(text or "").strip().lower()
    if not low:
        return False
    has_entity = any(term in low for term in _STRONG_ENTITY_TERMS)
    has_dim = any(term in low for term in _DATA_SIGNAL_DIMS)
    has_chart = any(term in low for term in _DATA_SIGNAL_CHARTS)
    has_data_word = any(term in low for term in _GENERIC_DATA_WORDS)
    has_query = any(term in low for term in _QUERY_VERBS)
    has_time = bool(_EXPLICIT_TIME_RE.search(low))
    if has_entity or has_dim:
        return True
    if any(term in low for term in _STRONG_SIGNAL_TERMS):
        return True
    if has_data_word and (has_time or has_chart or has_dim or has_query):
        return True
    if has_chart and (has_entity or has_data_word or has_dim or has_query or has_time):
        return True
    return False


def _has_manufacturing_context(text: str) -> bool:
    """生产语境兜底：无明确业务实体词、但明显在问生产数量/线体/工位/工序。

    只用于安全兜底（防止把这类问句降级成闲聊），不参与语义理解；语义仍交给 LLM。
    """
    return bool(_MFG_CONTEXT_RE.search(str(text or "").lower()))


def _has_data_signal(text: str) -> bool:
    """兼容旧名：弱信号兜底等价于业务分析信号。"""
    return _has_business_analysis_signal(text)

GENERAL_KNOWLEDGE_PATTERNS = (
    re.compile(r"\d+\s*[+\-*/\u00d7\u00f7^]\s*\d+"),
    re.compile(r"(?:你知道(?:吗)?|请问|问一下|我想知道|告诉(?:我)?)\s*.{0,40}$"),
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
    if _has_business_analysis_signal(text):
        return False
    return _is_report_request(text) or any(
        pattern.search(text)
        for pattern in (*SMALLTALK_PATTERNS, *OUT_OF_SCOPE_PATTERNS, *GENERAL_KNOWLEDGE_PATTERNS)
    )


def is_deterministic_conversation(text: str, *, intent_enabled: bool = False) -> bool:
    """Whether a fast, model-free ForgeSight response fully covers this turn."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return True
    if _has_business_analysis_signal(normalized):
        return False
    return _is_known_conversation(normalized) or _is_pure_system_utility(normalized)


def _is_pure_system_utility(text: str) -> bool:
    """Keep utility lookup deterministic without swallowing a real analysis ask."""

    if not is_system_utility_request(text) or _has_business_analysis_signal(text):
        return False
    return True




_DEFINITION_LEAD_RE = re.compile(r"(?:什么是|是什么|什么叫|是什么意思|怎么理解|介绍一下|解释(?:一下)?(?:什么|什么是|这)?)")
_DEFINITION_NEGATIVE_RE = re.compile(
    r"(?:为什么|原因|根因|下钻|对比|比较|差异|波动|趋势|分布|排名|占多少|是多少|多少|变化|影响|情况|"
    r"最近|今年|这个月|本月|本周|今天|昨天|上周|去年|上月|近\s*\d+)"
)


def _is_concept_explanation(text: str) -> bool:
    """概念/定义解释（什么是OEE、什么是工序良率、解释一下良率）→ 交 LLM 解释，不查库。

    但若带因果/对比/趋势/取数/时间聚合语境（“解释一下为什么产量低”“今年产量多少”），
    则不是纯定义，放行到分析工作流。
    """
    t = str(text or "").strip()
    if not _DEFINITION_LEAD_RE.search(t):
        return False
    if _DEFINITION_NEGATIVE_RE.search(t):
        return False
    return True


def _references_previous_turn(text: str) -> bool:
    """是否引用/追问上一轮结果（代词/时间/继续/呢）。"""
    t = str(text or "").strip()
    if bool(_OBJECT_REFERENCE_RE.search(t)):
        return True
    if any(marker in t for marker in (*VAGUE_TIME_TERMS, "继续", "接着", "上一个", "刚才", "前面", "昨天")):
        return True
    return bool(t.rstrip("？?!！").endswith("呢"))


def _is_general_knowledge(text: str) -> bool:
    """常识/数学/范围外问题：无业务实体与分析动作，应走对话而非数据查询。
    但若含真实业务实体（产量/良率/产线等），即使句式象“你知道…吗”，也不得被当成对话吞掉。"""
    if _has_business_analysis_signal(text):
        return False
    return any(pattern.search(text) for pattern in (*GENERAL_KNOWLEDGE_PATTERNS, *OUT_OF_SCOPE_PATTERNS, *SMALLTALK_PATTERNS))


def classify_request_kind(
    text: str,
    conversation_context: list[dict[str, str]] | None = None,
    *,
    intent_enabled: bool = False,
) -> str:
    """Classify locally; reserve the analysis worker for data-bearing turns."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return "conversation"
    if _is_pure_system_utility(normalized):
        return "utility"
    # 概念/定义解释：不查库，交给 LLM 对话解释。
    if _is_concept_explanation(normalized):
        return "conversation"
    # A bare report request does not contain a metric, period, or data scope.
    # Keep it in the conversation path so it can ask for the missing report
    # parameters instead of falling through to semantic retrieval and returning
    # a misleading no-data error.
    if _is_report_request(normalized):
        return "conversation"
    if _is_general_knowledge(normalized):
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
        or _has_business_analysis_signal(previous_user_text)
    )
    # 真正业务数据查询：命中业务实体或「数据指代 + 时间/维度/图表/取数动作」。
    if _has_business_analysis_signal(normalized):
        return "analysis"
    # 仅“换一种图”的追问：上一轮是业务问题、本轮只点名图表类型（柱状图/折线图/热力图…），
    # 视为对上一轮结果的展示重述，进入分析工作流并继承上一轮的维度/时间/指标。
    if (
        _requested_chart(normalized) is not None
        and _is_chart_only_directive(normalized)
        and previous_business_turn
    ):
        return "analysis"
    if _is_known_conversation(normalized):
        return "conversation"
    if previous_business_turn and _references_previous_turn(normalized):
        return "analysis"
    # Ambiguous natural language is decided by one small routing call. This keeps
    # ordinary conversation fast without silently downgrading short business asks.
    return "unknown"


def should_downgrade_to_conversation(
    question: str,
    kind: str,
    *,
    previous_business: bool = False,
) -> bool:
    """安全网：LLM/正则把明显非业务问句判成分析时，降级为对话，避免跑空 SQL。

    只在存在「明确非业务信号」时降级：概念/定义、常识、闲聊、范围外、数学。若一个问句
    只是模糊（例如“上周每条线做了几件”），没有业务实体但也没有明确非业务信号，仍交给
    LLM 路由/分析工作流去判定，而不是武断降级吞掉潜在业务问题。
    """
    if kind not in {"analysis", "modeling", "investigation", "report"}:
        return False
    if _has_business_analysis_signal(question):
        return False
    if _has_manufacturing_context(question):
        return False
    if previous_business and _references_previous_turn(question):
        return False
    return True


# 写意图词：命中即说明想改动/删除数据。分析工作流里的 intent_guard 会据此阻断，变成只读安全提示。
_WRITE_TERMS = (
    "删除", "删掉", "删去", "删了", "清除", "清空", "清零", "重置", "覆盖", "抹掉", "作废",
    "修改", "改成", "改为", "改写", "调整", "设置成", "写入", "插入", "新增记录", "新增一条", "建表", "删表",
    "更新数据", "更新指标", "更新表", "更新库", "更新字段",
    "delete ", "update ", "insert ", "drop ", "alter ", "truncate ", "create table",
)


def has_write_intent(text: str) -> bool:
    """是否包含明确的写/删意图（如“删除某记录”“更新指标”）。"""
    low = str(text or "").lower()
    return any(term in low for term in _WRITE_TERMS)


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
        if kind not in {"analysis", "conversation", "report", "utility"}:
            kind = "unknown"
        classified.append({"index": index, "text": segment, "kind": kind})

    analysis_parts = [item["text"] for item in classified if item["kind"] == "analysis"]
    conversation_parts = [item["text"] for item in classified if item["kind"] == "conversation"]
    report_parts = [item["text"] for item in classified if item["kind"] == "report"]
    utility_parts = [item["text"] for item in classified if item["kind"] == "utility"]
    unknown_parts = [item for item in classified if item["kind"] == "unknown"]
    route_kind = (
        "mixed"
        if analysis_parts and (conversation_parts or report_parts or utility_parts)
        else "analysis"
        if analysis_parts
        else "report"
        if report_parts
        else "utility"
        if utility_parts
        else "conversation"
    )
    return {
        "kind": route_kind,
        "segments": classified,
        "unknownSegments": unknown_parts,
        "reportSegments": [item for item in classified if item["kind"] == "report"],
        "utilitySegments": [item for item in classified if item["kind"] == "utility"],
        "utilityQuestion": "；".join(utility_parts),
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
    if _is_report_request(normalized):
        return (
            "可以生成，但还需要报告类型、统计周期和数据范围。"
            "例如可以说“生成最近 7 天的质量周报”；也可以打开“周报中心”选择模板后创建。"
        )

    math_match = re.search(r"(\d+(?:\.\d+)?)\s*([+\-*/\u00d7\u00f7^])\s*(\d+(?:\.\d+)?)", normalized)
    if math_match and not any(t in normalized for t in _STRONG_BUSINESS_TERMS):
        a = float(math_match.group(1)); op = math_match.group(2); b = float(math_match.group(3))
        val = None
        if op in ("+",):
            val = a + b
        elif op in ("-",):
            val = a - b
        elif op in ("*", "\u00d7"):
            val = a * b
        elif op in ("/", "\u00f7"):
            val = a / b if b else None
        elif op == "^":
            val = a ** b
        if val is not None:
            return f"该算式的结果是 {val:g}。" if float(val).is_integer() else f"该算式的结果约是 {val:.4f}。"
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
    if any(pattern.search(normalized) for pattern in GENERAL_KNOWLEDGE_PATTERNS):
        return "这属于通用常识类问题，不属于当前企业数据底座的分析范围。我主要负责生产、质量、设备和库存数据的只读问析；如果你有这些业务数据问题，可以直接问我。"
    return "你好，我是 ForgeSight。你可以直接用业务语言提问，例如“找出最近一个月不良数量最高的产品”。"


_CHART_VIEW_TERMS = {
    "bar": ("柱状图", "条形图"),
    "line": ("折线图", "趋势图", "曲线图"),
    "donut": ("饼图", "环形图"),
    "scatter": ("散点图",),
    "heatmap": ("热力图", "热度图", "矩阵图"),
}
_CHART_VIEW_CANONICAL = {
    "bar": "柱状图",
    "line": "折线图",
    "donut": "饼图",
    "scatter": "散点图",
    "heatmap": "热力图",
}


def _has_query_core(core: str) -> bool:
    """判断切前的短语是否包含真正的查询主体（分析动作或业务指标词）。"""
    return any(t in core for t in ANALYSIS_VERBS) or any(t in core for t in BUSINESS_TERMS)


def _multi_chart_views(text: str) -> list[str] | None:
    """当同一子问题同时点名两种图表（如“柱状图以及趋势 折线图”）时，
    把它展开成两个各自携带完整查询主体与单一图表的子问题，让前端能同时给出
    柱状图与折线图两种视图，而不是把后一个图表条件吞掉。

    仅在未使用“组合图/双轴图/柱线图”这类一次成型复合类型、且非多指标对比时才展开，
    避免把“柱状图+折线图对比产量与良率”这种需要双轴对照的请求错误拆散。
    """
    present = [
        view for view, terms in _CHART_VIEW_TERMS.items()
        if any(t in text for t in terms)
    ]
    if len(present) < 2:
        return None
    if any(t in text for t in ("组合图", "双轴图", "柱线图", "柱线组合图")):
        return None
    if any(term in text for term in ("对比", "比较", "差异")):
        return None

    positions = [
        (text.find(term), view)
        for view in present
        for term in _CHART_VIEW_TERMS[view]
        if term in text
    ]
    if not positions:
        return None
    first_pos = min(pos for pos, _ in positions)
    core = text[:first_pos].strip(" ，,、和与")
    if core and _has_query_core(core):
        return [f"{core} {_CHART_VIEW_CANONICAL[view]}".strip() for view in present]

    # 图表词在句首/中间：去掉其他图表词，保留目标图表词与查询主体（同时清理干掉的连接词）。
    out = []
    for keep in present:
        q = text
        for other in present:
            if other == keep:
                continue
            for term in _CHART_VIEW_TERMS[other]:
                q = q.replace(term, "")
        q = re.sub(r"\s+", " ", q).strip().strip(" ，,、和与")
        if q:
            out.append(q)
    return out if len(out) >= 2 else None


_METRIC_JOIN_RE = re.compile(r"以及|和|与|及")
# 指标计量词：只有片段以这些词结尾，且右侧紧跟另一个指标词，才判定为「并列两个指标」。
_METRIC_SUFFIX_RE = re.compile(r"(?:率|量|数|金额|时长|成本|库存|产值|价格|薪资|占比|额度|次数|总额)$")
_METRIC_LEAD_RE = re.compile(
    r"^(?:不良率|良率|返工率|合格率|抽检不良率|停机时长|产量|合格数量|不良数量|"
    r"合格数|不良数|库存|产值|成本|价格|薪资|占比|数量|总额|时长|金额)"
)
_METRIC_LEAD_VERB_RE = re.compile(r"^(?:比较|对比|分析|计算|统计|生成|识别|查找|查看|排序|排名|汇总|下钻|拆|趋势)")
_METRIC_KEYWORD_RE = re.compile(r"不良率|良率|返工率|合格率|抽检不良率|产量|合格数量|不良数量|库存|产值|成本|价格|薪资|占比|停机时长|金额|时长|数量|总额")


def split_metric_compound(text: str) -> list[str]:
    """把「A指标和B指标 / A以及B」这类并列多个计量指标的问题拆成可独立执行的子问题。

    仅在连词两侧都确实是「指标计量短语」时才拆分，避免把「良率并与标准比较」这类
    「同一指标的补充条件」误拆成两个任务。也避免把「按产线和工序」这类并列维度拆开。
    """
    text = str(text or "").strip()
    if not text:
        return [""]
    cuts: list[tuple[int, int]] = []
    for m in _METRIC_JOIN_RE.finditer(text):
        left = text[: m.start()].strip()
        right = text[m.end():].strip()
        if not left or not right:
            continue
        # 左侧结尾必须落在某个指标计量词上，且左侧确实含指标词（避免「按产线和工序」被拆）。
        if not _METRIC_SUFFIX_RE.search(left):
            continue
        if not _METRIC_KEYWORD_RE.search(left):
            continue
        # 右侧必须紧跟一个指标词，而不是「比较/下钻」这类对同指标的补充动词。
        if _METRIC_LEAD_VERB_RE.match(right):
            continue
        if not _METRIC_LEAD_RE.match(right):
            continue
        cuts.append((m.start(), m.end()))
    if not cuts:
        return [text]
    pieces: list[str] = []
    prev = 0
    for start, end in cuts:
        pieces.append(text[prev:start].strip())
        prev = end
    pieces.append(text[prev:].strip())
    return [p for p in pieces if p]


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
        pieces = re.split(r"[，,]\s*(?=(?:然后|再|并且|同时|另外|此外)?[^，,]{0,12}(?:分析|统计|比较|对比|查找|找出|查看|计算|汇总|生成|识别|占比|构成|份额))", part)
        result.extend(piece.strip(" ，,") for piece in pieces if piece.strip(" ，,"))
    metric_split: list[str] = []
    for piece in result:
        metric_split.extend(split_metric_compound(piece))
    expanded: list[str] = []
    for piece in metric_split:
        views = _multi_chart_views(piece)
        expanded.extend(views if views else [piece])
    return expanded or [normalized]


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
    if any(term in text for term in ("占比", "构成", "份额", "比例", "占多少", "占比图")):
        return "share"
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
    # 用户同时点名柱状图与折线图（包括“柱状图以及趋势 折线图”）：应输出柱+线组合，而非只输出后一个图表类型。
    if any(t in text for t in ("柱状图", "条形图")) and any(t in text for t in ("折线图", "趋势图", "曲线图")):
        return "combo"
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


def _is_chart_only_directive(text: str) -> bool:
    """当前句是否只是“换一种图”这类纯图表指令，而不带独立的分析动作/业务实体。

    例如“用柱状图展示”“换成折线图”“改成热力图”都属此类；而“分析6月产线良率用柱状图”
    带完整分析主体，应走完整查询而不是简单继承上一轮。
    """
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if _has_analysis_verb(normalized):
        return False
    return not any(term in normalized for term in _STRONG_BUSINESS_TERMS)


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
    if re.search(r"(?:上周|上一周)", text):
        return {"kind": "previous_week", "label": "上周", "timeGrain": "day", "reference": "data_horizon"}
    if re.search(r"(?:上季度|上个季度)", text):
        return {"kind": "previous_quarter", "label": "上季度", "timeGrain": "month", "reference": "data_horizon"}
    if re.search(r"(?:去年|上年)", text):
        return {"kind": "previous_year", "label": "去年", "timeGrain": "month", "reference": "data_horizon"}
    if re.search(r"(?:上个月|上月)", text):
        return {"kind": "previous_month", "label": "上个月", "timeGrain": "month", "reference": "data_horizon"}
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
    # 裸“6月份/6月”这类不带年份的指定月份，需要锚定到数据地平线来确定年份，
    # 避免 SQL 生成阶段凭空猜测（例如把 2026 年 6 月猜成 2025 年 6 月导致空结果）。
    month_ref = re.search(r"(?<![上本这每近下哪前])(\d{1,2})\s*月(?:份)?", text)
    if month_ref:
        month = int(month_ref.group(1))
        if 1 <= month <= 12:
            return {
                "kind": "specific_month",
                "month": month,
                "label": f"{month}月",
                "timeGrain": "day",
                "reference": "data_horizon",
            }
    # 裸年份（如“2020年总产量”“2026年各月”）：锚定数据地平线，超范围时由查询层返回无数据。
    if re.search(r"(?<!\d)(?:20\d{2}|19\d{2})\s*年", text):
        y = int(re.search(r"(?<!\d)(\d{4})\s*年", text).group(1))
        if 1900 <= y <= 2100:
            return {
                "kind": "specific_year",
                "year": y,
                "label": f"{y}年",
                "timeGrain": "year",
                "reference": "data_horizon",
            }
    return None


# 显式“时间拆分”描述：各月份/按月/每个月/逐月/每周/季度/每天 等。
# 与时间范围(本月/去年)不同，这里表示“把横轴按时间粒度拆开”。仅当明确要求拆开时才命中，
# 避免把“今年总产量/本月合计”这类单一聚合误判为按月分组。
_TIME_BREAKDOWN_PATTERNS = {
    "月份": re.compile(
        r"(?:各月份|各个月|各月|按月|按月份|每个月|逐月|每月|月度|按月展示|按月份展示)"
    ),
    "周": re.compile(r"(?:各周|每周|逐周|按周|各星期|每个星期|按星期)"),
    "季度": re.compile(r"(?:各季度|每季度|逐季度|按季度|各季|逐季|按季|每个季度)"),
    "日": re.compile(r"(?:每天|每日|逐日|按天|按日|每天|每日)"),
}


def _time_breakdown_dimensions(text: str) -> list[str]:
    """返回问题里显式要求的时间拆分维度（月份/周/季度/日）。"""
    return [name for name, pattern in _TIME_BREAKDOWN_PATTERNS.items() if pattern.search(text)]


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



# ---------------------------------------------------------------------------
# 跨轮次上下文继承：当本轮是一个“追问/视角切换/继续下钻”指令且缺少维度、时间
# 或图表类型时，从上一轮任务复用这些槽位，避免退化成裸查询（例如“改成排名”
# 丢掉了上文的“产线/本周/产量”）。
# ---------------------------------------------------------------------------
_TURN_SWITCH_TERMS = (
    "改成", "换成", "转为", "改为", "换种", "换个", "看下排名", "排名那种",
    "占比", "构成", "份额", "比例", "再下钻", "进一步看", "接着看", "继续看",
    "那差在哪", "哪个工序", "哪条产线", "哪个设备", "哪台", "哪个批次",
    "下钻", "拆开", "按什么维度", "再看", "还有呢", "那具体", "那到底",
)


def _is_turn_switch(question: str) -> bool:
    """当前句是否是对上一轮结果的追问/补动作（而非全新独立问题）。"""
    text = re.sub(r"\s+", " ", str(question or "")).strip()
    if not text:
        return False
    if any(term in text for term in _TURN_SWITCH_TERMS):
        return True
    # 仅点名图表类型的指令（“用柱状图展示”“换成折线图”）是对上一轮结果的展示重述，
    # 新视角 = 换一种图，维度/时间/指标都沿用上一轮。
    if _requested_chart(text) is not None and _is_chart_only_directive(text):
        return True
    # 指代词 / 承前省略
    if _OBJECT_REFERENCE_RE.search(text):
        return True
    if text.rstrip("？?。！!").endswith("呢"):
        return True
    return False


def _inherit_previous_task(
    task: dict[str, Any],
    previous: dict[str, Any] | None,
    question: str,
) -> dict[str, Any]:
    """把上一轮任务的 groupBy / timeConstraint / requestedChart 继承到当前任务。"""
    if not previous:
        return task
    inherited = dict(task)
    prev_group = list(previous.get("groupBy") or [])
    prev_time = previous.get("timeConstraint")
    prev_chart = previous.get("requestedChart")

    # 1) 维度继承：本轮没显式指定产线/工序/产品… 且是追问时继承上轮。
    if not inherited.get("groupBy") and prev_group and _is_turn_switch(question):
        inherited["groupBy"] = list(dict.fromkeys(prev_group))
    # 2) 时间继承：本轮没有时间约束且是追问时继承上轮（如“那这个月总的”）。
    if not inherited.get("timeConstraint") and prev_time and _is_turn_switch(question):
        inherited["timeConstraint"] = prev_time
    # 3) 图表视角继承：本轮没点名图表类型时沿用上轮；若切换指令点名了则用本轮的。
    if not inherited.get("requestedChart") and prev_chart and _is_turn_switch(question):
        inherited["requestedChart"] = prev_chart
    return inherited

def build_intent_plan(
    question: str,
    conversation_context: list[dict[str, str]] | None = None,
    clarification: dict[str, str] | None = None,
    previous_task: dict[str, Any] | None = None,
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
            or text.startswith(("同时", "并且", "以及", "另外", "此外", "还要", "并"))
        )
        normalized = text
        if has_reference:
            if dependent:
                normalized = _replace_reference(text, "上一步结果中的对象")
            elif len(history_candidates) == 1:
                normalized = _replace_reference(text, history_candidates[0])
        # 跨轮继承：追问/视角切换时复用上一任务槽位，避免退化成裸查询。
        built_task = {
            "id": new_id("task"),
            "order": index + 1,
            "title": text[:80],
            "normalizedQuestion": normalized,
            "originalQuestion": text,
            "mode": "dependent" if dependent else ("parallel" if multi else "single"),
            "analysisType": classify_analysis_type(normalized),
            "requestedChart": _requested_chart(normalized),
            "timeConstraint": _time_constraint(normalized),
            "groupBy": list(dict.fromkeys(_group_dimensions(normalized) + _time_breakdown_dimensions(normalized))),
            "dependsOn": [tasks[index - 1]["id"]] if dependent else [],
        }
        built_task = _inherit_previous_task(built_task, previous_task, text)
        tasks.append(built_task)

    # Inherit shared context (dimensions / time / chart) from the primary
    # sub-question into any downstream comparative sub-question, so compound
    # asks like "last month per-line pass rate, also compare with defect rate"
    # keep the same dimension & time basis instead of degrading to a bare query.
    if multi and len(tasks) > 1:
        primary = tasks[0]
        # 复合问题（如「6月各周良率和不良率」「产量以及良率」）拆出的后续子问题通常是
        # 并行任务，自身可能只剩裸指标词（如「不良率」）而缺失时间/维度。这里把它们补齐为
        # 主干的时间与维度，避免退化成整段聚合标量。对比型任务自行管理周期，不继承。
        for task in tasks[1:]:
            if task.get("analysisType") == "comparison" or task.get("mode") != "parallel":
                continue
            if not task.get("groupBy") and primary.get("groupBy"):
                task["groupBy"] = list(dict.fromkeys(list(task.get("groupBy") or []) + list(primary["groupBy"])))
            if not task.get("timeConstraint") and primary.get("timeConstraint"):
                task["timeConstraint"] = primary["timeConstraint"]
            if not task.get("requestedChart") and primary.get("requestedChart"):
                task["requestedChart"] = primary["requestedChart"]

    return {"tasks": tasks, "clarification": None}
