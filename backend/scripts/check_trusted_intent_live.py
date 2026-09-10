"""Small opt-in smoke check for the configured production-compatible LLM endpoint."""
from __future__ import annotations

import json

from app.agent.llm_intent import resolve_intent
from app.llm.provider import chat_completion


def generate(_state, _step, system, user):
    return chat_completion(system, user, structured=True, max_tokens=1800).content


def main() -> None:
    calendar, _, calendar_error = resolve_intent("统计上个月的产量", generate_text=generate)
    compound, _, compound_error = resolve_intent(
        "比较6月和7月的良率，再按周统计6月产量，前者用柱状图，后者用折线图",
        generate_text=generate,
    )
    previous = {
        "version": 2,
        "runId": "acceptance-previous",
        "results": [
            {"resultId": "R1", "label": "良率", "agentIntent": {
                "kind": "analysis", "question": "良率", "metrics": [{"semantic": "良率"}]}},
            {"resultId": "R2", "label": "产量", "agentIntent": {
                "kind": "analysis", "question": "产量", "metrics": [{"semantic": "产量"}]}},
        ],
    }
    followup, _, followup_error = resolve_intent(
        "把第二张改成折线图",
        [{"role": "system", "content": "上一轮成功分析条件", "analysisContext": previous}],
        generate,
    )
    checks = {
        "calendarReference": calendar.time.reference if calendar.time else None,
        "compoundSteps": len(compound.stepsHint),
        "compoundCharts": [step.chart.type if step.chart else None for step in compound.stepsHint],
        "followupTarget": followup.targetResultId,
        "followupOperation": followup.operation,
        "errors": [calendar_error, compound_error, followup_error],
    }
    print(json.dumps(checks, ensure_ascii=False))
    assert calendar_error is None and checks["calendarReference"] == "now"
    assert compound_error is None and checks["compoundSteps"] >= 2
    assert followup_error is None and checks["followupTarget"] == "R2"
    assert checks["followupOperation"] == "change_presentation"


if __name__ == "__main__":
    main()
