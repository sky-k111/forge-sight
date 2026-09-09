"""对话意图路由前置层（dialogue-intent）。

只决定「这一轮走对话（不查数据）还是走分析」，不复制 LLM 回复生成——
对话回复由 `execute_inline_conversation(None)` 里已有的 LLM 对话器生成（自带上下文+降级兜底）。

要解决的两类「笨」：
1) 真实闲聊被 canned 模板短路，显得像机器人；
2) 「你这个是所有产线的吗」这类【引用上一轮结果的确认/口径澄清】被正则当成新查询，重跑整条分析。

准则：
- 角色/能力/寒暄（你是谁、你有什么能力、你好、谢谢）→ 零成本 canned（本就自然）。
- 真实闲聊/常识/你问我答 → 交给 LLM 自然回应（不用模板拒绝）。
- 引用上一轮结果、且带确认/澄清语气（这个/上面/所有产线的吗…）→ 对话，不重跑查询。
- 带明确指标+时间/维度的数据查询、业务追问（含「继续看看它最近怎么样」）→ 仍走分析。
- 系统类（你是谁/现在几点）走 utility；模糊 unknown 走 LLM 路由，都不覆盖。
"""

from __future__ import annotations

import re

# 明确指代上一轮结果的词（路由门，非语义来源）。刻意不收「它/该」等过宽代词，
# 以免把「继续看看它最近怎么样」这类业务追问误判成确认。
_REFERENCE_TERMS = (
    "这个", "这些", "那个", "那些", "上面", "刚才", "前面", "之前", "上一次",
    "上一条", "这条", "那个数", "这个数", "这些数", "你刚才", "你刚刚", "刚刚",
    "上一步",
)
# 范围/口径确认式（如「所有产线的吗」「整体的是吗」）
_SCOPE_CONFIRM_RE = re.compile(r"(所有|全部|整体|全部产线|这些产线|哪些).*(吗|呢|是不是|对不对|对吗|是吗|对吧)")

_GREETING_RE = re.compile(r"(^|\s)(你好|您好|嗨|hi|hello|辛苦了|谢谢|感谢|再见|拜拜)(\s|$)", re.IGNORECASE)
_ROLE_QUESTION_RE = re.compile(r"你是谁|你是什么|你有什么能力|你能做什么|你能干什么|你会什么|怎么使用|如何使用|你的功能|能做什么|有哪些功能|能帮我做什么")

_ROLE_ANSWER = (
    "我是 ForgeSight，面向制造业数据底座的智能问析 Agent。我可以帮你查询并解释生产、质量、设备、"
    "库存数据，选择合适图表并给出带证据的结论；数据均为只读分析，不会改动底层数据。"
    "想分析什么，直接用业务语言告诉我就行。"
)


def _looks_referential(text: str) -> bool:
    """是否像是引用/确认上一轮结果的短追问。"""
    t = str(text or "").strip()
    if not t:
        return False
    if any(p in t for p in _REFERENCE_TERMS):
        return True
    if _SCOPE_CONFIRM_RE.search(t):
        return True
    return False


def has_previous_result(context: list[dict[str, str]] | None) -> bool:
    """最近对话里是否有过一条带内容的助手回答（代表已有分析结果可引用）。"""
    for item in reversed(context or []):
        if item.get("role") == "assistant" and str(item.get("content") or "").strip():
            return True
    return False


def should_treat_as_conversation(text: str, has_prev_result: bool, request_kind: str) -> bool:
    """这一轮是否应走对话（不查数据）。仅在已判为 analysis 时做「纠正」。"""
    t = str(text or "").strip()
    if not t:
        return False
    # 系统/模糊路由交给各自处理器，不覆盖。
    if request_kind in {"utility", "unknown"}:
        return False
    # 引用上一轮结果的确认/口径澄清：即使带产线/不良率等数据信号，也不该重新查询。
    if request_kind == "analysis" and has_prev_result and _looks_referential(t):
        return True
    return False


def inline_conversation_reply(text: str) -> str | None:
    """角色/能力/寒暄的零成本回复；其余返回 None → 交给 LLM 对话器。"""
    t = str(text or "").strip()
    if not t:
        return None
    if _ROLE_QUESTION_RE.search(t):
        return _ROLE_ANSWER
    if _GREETING_RE.search(t):
        if re.search(r"谢谢|感谢|辛苦了", t):
            return "不客气，有需要随时问。"
        if re.search(r"再见|拜拜", t):
            return "好的，随时可以回来继续分析。"
        return "你好，我是 ForgeSight。想分析生产、质量、设备或库存数据，直接告诉我就行。"
    return None
