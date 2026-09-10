"""Bounded planner: model-assisted planning with deterministic safe fallback."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.agent.ids import new_id
from app.agent.intent import build_intent_plan
from app.agent.intent_adapter import intent_to_tasks
from app.agent.intent_contract import AgentIntent
from app.agent.plan_schema import (
    AgentPlan,
    PlanBudget,
    PlanStep,
    PlanValidationResult,
    StepFailurePolicy,
)

PLANNING_TERMS = (
    "根因", "为什么", "原因", "下钻", "进一步", "继续调查", "形成报告", "整理成", "周报",
    "月报", "预测", "建模", "模型", "然后", "再根据", "如果", "并发送",
)
REPORT_TERMS = ("周报", "月报", "专题报告", "形成报告", "整理成报告", "整理成质量周报")
_STRONG_MODELING_TERMS = ("预测", "建模", "模型", "训练", "模型治理", "分类器", "二分类", "多分类", "聚类", "异常检测", "机器学习", "回归", "智能建模")
# “分类”在这些语境下是“按某维度分组”，不是机器学习分类：例如“分别按产线以及周数分类”。
_GROUPING_CLASSIFY_RE = re.compile(r"(?:\u5206\u522b\s*)?(?:\u6309|\u6309\u7167|\u6839\u636e|\u4f9d\u636e|\u4f9d|\u4ee5|\u5206\u4e3a|\u5206\u6210)\s*.{0,24}(?:\u5206\u7c7b|\u5206\u7ec4)")


def _intent_kind(intent: AgentIntent | dict[str, Any]) -> str:
    if isinstance(intent, AgentIntent):
        return intent.kind.value
    return str((intent or {}).get("kind") or "")


def _intent_get(intent: AgentIntent | dict[str, Any], key: str) -> Any:
    if isinstance(intent, AgentIntent):
        return getattr(intent, key, None)
    return (intent or {}).get(key)


def _as_intent(data: AgentIntent | dict[str, Any]) -> AgentIntent:
    if isinstance(data, AgentIntent):
        return data
    return AgentIntent.model_validate(data)


def _is_llm_intent(intent: AgentIntent | dict[str, Any]) -> bool:
    """仅当意图由 LLM 实际产出时才信任其语义；确定性兜底仍走 build_intent_plan。"""
    if isinstance(intent, AgentIntent):
        return intent.producer == "llm"
    return str((intent or {}).get("producer") or "") == "llm"


def _capability_schemas(desc: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in desc or []:
        name = d.get("name")
        if name:
            out[name] = {
                "inputSchema": d.get("inputSchema") or {},
                "outputSchema": d.get("outputSchema") or {},
                "effectLevel": d.get("effectLevel"),
            }
    return out


def _looks_like_modeling(question: str) -> bool:
    """判断问题是否属于真正的建模/预测请求，而非业务分组或风险描述。

    原来用拼词分分拼的（“分类”“风险”）会把“分别按产线以及周数分类”误导到建模路径，实际上它只是查询分组。这里用语义判定避免误派。
    """
    if not question:
        return False
    if any(term in question for term in _STRONG_MODELING_TERMS):
        return True
    if "\u5206\u7c7b" not in question and "\u98ce\u9669" not in question:
        return False
    # 单独“分类/风险”但属于“按…分组”，不算建模。
    if _GROUPING_CLASSIFY_RE.search(question):
        return False
    return any(term in question for term in ("\u6a21\u578b", "\u5efa\u6a21", "\u9884\u6d4b", "\u8bad\u7ec3", "\u5f02\u5e38", "\u805a\u7c7b", "\u5206\u7c7b\u5668", "\u673a\u5668\u5b66\u4e60"))


def should_use_planner(
    question: str,
    request_segments: list[dict[str, Any]],
    *,
    enabled: bool,
    agent_intent: AgentIntent | dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if not enabled:
        return False, "FEATURE_DISABLED"
    if agent_intent is not None and _is_llm_intent(agent_intent):
        kind = str(_intent_kind(agent_intent))
        if kind in {"modeling", "report", "investigation"}:
            return True, "INTENT_CROSS_CAPABILITY"
        if kind == "analysis":
            comp = _intent_get(agent_intent, "comparison")
            steps = _intent_get(agent_intent, "stepsHint") or []
            dims = _intent_get(agent_intent, "dimensions") or []
            metrics = _intent_get(agent_intent, "metrics") or []
            # 简单单步查询（少量维度/指标、无对比、无多步）走快速路径。
            if comp or (steps and len(steps) > 1):
                return True, "INTENT_MULTI_TASK"
            if len(dims) <= 2 and len(metrics) <= 2:
                return False, "INTENT_SIMPLE_FAST_PATH"
            return True, "INTENT_COMPLEX_ANALYSIS"
        return False, "INTENT_NON_ANALYSIS"
    normalized = str(question or "")
    kinds = {str(item.get("kind")) for item in request_segments}
    analysis_count = sum(1 for item in request_segments if item.get("kind") == "analysis")
    cross_capability = "analysis" in kinds and bool(kinds & {"report", "utility"})
    mentions_modeling = _looks_like_modeling(normalized)
    mentions_report = any(term in normalized for term in REPORT_TERMS)
    dependent_goal = any(term in normalized for term in PLANNING_TERMS)
    if cross_capability or ("analysis" in kinds and (mentions_report or mentions_modeling)):
        return True, "CROSS_CAPABILITY_GOAL"
    if analysis_count > 1 and dependent_goal:
        return True, "DEPENDENT_MULTI_TASK"
    if "analysis" in kinds and dependent_goal and any(term in normalized for term in ("根因", "下钻", "为什么")):
        return True, "ADAPTIVE_ANALYSIS"
    return False, "SIMPLE_FAST_PATH"


def _analysis_steps(
    question: str,
    history: list[dict[str, str]],
    agent_intent: AgentIntent | dict[str, Any] | None = None,
) -> list[PlanStep]:
    if agent_intent is not None and _is_llm_intent(agent_intent):
        tasks = intent_to_tasks(_as_intent(agent_intent), question=question)
    else:
        tasks = (build_intent_plan(question, history).get("tasks") or [])
    steps: list[PlanStep] = []
    previous: str | None = None
    for task in tasks:
        step_id = new_id("pst")
        dependencies = [previous] if task.get("dependsOn") and previous else []
        steps.append(
            PlanStep(
                id=step_id,
                capability="analysis.query_data",
                purpose=f"查询并验证：{task.get('title') or task.get('normalizedQuestion')}",
                arguments={"question": task.get("normalizedQuestion"), "task": task},
                dependsOn=dependencies,
                successCriteria=["返回只读查询结果", "披露指标口径与数据限制"],
                onFailure=StepFailurePolicy.REPLAN_ONCE,
            )
        )
        previous = step_id
    return steps


def build_fallback_plan(
    question: str,
    request_segments: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
    *,
    budget: PlanBudget | None = None,
    agent_intent: AgentIntent | dict[str, Any] | None = None,
) -> AgentPlan:
    analysis_question = "；".join(
        str(item.get("text") or "")
        for item in request_segments
        if item.get("kind") == "analysis"
    ).strip("；") or question
    steps = _analysis_steps(analysis_question, history or [], agent_intent=agent_intent)
    if not steps:
        steps.append(
            PlanStep(
                id=new_id("pst"),
                capability="analysis.query_data",
                purpose="完成受治理的只读数据分析",
                arguments={"question": analysis_question},
                successCriteria=["返回可核验结果"],
                onFailure=StepFailurePolicy.REPLAN_ONCE,
            )
        )
    last_analysis = steps[-1].id
    needs_quality = any(term in question for term in ("为什么", "根因", "原因", "报告", "周报", "预测"))
    if needs_quality:
        quality_id = new_id("pst")
        steps.append(
            PlanStep(
                id=quality_id,
                capability="analysis.inspect_result_quality",
                purpose="检查样本量、时间覆盖和比较完整性",
                arguments={"fromStep": last_analysis},
                dependsOn=[last_analysis],
                successCriteria=["给出结构化数据质量结论"],
                onFailure=StepFailurePolicy.STOP_WITH_LIMITATIONS,
            )
        )
        last_analysis = quality_id
    if _looks_like_modeling(question):
        find_id = new_id("pst")
        steps.append(
            PlanStep(
                id=find_id,
                capability="modeling.find_reusable",
                purpose="优先寻找同数据源、目标和任务类型匹配的已发布模型",
                arguments={"question": question},
                dependsOn=[last_analysis],
                successCriteria=["返回可复用模型或明确说明未命中"],
                onFailure=StepFailurePolicy.RETURN_ANALYSIS_ONLY,
            )
        )
        propose_id = new_id("pst")
        steps.append(
            PlanStep(
                id=propose_id,
                capability="modeling.propose",
                purpose="没有可复用模型时形成可确认的建模草案",
                arguments={"question": question, "onlyIfNoReusable": True},
                dependsOn=[find_id],
                successCriteria=["返回模型复用建议或训练草案"],
                onFailure=StepFailurePolicy.RETURN_ANALYSIS_ONLY,
            )
        )
        last_analysis = propose_id
    if any(term in question for term in REPORT_TERMS):
        steps.append(
            PlanStep(
                id=new_id("pst"),
                capability="report.compose_from_evidence",
                purpose="复用本次已校验证据形成报告草稿，避免重复查询",
                arguments={"title": question[:120], "templateId": "tpl-quality-weekly" if "质量" in question else "tpl-operations-weekly"},
                dependsOn=[last_analysis],
                successCriteria=["草稿引用本次分析产物"],
                onFailure=StepFailurePolicy.RETURN_ANALYSIS_ONLY,
            )
        )
    return AgentPlan(
        goal=question,
        assumptions=[],
        steps=steps,
        stopConditions=["目标已满足", "需要用户澄清", "预算耗尽", "安全或权限校验失败"],
        budget=budget or PlanBudget(),
    )


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip().replace("```json", "").replace("```", "")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Planner 未返回 JSON 对象")
    return json.loads(cleaned[start:end + 1])


def normalize_delegated_tasks(plan: AgentPlan, run_id: str) -> AgentPlan:
    """Give every delegated subgraph step one stable task identity.

    Model-generated plans are allowed to omit the internal AnalysisTask envelope,
    but persistence and checkpoint recovery are not.  The ID is derived from the
    immutable plan step rather than generated during execution, so retries and
    resume never create duplicate tasks.
    """

    normalized: list[PlanStep] = []
    delegated_order = 0
    for step in plan.steps:
        if step.capability not in {"analysis.query_data", "modeling.propose"}:
            normalized.append(step)
            continue
        delegated_order += 1
        arguments = dict(step.arguments)
        task = dict(arguments.get("task") or {})
        question = str(arguments.get("question") or task.get("normalizedQuestion") or plan.goal)
        task.setdefault("id", f"tsk-{run_id[-12:]}-{step.id[-8:]}")
        task.setdefault("order", delegated_order)
        task.setdefault("title", step.purpose or question[:120])
        task.setdefault("originalQuestion", question)
        task.setdefault("normalizedQuestion", question)
        task.setdefault("mode", "planned")
        task.setdefault("analysisType", "modeling" if step.capability == "modeling.propose" else "query")
        task.setdefault("dependsOn", [])
        task.setdefault("requestedChart", None)
        task.setdefault("timeConstraint", None)
        task.setdefault("groupBy", [])
        arguments["task"] = task
        normalized.append(step.model_copy(update={"arguments": arguments}))
    return plan.model_copy(update={"steps": normalized})


def build_plan(
    state: dict[str, Any],
    allowed_capabilities: set[str],
    generate_text: Callable[[dict, str, str, str], str] | None,
    *,
    model_enabled: bool = True,
    budget: PlanBudget | None = None,
    capability_descriptors: list[dict[str, Any]] | None = None,
) -> tuple[AgentPlan, str, str | None]:
    fallback = build_fallback_plan(
        state.get("analysis_question") or state.get("user_question", ""),
        state.get("request_segments") or [],
        state.get("conversation_context") or [],
        budget=budget,
        agent_intent=state.get("agent_intent"),
    )
    if not model_enabled or generate_text is None:
        return normalize_delegated_tasks(fallback, state.get("run_id", "run")), "deterministic", None
    schema = AgentPlan.model_json_schema(by_alias=True)
    system = (
        "你是 ForgeSight 的受限业务规划器。只选择 allowedCapabilities 中的业务能力；"
        "不得生成 SQL、Shell、Python、网络请求、租户、用户或数据源授权参数。"
        "计划必须是按依赖顺序排列的 DAG，最多一次重规划，外部发送、训练、发布必须保留确认步骤。"
        "只返回符合 schema 的 JSON，不输出思维链。"
    )
    user = json.dumps(
        {
            "goal": state.get("user_question"),
            "analysisQuestion": state.get("analysis_question"),
            "segments": state.get("request_segments") or [],
            "allowedCapabilities": sorted(allowed_capabilities),
            "agentIntent": state.get("agent_intent"),
            "capabilitySchemas": _capability_schemas(capability_descriptors),
            "budget": (budget or fallback.budget).model_dump(by_alias=True),
            "schema": schema,
            "safeFallbackPlan": fallback.model_dump(by_alias=True),
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        raw = generate_text(state, "planner.build", system, user)
        candidate = AgentPlan.model_validate(_extract_json(raw))
        if any(step.capability not in allowed_capabilities for step in candidate.steps):
            raise ValueError("计划引用了未注册能力")
        # Every requested query retains its authoritative task contract. Model
        # planning may add inspection steps, but cannot silently drop a request.
        required = {s.arguments["task"]["requirementId"]: s.arguments["task"]
                    for s in fallback.steps if s.capability == "analysis.query_data"
                    and s.arguments.get("task", {}).get("requirementId")}
        covered = set()
        for step in candidate.steps:
            if step.capability != "analysis.query_data":
                continue
            rid = (step.arguments.get("task") or {}).get("requirementId") or step.arguments.get("requirementId")
            if rid in required:
                step.arguments = {**step.arguments, "task": required[rid]}
                covered.add(rid)
        if set(required) - covered:
            raise ValueError("计划未覆盖全部分析要求")
        return normalize_delegated_tasks(candidate, state.get("run_id", "run")), "model", None
    except Exception as exc:  # noqa: BLE001 - invalid model output must degrade safely
        return normalize_delegated_tasks(fallback, state.get("run_id", "run")), "deterministic_fallback", type(exc).__name__


def validate_plan(plan: AgentPlan, capability_names: set[str]) -> PlanValidationResult:
    errors: list[str] = []
    checks = ["计划 Schema 已校验", "步骤依赖为有序 DAG"]
    unknown = sorted({step.capability for step in plan.steps} - capability_names)
    if unknown:
        errors.append(f"计划引用未注册能力：{unknown}")
    if len(plan.steps) > plan.budget.max_capability_calls:
        errors.append("计划步骤数超过能力调用预算")
    database_steps = sum(step.capability == "analysis.query_data" for step in plan.steps)
    if database_steps > plan.budget.max_database_executions:
        errors.append("计划查询次数超过数据库执行预算")
    if not errors:
        checks.extend(["能力均已注册", "调用数量未超过预算"])
    return PlanValidationResult(
        valid=not errors,
        checks=checks,
        errors=errors,
        degraded=False,
        reasonCode="OK" if not errors else "PLAN_VALIDATION_FAILED",
    )


def revise_plan(
    plan: AgentPlan,
    observations: list[dict[str, Any]],
    capability_names: set[str],
) -> AgentPlan:
    """Deterministic bounded revision: stop unsafe branches and keep useful partial work.

    A later model revision can target this same contract.  Production safety does
    not depend on it: failed steps and their dependent side effects are removed,
    while already completed evidence remains available to the final response.
    """

    failed = {item.get("stepId") for item in observations if item.get("status") == "failed"}
    if not failed:
        return plan
    blocked = set(failed)
    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            if step.id not in blocked and set(step.depends_on) & blocked:
                blocked.add(step.id)
                changed = True
    kept = [step for step in plan.steps if step.id not in blocked and step.capability in capability_names]
    if not kept:
        # Keep one already-observed failed step as a terminal audit anchor.  Its
        # ID remains in observations, so the selector will not execute it again.
        # Clearing dependencies keeps the revised contract a valid DAG.
        anchor = next((step for step in plan.steps if step.id in failed), plan.steps[0])
        kept = [anchor.model_copy(update={"depends_on": []})]
    else:
        kept_ids = {step.id for step in kept}
        kept = [
            step.model_copy(update={"depends_on": [item for item in step.depends_on if item in kept_ids]})
            for step in kept
        ]
    return plan.model_copy(update={"steps": kept})
