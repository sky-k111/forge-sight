"""统一语义契约：AgentIntent。

这是全系统唯一的「用户想要什么」语义事实源。意图由 LLM 意图解析器产出
（producer=llm），或在 LLM 不可用时由确定性兜底（producer=fallback）产出同一 shape。
语义（用户想要什么维度/时间/图/对比/是不是建模或报告）只在此处定义一次；后续
planner / graph / query_compiler / interpretation 全部消费它，而不再用正则重新推断语义。

安全边界（只读/白名单/预算/口径/审计）不在这里定义；它们由确定性校验层与受治理子图保证。
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Grain = Literal["day", "week", "month", "quarter", "year"]


def _coerce_time_range(v: Any) -> Any:
    """把 LLM 常用的「字符串形态」周期规范成 TimeRange 可接受的 dict。

    LLM 常把对比周期写成 "2023-05" / "5月" / "2023" 这类字符串；直接喂给
    list[TimeRange] 会抛 ValidationError。这里统一按月份/年份提取，并把
    裸月份（如“5月”）锚定到 data_horizon；显式年份（如“2026-05”）则是
    用户给出的事实，必须保留为绝对边界，不能悄悄改写成数据最新年份。
    非字符串/字典原样返回，交给校验器处理。
    """
    if v is None:
        return v
    if isinstance(v, dict):
        return v
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return {"mode": "absolute", "label": s, "reference": "data_horizon"}
    m = re.fullmatch(r"(\d{1,2})\s*月", s)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            return {"mode": "absolute", "grain": "month", "label": f"{mo}月", "reference": "data_horizon"}
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        year = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            start = date(year, mo, 1)
            end = date(year + 1, 1, 1) if mo == 12 else date(year, mo + 1, 1)
            return {"mode": "absolute", "grain": "month", "label": f"{year}-{mo:02d}",
                    "reference": "now", "start": start.isoformat(), "end": end.isoformat()}
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        year = int(m.group(1))
        return {"mode": "absolute", "grain": "year", "label": s, "reference": "now",
                "start": f"{year}-01-01", "end": f"{year + 1}-01-01"}
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        start = date(y, mo, d)
        return {"mode": "absolute", "grain": "day", "label": f"{y:04d}-{mo:02d}-{d:02d}", "reference": "now",
                "start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()}
    return {"mode": "absolute", "label": s, "reference": "data_horizon"}


class IntentKind(str, Enum):
    ANALYSIS = "analysis"           # 数据查询/分析
    MODELING = "modeling"           # 建模/预测/训练
    REPORT = "report"               # 生成报告（周报/月报/专题）
    INVESTIGATION = "investigation"  # 下钻调查/根因
    UTILITY = "utility"             # 系统信息/时间
    CONVERSATION = "conversation"   # 闲聊/范围外（一般无数据）
    CLARIFY = "clarify"             # 需要向用户确认


class TimeMode(str, Enum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"
    CURRENT = "current"
    RECENT = "recent"
    COMPARISON = "comparison"
    NONE = "none"


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: TimeMode
    grain: Grain | None = None
    start: str | None = None            # ISO 8601 起始（含）
    end: str | None = None              # ISO 8601 结束（不含）
    amount: int | None = None
    unit: str | None = None             # 天/周/月/季度/年
    reference: Literal["now", "data_horizon", "prev_period"] = "data_horizon"
    comparisons: list["TimeRange"] = []  # 对比周期，如 [5月, 6月]
    label: str = ""                      # 人类可读 label，供结论/证据披露

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_to_default(cls, v: Any) -> Any:
        """LLM 常输出 reference/label:null；规范化为默认值，避免整条意图被打回兜底。"""
        if isinstance(v, dict):
            if v.get("reference") is None:
                v["reference"] = "data_horizon"
            if v.get("label") is None:
                v["label"] = ""
        return v

    @field_validator("comparisons", mode="before")
    @classmethod
    def _coerce_comparisons(cls, v: Any) -> Any:
        """LLM 有时把对比周期写成字符串数组；规范化为 TimeRange。"""
        if v is None:
            return []
        if not isinstance(v, list):
            return v
        return [_coerce_time_range(item) for item in v]


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    semantic: str                        # 产线/产品/工序/车间/班次/缺陷类型/状态/时间
    code: str | None = None              # 元数据解析后的物理字段，由校验层回填
    grain: Grain | None = None           # 时间维度才需要
    label: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_to_default(cls, v: Any) -> Any:
        if isinstance(v, dict) and v.get("label") is None:
            v["label"] = ""
        return v


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    semantic: str                        # 产量/良率/不良数/缺陷率/产能利用率…
    id: str | None = None                # 元数据解析后回填
    aggregation: Literal["sum", "avg", "count", "max", "min", "rate", "share", "rank"] = "sum"
    alias: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_to_default(cls, v: Any) -> Any:
        if isinstance(v, dict) and v.get("alias") is None:
            v["alias"] = ""
        return v


class Chart(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["auto", "bar", "line", "donut", "scatter", "heatmap", "table", "combo"]
    x: str | None = None                 # 横轴维度名（semantic）
    y: str | None = None                 # 主指标 alias
    series: str | None = None            # 分组/序列
    orient: Literal["auto", "vertical", "horizontal"] = "auto"
    secondary: str | None = None         # combo 次轴


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    basis: str = ""                      # 对比基准描述
    periods: list[TimeRange] = []
    top_diff_k: int | None = Field(default=None, alias="topDiffK")  # 例如「差异最大的两个工序」
    by: Dimension | None = None          # 对比后下钻维度

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_to_default(cls, v: Any) -> Any:
        if isinstance(v, dict) and v.get("basis") is None:
            v["basis"] = ""
        return v

    @field_validator("periods", mode="before")
    @classmethod
    def _coerce_periods(cls, v: Any) -> Any:
        """LLM 常把对比周期写成字符串（如 "2023-05"）；规范化为 TimeRange 列表。"""
        if v is None:
            return []
        if not isinstance(v, list):
            return v
        return [_coerce_time_range(item) for item in v]

    @field_validator("by", mode="before")
    @classmethod
    def _coerce_by(cls, v: Any) -> Any:
        """LLM 常把下钻维度写成字符串（如 by=\"工序\"），规范化为 Dimension。"""
        if isinstance(v, str):
            return {"semantic": v, "label": v}
        return v


class ModelingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task: str                            # 回归/二分类/多分类/聚类/异常检测
    target: str | None = None
    features: list[str] = []
    horizon: str | None = None


class ReportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str                            # 周报/月报/专题
    period: str | None = None
    audience: str | None = None
    channel: str | None = None           # feishu/dingtalk/email


class ClarifyField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    label: str
    question: str
    options: list[str] = []
    allowCustom: bool = True


class StepIntent(BaseModel):
    """能力步骤内嵌的、已裁剪的子语义（用于复合/多步问题）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: IntentKind
    metrics: list[Metric] = []
    dimensions: list[Dimension] = []
    time: TimeRange | None = None
    chart: Chart | None = None
    comparison: Comparison | None = None


class AgentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    relation: Literal["new", "refine_previous", "retry_previous"] = "new"
    operation: Literal["query", "add_dimension", "replace_dimension", "remove_dimension", "change_presentation", "replace_filter", "retry"] = "query"
    changedFields: list[str] = []
    filters: list[dict[str, Any]] = []
    sort: list[dict[str, Any]] = []
    topN: int | None = Field(default=None, ge=1, le=10000)
    inheritedFrom: str | None = None
    targetResultId: str | None = None       # 多结果追问明确指向的结果，例如 R2
    understanding: list[str] = []
    failure: dict[str, Any] | None = None  # 系统故障，不是业务澄清
    kind: IntentKind
    question: str                        # 原始输入（多轮时已含上下文解析文本）
    metrics: list[Metric] = []
    dimensions: list[Dimension] = []
    time: TimeRange | None = None
    chart: Chart | None = None
    comparison: Comparison | None = None
    modeling: ModelingSpec | None = None
    report: ReportSpec | None = None
    clarify: list[ClarifyField] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    producer: Literal["llm", "fallback"] = "llm"
    reasons: list[str] = []              # 供审计展示「为什么这样理解」
    stepsHint: list[StepIntent] = []     # 复合/多步问题拆出的子语义

    @field_validator("clarify", mode="before")
    @classmethod
    def _coerce_clarify(cls, v: Any) -> Any:
        """LLM 有时输出 clarify:null；规范化为空列表。"""
        return v or []
