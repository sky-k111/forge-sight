"""Public dashboard contracts: layout plus governed query references, never SQL."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


WidgetKind = Literal["kpi", "trend", "rank", "recent", "context", "catalog"]
PeriodPreset = Literal["last7", "last30", "all"]
AccentPreset = Literal["default", "ocean", "teal", "violet", "graphite"]
ChartType = Literal[
    "auto", "number", "line", "area", "bar", "horizontal_bar",
    "donut", "pie", "treemap", "table",
]


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(default=0, ge=0, le=11)
    y: int = Field(default=0, ge=0, le=200)
    w: int = Field(default=3, ge=2, le=12)
    h: int = Field(default=3, ge=2, le=20)
    minW: int = Field(default=2, ge=2, le=12)
    minH: int = Field(default=2, ge=2, le=20)

    @model_validator(mode="after")
    def fits_dashboard_grid(self):
        if self.x + self.w > 12:
            raise ValueError("组件不能超出 12 列看板网格")
        if self.w < self.minW or self.h < self.minH:
            raise ValueError("组件尺寸不能小于最小尺寸")
        return self


class WidgetAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accent: AccentPreset = "default"
    chartType: ChartType = "auto"


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    kind: WidgetKind
    metricId: str | None = Field(default=None, max_length=40)
    dimension: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=100)
    appearance: WidgetAppearance = Field(default_factory=WidgetAppearance)
    layout: Layout = Field(default_factory=Layout)

    @model_validator(mode="after")
    def metric_widgets_require_metric(self):
        if self.kind in {"kpi", "trend", "rank"} and not self.metricId:
            raise ValueError("指标组件必须选择已发布指标")
        if self.kind == "rank" and not self.dimension:
            raise ValueError("维度对比组件必须选择业务维度")
        compatible_charts = {
            "kpi": {"auto", "number"},
            "trend": {"auto", "line", "area", "bar"},
            "rank": {"auto", "horizontal_bar", "bar", "donut", "pie", "treemap", "table"},
            "recent": {"auto"},
            "context": {"auto"},
            "catalog": {"auto"},
        }
        if self.appearance.chartType not in compatible_charts[self.kind]:
            raise ValueError("图表类型与组件数据结构不兼容")
        return self


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=0, ge=0)
    period: PeriodPreset = "last30"
    showContext: bool = True
    widgets: list[Widget] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_widget_ids(self):
        ids = [item.id for item in self.widgets]
        if len(ids) != len(set(ids)):
            raise ValueError("组件标识不能重复")
        system_kinds = [item.kind for item in self.widgets if item.kind in {"recent", "context", "catalog"}]
        if len(system_kinds) != len(set(system_kinds)):
            raise ValueError("同一种工作区组件只能添加一次")
        return self


class WidgetQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceId: int = Field(gt=0)
    metricId: str = Field(min_length=1, max_length=40)
    kind: Literal["kpi", "trend", "rank"]
    period: PeriodPreset = "last30"
    dimension: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def rank_requires_dimension(self):
        if self.kind == "rank" and not self.dimension:
            raise ValueError("维度对比必须选择业务维度")
        return self
