"""可序列化 LangGraph State。完整结果只保存 Artifact 引用；第一版小结果暂随状态流转。"""

from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    session_id: str
    run_id: str
    source_id: int
    tenant_id: str
    role: str
    capability_scopes: list[str]
    user_id: str
    user_question: str
    analysis_question: str
    conversation_context: list[dict[str, str]]
    memory_enabled: bool
    memory_context: dict[str, Any]
    intent_kind: str
    direct_response: str
    chart_adjustment: dict[str, Any] | None
    utility_question: str
    request_segments: list[dict[str, Any]]
    preclassified_route_labels: dict[int, str]
    preclassified_route_responses: dict[int, str]
    blocked: bool
    waiting_user: bool
    retry_count: int
    attempted_sqls: list[str]
    tasks: list[dict[str, Any]]
    task_index: int
    task: dict[str, Any]
    task_results: list[dict[str, Any]]
    clarification_response: dict[str, Any] | None
    semantic_context: dict[str, Any]
    skill_enhancement_enabled: bool
    skill_context: dict[str, Any]
    metric_resolution: dict[str, Any]
    interpretation: dict[str, Any]
    confidence: dict[str, Any]
    sql: str
    validation: dict[str, Any]
    execution: dict[str, Any]
    conclusion: str
    modeling_proposal: dict[str, Any] | None
    planner_enabled: bool
    planner_model_enabled: bool
    execution_path: str
    planning_reason: str
    plan: dict[str, Any] | None
    plan_validation: dict[str, Any]
    plan_revision: int
    active_plan_step_id: str | None
    active_plan_step: dict[str, Any] | None
    capability_result: dict[str, Any] | None
    capability_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    replan_count: int
    capability_budget: dict[str, Any]
    pending_approval: dict[str, Any] | None
    progress_decision: dict[str, Any]
    finalization_reason: str | None
    planner_degraded: bool
    error: dict[str, Any] | None

