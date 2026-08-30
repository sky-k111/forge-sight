"""Business-level capability registry for the bounded planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agent.plan_schema import EffectLevel


class CapabilityDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,80}$")
    version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="inputSchema")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="outputSchema")
    effect_level: EffectLevel = Field(alias="effectLevel")
    allowed_roles: list[str] = Field(default_factory=lambda: ["business", "admin"], alias="allowedRoles")
    required_scopes: list[str] = Field(default_factory=list, alias="requiredScopes")
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds", ge=1, le=3600)
    retry_policy: str = Field(default="internal_only", alias="retryPolicy")
    approval_policy: str = Field(default="none", alias="approvalPolicy")
    cost_class: str = Field(default="low", alias="costClass")
    idempotent: bool = True
    delegated: bool = False


@dataclass(frozen=True)
class CapabilityContext:
    run_id: str
    session_id: str
    tenant_id: str
    user_id: str
    role: str
    source_ids: tuple[int, ...]
    capability_scopes: frozenset[str]
    plan_revision: int
    step_id: str
    idempotency_key: str
    artifact_refs: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def source_id(self) -> int:
        if not self.source_ids:
            raise ValueError("当前运行没有已授权数据源")
        return self.source_ids[0]


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason_code: str = "OK"
    message: str = ""
    requires_approval: bool = False


@dataclass
class CapabilityResult:
    status: str
    summary: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    reason_code: str = "OK"
    suggested_next: list[str] = field(default_factory=list)
    usage: dict[str, int | float] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


class Capability(Protocol):
    descriptor: CapabilityDescriptor

    def authorize(self, context: CapabilityContext, arguments: dict[str, Any]) -> AuthorizationDecision: ...

    def execute(self, context: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult: ...


class FunctionCapability:
    def __init__(
        self,
        descriptor: CapabilityDescriptor,
        execute: Callable[[CapabilityContext, dict[str, Any]], CapabilityResult],
        authorize: Callable[[CapabilityContext, dict[str, Any]], AuthorizationDecision] | None = None,
    ):
        self.descriptor = descriptor
        self._execute = execute
        self._authorize = authorize

    def authorize(self, context: CapabilityContext, arguments: dict[str, Any]) -> AuthorizationDecision:
        if self._authorize is not None:
            return self._authorize(context, arguments)
        return AuthorizationDecision(allowed=True)

    def execute(self, context: CapabilityContext, arguments: dict[str, Any]) -> CapabilityResult:
        return self._execute(context, arguments)


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        name = capability.descriptor.name
        if name in self._capabilities:
            raise ValueError(f"能力 {name} 已注册")
        self._capabilities[name] = capability

    def get(self, name: str) -> Capability:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise KeyError(f"未注册能力：{name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._capabilities

    def descriptors(self) -> list[dict[str, Any]]:
        return [
            item.descriptor.model_dump(by_alias=True, mode="json")
            for item in sorted(self._capabilities.values(), key=lambda value: value.descriptor.name)
        ]

    def names(self) -> set[str]:
        return set(self._capabilities)

    def names_for(self, role: str, scopes: set[str] | frozenset[str]) -> set[str]:
        """Return only capabilities the frozen identity can ever authorize.

        Approval is intentionally not evaluated here: it is a runtime decision.
        Role and scope denial, however, must be removed before the model sees the
        catalogue so it cannot plan an impossible or privilege-escalating step.
        """

        granted = set(scopes)
        return {
            name
            for name, capability in self._capabilities.items()
            if role in capability.descriptor.allowed_roles
            and set(capability.descriptor.required_scopes).issubset(granted)
        }


def authorize_descriptor(
    descriptor: CapabilityDescriptor,
    context: CapabilityContext,
    arguments: dict[str, Any],
) -> AuthorizationDecision:
    if context.role not in descriptor.allowed_roles:
        return AuthorizationDecision(False, "ROLE_DENIED", "当前角色无权使用该能力")
    missing = set(descriptor.required_scopes) - set(context.capability_scopes)
    if missing:
        return AuthorizationDecision(False, "SCOPE_DENIED", f"缺少能力权限：{sorted(missing)}")
    supplied_source = arguments.get("sourceId")
    if supplied_source is not None and int(supplied_source) not in context.source_ids:
        return AuthorizationDecision(False, "SOURCE_SCOPE_DENIED", "计划参数越出了当前数据源范围")
    if descriptor.effect_level in {EffectLevel.EXTERNAL_EFFECT, EffectLevel.GOVERNANCE}:
        return AuthorizationDecision(True, "APPROVAL_REQUIRED", "该操作需要显式确认", True)
    if descriptor.approval_policy == "always":
        return AuthorizationDecision(True, "APPROVAL_REQUIRED", "该操作需要显式确认", True)
    return AuthorizationDecision(True)
