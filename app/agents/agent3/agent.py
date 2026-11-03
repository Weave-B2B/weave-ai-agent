"""Agent3 impact proposals using the same structured client as Agent1."""

from collections.abc import Callable

from app.agents.agent3.draft import Agent3ImpactDraft
from app.agents.agent3.effort import load_effort_rules, matches_rule as _matches_rule
from app.agents.agent3.guards import finalize_estimates
from app.agents.agent3.prompts import PLAN_SYSTEM, PROMPT_VERSION, build_user_prompt
from app.agents.base import AgentFailure
from app.llm.client import LlmClient
from app.orchestration.preflight import preflight_a3
from app.tools.errors import ToolInputError
from app.tools.get_schedule import get_schedule
from schema.schemas import Agent3Input, Agent3Output, AgentError, Estimate, Line, Meta, Question, Schedule


def _retryable_rule_error(proposal, inp: Agent3Input, rules: dict) -> bool:
    """Retry an invalid rule selection without relaxing server validation."""
    targets = inp.targets()
    for estimate in proposal.estimates:
        used = set()
        for line in estimate.lines:
            if not line.item_numbers or any(n < 1 or n > len(targets) for n in line.item_numbers):
                continue
            matched = {targets[n - 1].item_id: targets[n - 1].text for n in line.item_numbers}
            rule = rules.get(line.rule_id)
            if not rule or not _matches_rule(rule, inp, matched) or rule['role'] != line.role or line.rule_id in used:
                return True
            used.add(line.rule_id)
    return False


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
        prompt = build_user_prompt(inp)
        result = self.llm.complete_structured(
            system=PLAN_SYSTEM, user=prompt,
            schema=Agent3ImpactDraft, tool_name="submit_impact",
        )
        proposal = result.parsed
        effort_rules = load_effort_rules()
        rule_retry = False
        assumption_retry = False
        rule_retry = _retryable_rule_error(proposal, inp, effort_rules["rules"])
        assumption_retry = any(e.status == "not_estimable" for e in proposal.estimates)
        if rule_retry or assumption_retry:
            try:
                corrected = self.llm.complete_structured(
                    system=PLAN_SYSTEM,
                    user=prompt + "\n이전 응답에 미산정 항목 또는 잘못된 공수 기준 ID가 있습니다. 제공된 rules의 키와 직군만 사용하고, 세부 조건을 합리적으로 가정해 작업 유형별 임시 기준으로 산정할 수 있는지 다시 확인하세요. 확인되지 않은 내용은 각 작업의 assumption에 명시하고 PM 확인 질문으로 남기세요. 가정으로도 규모를 판단할 수 없거나 기준이 없으면 미산정을 유지하세요. 같은 기준은 하나의 작업 행으로 합치세요.",
                    schema=Agent3ImpactDraft, tool_name="submit_impact",
                )
                corrected.usage.input_tokens += result.usage.input_tokens
                corrected.usage.output_tokens += result.usage.output_tokens
                corrected.latency_ms += result.latency_ms
                result, proposal = corrected, corrected.parsed
            except AgentFailure:
                # Keep the first response and report the missing rule instead of inventing MD.
                pass

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
        missing_rules = []
        for estimate in proposal.estimates:
            item_ids = ids(estimate.item_numbers)
            lines = []
            used_rules = set()
            invalid_rule = False
            for line in estimate.lines:
                line_ids = ids(line.item_numbers)
                if not set(line_ids) <= set(item_ids):
                    self._invalid("task references a different estimate")
                rule = effort_rules["rules"].get(line.rule_id)
                matched_targets = {t.item_id: t.text for t in targets if t.item_id in line_ids}
                sample_matches = bool(rule) and _matches_rule(rule, inp, matched_targets)
                if not sample_matches or not rule or rule["role"] != line.role or line.rule_id in used_rules:
                    invalid_rule = True
                    continue
                used_rules.add(line.rule_id)
                marker = "작업 유형별 임시 기준 · 적용 적합성 PM 확인 필요" if rule.get("kind") == "general_test" else "테스트 공수 기준"
                lines.append(Line(task=line.task, item_ids=line_ids, role=line.role,
                                  md=rule["md"], assumption=f"{line.assumption} [{marker} {effort_rules['version']}:{line.rule_id}]"))
            if invalid_rule:
                missing_rules.extend(item_ids)
                lines = []
            if lines and {i for line in lines for i in line.item_ids} != set(item_ids):
                self._invalid("task lines must cover every estimate item")
            estimates.append(Estimate(
                item_ids=item_ids, status="not_estimable" if invalid_rule else estimate.status, lines=lines,
                schedule=Schedule(business_days=None, basis=basis,
                    reason=estimate.reason or "가용 인력·선행 작업 정보가 없어 일정 영향 미산정"),
                rate_card_id=inp.rate_card_id,
            ))
        questions = [Question(
            question_id=f"{inp.context.case_id}-A3Q{n}", case_id=inp.context.case_id,
            stage="agent3", item_ids=ids(q.item_numbers), field=q.field,
            text=q.text, reason=q.reason, target=q.target, blocking=q.blocking,
        ) for n, q in enumerate(proposal.questions, 1)]
        if missing_rules:
            questions.append(Question(question_id=f"{inp.context.case_id}-A3QRULE", case_id=inp.context.case_id,
                stage="agent3", item_ids=sorted(set(missing_rules)), field="effort_rule",
                text="해당 작업에 적용할 공수 기준을 확인해주세요.",
                reason="모델이 공수 기준을 찾지 못했거나 기준 ID·직군이 잘못됐거나 중복되었습니다. 입력과 기준을 확인한 뒤 다시 실행해주세요.", target="pm", blocking=True))
        blocked_items = {i for q in questions if q.blocking and q.status == "open" for i in q.item_ids}
        unresolved = [i for e in estimates if e.status == "not_estimable" for i in e.item_ids if i not in blocked_items]
        if unresolved:
            questions.append(Question(question_id=f"{inp.context.case_id}-A3QDETAIL", case_id=inp.context.case_id,
                stage="agent3", item_ids=unresolved, field="estimation_details",
                text="공수 산정을 위해 대상·규모·구현 조건을 구체화해주세요.",
                reason="요구사항 또는 작업 규모 부족으로 산정할 수 없음", target="pm", blocking=True))
        out = Agent3Output(
            context=inp.context,
            meta=Meta(model=result.label, latency_ms=result.latency_ms, agent_version="agent3-0.1.0",
                debug={"prompt_version": PROMPT_VERSION, "retried": result.retried,
                       "effort_rule_version": effort_rules["version"],
                       "effort_rule_retry": rule_retry,
                       "assumption_retry": assumption_retry,
                       "input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}),
            status="NEEDS_INPUT" if any(q.blocking for q in questions) or any(e.status == "not_estimable" for e in estimates) else "IMPACT_DRAFT_READY",
            estimates=estimates, questions=questions, draft=None,
        )
        return finalize_estimates(out, inp.rate_card_id)

    @staticmethod
    def _invalid(message: str):
        raise AgentFailure(AgentError(code="OUTPUT_INVALID", message=message, stage="agent3"))
