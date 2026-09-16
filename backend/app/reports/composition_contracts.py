"""Personal report composition: governed references, never client-authored SQL."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dashboard.schemas import AccentPreset


class CompositionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportBlock(CompositionModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["kpi", "trend", "breakdown"]
    title: str = Field(min_length=1, max_length=100)
    metricId: str | None = Field(default=None, max_length=40)
    dimension: str | None = Field(default=None, max_length=80)
    grain: Literal["day", "week", "month"] = "day"
    chart: Literal["number", "line", "area", "column", "bar", "donut", "pareto", "table", "calendar"] | None = None
    width: Literal["half", "full"] = "full"
    height: Literal["compact", "standard", "focus"] = "standard"
    accent: AccentPreset = "default"
    compare: bool = False
    topN: int = Field(default=12, ge=2, le=40)

    @model_validator(mode="after")
    def valid_block(self):
        if not self.metricId:
            raise ValueError("数据内容必须选择已发布指标")
        if self.kind == "breakdown" and not self.dimension:
            raise ValueError("维度分析必须选择指标支持的维度")
        allowed = {
            "kpi": {"number"}, "trend": {"line", "area", "column", "calendar"},
            "breakdown": {"bar", "donut", "pareto", "table"},
        }
        self.chart = self.chart or {"kpi": "number", "trend": "line", "breakdown": "bar"}[self.kind]
        if self.chart not in allowed[self.kind]:
            raise ValueError("图表类型与内容的数据结构不兼容")
        if self.chart == "donut" and self.topN > 12:
            raise ValueError("环形图最多展示 12 个分类，请改用条形图或数据条表格查看更多项目")
        if self.kind == "breakdown" and self.compare:
            raise ValueError("上一周期对比仅用于指标概览与趋势，不能对分组数值直接求和")
        if self.chart == "calendar" and (self.grain != "day" or self.compare):
            raise ValueError("日历热力图仅支持按日展示本期分布，请使用折线或柱形图对比上一周期")
        return self


class ReportComposition(CompositionModel):
    schemaVersion: Literal["report-composition/v1"] = "report-composition/v1"
    name: str = Field(default="我的制造周报", min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    blocks: list[ReportBlock] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unique_blocks(self):
        ids = [block.id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("内容标识不能重复")
        return self


class CompositionTemplateSave(CompositionModel):
    version: int = Field(default=0, ge=0)
    composition: ReportComposition


class CompositionPreview(CompositionModel):
    sourceId: int = Field(gt=0)
    periodStart: datetime
    periodEnd: datetime
    composition: ReportComposition

    @model_validator(mode="after")
    def valid_period(self):
        if self.periodStart.tzinfo or self.periodEnd.tzinfo:
            raise ValueError("日期使用企业当地时间，不接受含时区偏移的日期")
        days = (self.periodEnd - self.periodStart).total_seconds() / 86400
        if not 0 < days <= 93:
            raise ValueError("统计区间必须为 1 至 93 天，结束时间不包含在内")
        return self


class CompositionGenerate(CompositionPreview):
    title: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def has_data(self):
        if not self.composition.blocks:
            raise ValueError("生成周报至少需要一个数据内容块")
        return self
