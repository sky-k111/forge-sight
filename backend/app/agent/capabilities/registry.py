from __future__ import annotations

from app.agent.capabilities.analysis import analysis_capabilities
from app.agent.capabilities.delivery import delivery_capabilities
from app.agent.capabilities.investigation import investigation_capabilities
from app.agent.capabilities.modeling import modeling_capabilities
from app.agent.capabilities.monitoring import monitoring_capabilities
from app.agent.capabilities.prediction import prediction_capabilities
from app.agent.capabilities.report import report_capabilities
from app.agent.capabilities.system import system_capabilities
from app.agent.capability_registry import CapabilityRegistry


def build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in (
        *system_capabilities(),
        *analysis_capabilities(),
        *delivery_capabilities(),
        *modeling_capabilities(),
        *prediction_capabilities(),
        *monitoring_capabilities(),
        *report_capabilities(),
        *investigation_capabilities(),
    ):
        registry.register(capability)
    return registry
