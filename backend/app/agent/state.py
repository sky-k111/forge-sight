"""可序列化 LangGraph State。完整结果只保存 Artifact 引用；第一版小结果暂随状态流转。"""

from typing import Any, TypedDict


class AnalysisState(TypedDict, total=False):
    session_id: str
    run_id: str
    source_id: int
    user_id: str
    user_question: str
    analysis_question: str
    conversation_context: list[dict[str, str]]
    memory_enabled: bool
    memory_context: dict[str, Any]
    intent_kind: str
    direct_response: str
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
    confidence: dict[str, Any]
    sql: str
    validation: dict[str, Any]
    execution: dict[str, Any]
    conclusion: str
    modeling_proposal: dict[str, Any] | None
    error: dict[str, Any] | None
