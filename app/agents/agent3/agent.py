"""Agent3 impact proposals using the same structured client as Agent1."""

from collections.abc import Callable

from app.agents.agent3.draft import Agent3ImpactDraft
from app.agents.agent3.guards import finalize_estimates
from app.agents.agent3.prompts import PLAN_SYSTEM, PROMPT_VERSION, build_user_prompt
from app.agents.base import AgentFailure
from app.llm.client import LlmClient
from app.orchestration.preflight import preflight_a3
from app.tools.errors import ToolInputError
from app.tools.get_schedule import get_schedule
from schema.schemas import Agent3Input, Agent3Output, AgentError, Estimate, Line, Meta, Question, Schedule


class LlmAgent3:
    """Return impact only. Negotiation generation requires a separate PM decision."""

    def __init__(self, llm: LlmClient, *, schedule_reader: Callable = get_schedule):
        self.llm = llm
        self.schedule_reader = schedule_reader

    def run(self, inp: Agent3Input) -> Agent3Output:
        gate = preflight_a3(inp)
        if gate:
            if gate.code == "WAITING_REVIEW":
                return Agent3Output(context=inp.context, status="WAITING_REVIEW", estimates=[])
            raise AgentFailure(AgentError(code=gate.code, message=gate.reason, stage="agent3"))
        if not inp.a2_result.meta.result_id or not inp.pm_review.review_id:
            self._invalid("upstream results must have caller-issued IDs")
        targets = inp.targets()
        if not targets:
            return Agent3Output(context=inp.context, status="NOT_REQUIRED", estimates=[])
        result = self.llm.complete_structured(
            system=PLAN_SYSTEM, user=build_user_prompt(inp),
            schema=Agent3ImpactDraft, tool_name="submit_impact",
        )
        proposal = result.parsed

        def ids(numbers):
            if len(numbers) != len(set(numbers)) or any(n < 1 or n > len(targets) for n in numbers):
                self._invalid("unknown or duplicate target number")
            return [targets[n - 1].item_id for n in numbers]

        covered = [n for e in proposal.estimates for n in e.item_numbers]
        if sorted(covered) != list(range(1, len(targets) + 1)):
            self._invalid("impact must cover every selected item exactly once")
        basis = [i.item_id for i in inp.constraints]
        try:
            schedule = self.schedule_reader(inp.context.project_id, inp.context.baseline_as_of)
            basis.append(f"get_schedule:{inp.context.project_id}@{inp.context.baseline_as_of}")
            if schedule.get("contract_end_ref"):
                basis.append(schedule["contract_end_ref"])
        except ToolInputError:
            pass

        estimates = []
        for estimate in proposal.estimates:
            item_ids = ids(estimate.item_numbers)
            lines = []
            for line in estimate.lines:
                line_ids = ids(line.item_numbers)
                if not set(line_ids) <= set(item_ids):
                    self._invalid("task references a different estimate")
                lines.append(Line(task=line.task, item_ids=line_ids, role=line.role,
                                  md=line.md, assumption=line.assumption))
            if lines and {i for line in lines for i in line.item_ids} != set(item_ids):
                self._invalid("task lines must cover every estimate item")
            estimates.append(Estimate(
                item_ids=item_ids, status=estimate.status, lines=lines,
                schedule=Schedule(business_days=None, basis=basis,
                    reason=estimate.reason or "가용 인력·선행 작업 정보가 없어 일정 영향 미산정"),
                rate_card_id=inp.rate_card_id,
            ))
        questions = [Question(
            question_id=f"{inp.context.case_id}-A3Q{n}", case_id=inp.context.case_id,
            stage="agent3", item_ids=ids(q.item_numbers), field=q.field,
            text=q.text, reason=q.reason, target=q.target, blocking=q.blocking,
        ) for n, q in enumerate(proposal.questions, 1)]
        if not questions and any(e.status == "not_estimable" for e in estimates):
            unresolved = [i for e in estimates if e.status == "not_estimable" for i in e.item_ids]
            questions.append(Question(question_id=f"{inp.context.case_id}-A3Q1", case_id=inp.context.case_id,
                stage="agent3", item_ids=unresolved, field="estimation_details",
                text="공수 산정을 위해 대상·규모·구현 조건을 구체화해주세요.",
                reason="요구사항 또는 작업 규모 부족으로 산정할 수 없음", target="pm", blocking=False))
        out = Agent3Output(
            context=inp.context,
            meta=Meta(model=result.label, latency_ms=result.latency_ms, agent_version="agent3-0.1.0",
                debug={"prompt_version": PROMPT_VERSION, "retried": result.retried,
                       "input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}),
            status="NEEDS_INPUT" if any(q.blocking for q in questions) else "IMPACT_DRAFT_READY",
            estimates=estimates, questions=questions, draft=None,
        )
        return finalize_estimates(out, inp.rate_card_id)

    @staticmethod
    def _invalid(message: str):
        raise AgentFailure(AgentError(code="OUTPUT_INVALID", message=message, stage="agent3"))
