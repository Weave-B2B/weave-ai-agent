"""Streamlit test UI for Agent3's impact → PM approval → draft workflow."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import streamlit as st

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, EditImpact, ReviewImpact
from app.agents.base import AgentFailure
from app.core.config import ROOT, Settings, settings as env_settings
from app.llm.client import FakeLlm, build_llm
from app.tools.errors import ToolInputError
from schema.schemas import Agent3Input

PREFIX = "agent3_demo_"
SCENARIOS = ["정상 산정 · 모노마켓 리뷰 추가", "QA 단가 누락", "범위 PM 검토 전", "산정 대상 없음"]
PHASES = {
    "WAITING_IMPACT_APPROVAL": "PM 확인·승인 대기",
    "WAITING_INPUT": "추가 정보 필요",
    "WAITING_UPSTREAM_REVIEW": "앞 단계 PM 검토 필요",
    "DRAFT_READY": "협의안 초안 작성 완료",
    "NOT_REQUIRED": "산정 대상 없음",
    "FAILED": "처리 실패",
}


def _sample_input(scenario: str) -> str:
    data = json.loads((ROOT / "examples/agent3/input.json").read_text(encoding="utf-8"))
    if scenario == SCENARIOS[1]:
        data["rate_card_id"] = "RC-SYNTH-2026H2-noQA"
    elif scenario == SCENARIOS[2]:
        data["pm_review"]["status"] = "PENDING"
    elif scenario == SCENARIOS[3]:
        for item in data["pm_review"]["items"]:
            item["estimate"] = False
    return json.dumps(data, ensure_ascii=False, indent=2)


def _fake_response() -> str:
    return json.dumps(json.loads((ROOT / "examples/agent3/fake_responses.json").read_text(encoding="utf-8"))[0],
        ensure_ascii=False, indent=2)


def _client(provider, model, base_url, api_key, region, fake_json):
    if provider == "fake":
        # A fresh queue for each click; rerunning never exhausts a shared fake client.
        return FakeLlm([json.loads(fake_json)])
    config = Settings(_env_file=None, llm_provider=provider, llm_model=model.strip(),
        llm_base_url=base_url.strip(), llm_api_key=api_key or env_settings.llm_api_key,
        aws_region=region.strip(), llm_max_tokens=env_settings.llm_max_tokens,
        llm_temperature=env_settings.llm_temperature,
        llm_read_timeout_sec=env_settings.llm_read_timeout_sec,
        llm_connect_timeout_sec=env_settings.llm_connect_timeout_sec,
        llm_local_json_mode=env_settings.llm_local_json_mode)
    return build_llm(config)


def _draft_markdown(draft: dict) -> str:
    scope = "\n\n".join(draft["scope"].splitlines())
    impact = "\n".join(f"- {line}" for line in draft["impact"].splitlines())
    parts = [("요약", draft["summary"]), ("범위 판단과 근거", scope),
        ("작업·공수·일정", impact), ("금액", draft["cost"] or "미산정"),
        ("확인 필요 사항", "\n".join(f"- {q}" for q in draft["open_questions"]) or "없음")]
    return "# 협의안 초안 · 내부 검토용\n\n" + "\n\n".join(f"## {title}\n\n{value}" for title, value in parts)


def _show_result(view: dict, changed: bool) -> None:
    st.divider()
    st.subheader("2. 영향 산정 결과")
    st.info(PHASES.get(view["phase"], view["phase"]))
    if changed:
        st.warning("입력이나 실행 설정이 변경됐습니다. 아래는 이전 결과입니다. 다시 산정한 뒤 수정·승인해주세요.")
    estimates = view["impact"]["estimates"]
    complete = bool(estimates) and all(e["totals"] is not None for e in estimates)
    columns = st.columns(4)
    columns[0].metric("공수", f"{sum(e['totals']['md'] for e in estimates):g} MD" if complete else "미산정")
    columns[1].metric("판매 산정가 · VAT 별도", f"{sum(e['totals']['price'] for e in estimates):,}원" if complete else "미산정")
    columns[2].metric("예상 원가", f"{sum(e['totals']['cost'] for e in estimates):,}원" if complete else "미산정")
    columns[3].metric("일정 영향", "미산정" if not estimates or any(e["schedule"]["business_days"] is None for e in estimates) else "항목별 확인")
    rows = [{"작업": line["task"], "직군": line["role"], "공수(MD)": line["md"], "가정": line["assumption"]}
        for estimate in estimates for line in estimate["lines"]]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    for estimate in estimates:
        if estimate["schedule"]["business_days"] is None:
            st.caption("일정 미산정: " + estimate["schedule"]["reason"])
    for question in view["impact"]["questions"]:
        st.warning(("필수 확인 · " if question["blocking"] else "확인 사항 · ") + question["text"])


def _edit_form(workflow: Agent3Workflow, view: dict, changed: bool) -> None:
    estimates = view["impact"]["estimates"]
    if not estimates or not all(e["lines"] for e in estimates):
        return
    with st.expander("공수·가정 수정"):
        st.caption("수정하면 금액을 다시 계산하고, 이전 승인과 협의안은 이력으로 남긴 뒤 재승인합니다.")
        edits = []
        with st.form(PREFIX + "edit_form"):
            for i, estimate in enumerate(estimates):
                lines = []
                for j, line in enumerate(estimate["lines"]):
                    key = f"{PREFIX}edit_{view['impact']['meta']['result_id']}_{i}_{j}"
                    st.write(f"**{line['role']} · {line['task']}**")
                    cols = st.columns([1, 3])
                    md = cols[0].number_input("공수(MD)", min_value=0.0, step=0.5, value=float(line["md"]), key=key + "_md")
                    assumption = cols[1].text_input("가정", value=line["assumption"], key=key + "_assumption")
                    lines.append({**line, "md": md, "assumption": assumption})
                edits.append({"item_ids": estimate["item_ids"], "lines": lines})
            reason = st.text_input("수정 사유", key=PREFIX + "edit_reason")
            clicked = st.form_submit_button("수정 반영·재계산", key=PREFIX + "edit_submit", disabled=changed)
        if clicked:
            try:
                new_view = workflow.edit(view["session_id"], EditImpact(
                    state_revision=view["state_revision"], target_result_id=view["impact"]["meta"]["result_id"],
                    estimates=edits, reason=reason))
                st.session_state[PREFIX + "view"] = new_view
                st.session_state.pop(PREFIX + "ack_schedule", None)
                st.session_state[PREFIX + "notice"] = "수정 내용을 반영하고 금액을 다시 계산했습니다. 최신 결과를 승인해주세요."
                st.rerun()
            except (ValueError, ToolInputError) as error:
                st.error(str(error))


def _review_form(workflow: Agent3Workflow, view: dict, changed: bool) -> None:
    st.subheader("3. PM 검토")
    if view["phase"] == "DRAFT_READY":
        st.success("승인한 산정 결과로 협의안 초안을 작성했습니다.")
        return
    if view["phase"] != "WAITING_IMPACT_APPROVAL":
        st.caption("산정 결과와 필수 정보를 확인한 뒤 승인할 수 있습니다.")
        return
    can_approve = all(e["status"] == "draft" and e["totals"] is not None for e in view["impact"]["estimates"])
    unknown_schedule = any(e["schedule"]["business_days"] is None for e in view["impact"]["estimates"])
    with st.form(PREFIX + "review_form"):
        reviewer = st.text_input("검토자 · 로컬 테스트용", value="local-test-pm", key=PREFIX + "reviewer")
        note = st.text_area("검토 메모 / 반려 사유", key=PREFIX + "review_reason", height=80)
        acknowledged = st.checkbox("일정 영향이 미산정이며 추가 확인이 필요함을 확인했습니다.", key=PREFIX + "ack_schedule") if unknown_schedule else True
        cols = st.columns(2)
        approve = cols[0].form_submit_button("승인하고 협의안 작성", type="primary", key=PREFIX + "approve", disabled=changed or not can_approve)
        reject = cols[1].form_submit_button("반려", key=PREFIX + "reject", disabled=changed)
    if approve or reject:
        try:
            new_view = workflow.review(view["session_id"], ReviewImpact(
                state_revision=view["state_revision"], target_result_id=view["impact"]["meta"]["result_id"],
                snapshot_hash=view["snapshot_hash"], request_id=uuid4().hex, reviewer_ref=reviewer,
                approved=approve, reason=note, acknowledge_unknown_schedule=acknowledged))
            st.session_state[PREFIX + "view"] = new_view
            st.session_state[PREFIX + "notice"] = "협의안 초안을 작성했습니다." if approve else "반려 사유를 기록했습니다. 공수를 수정하거나 검토 후 다시 승인할 수 있습니다."
            st.rerun()
        except (ValueError, ToolInputError) as error:
            st.error(str(error))


def render_agent3() -> None:
    st.subheader("변경 영향 검토 · 협의안")
    st.caption("입력 확인 → 영향 산정 → PM 확인·수정·승인 → 협의안 초안")
    st.subheader("1. 입력 확인")
    provider = st.selectbox("실행 모드", ["fake", "local", "bedrock"],
        format_func={"fake": "가상 응답 · 오프라인", "local": "Local LLM", "bedrock": "AWS Bedrock"}.get,
        key=PREFIX + "provider")
    model = base_url = api_key = ""
    region = env_settings.aws_region
    if provider == "local":
        base_url = st.text_input("Local base URL", value=env_settings.llm_base_url or "https://generativelanguage.googleapis.com/v1beta/openai", key=PREFIX + "base_url")
        model = st.text_input("모델 이름", value=env_settings.llm_model, key=PREFIX + "model")
        api_key = st.text_input("API key (선택)", type="password", key=PREFIX + "api_key",
            placeholder="설정된 환경 변수 사용" if env_settings.llm_api_key else "")
    elif provider == "bedrock":
        model = st.text_input("Bedrock model ID", value=env_settings.llm_model, key=PREFIX + "model")
        region = st.text_input("AWS region", value=region, key=PREFIX + "region")
    scenario = st.selectbox("테스트 시나리오", SCENARIOS, key=PREFIX + "scenario")
    if st.session_state.get(PREFIX + "previous_scenario") != scenario:
        st.session_state[PREFIX + "input"] = _sample_input(scenario)
        st.session_state[PREFIX + "previous_scenario"] = scenario
    with st.expander("에이전트 1·2에서 전달받은 입력 JSON", expanded=False):
        raw_input = st.text_area("Agent3 입력", height=260, key=PREFIX + "input")
    if provider == "fake":
        st.info("가상 응답·가상 공수·가상 단가로 흐름을 시험합니다. 에이전트 1·2와 실제 LLM을 실행하지 않습니다.")
        if PREFIX + "fake" not in st.session_state:
            st.session_state[PREFIX + "fake"] = _fake_response()
        with st.expander("가상 산정 응답 JSON"):
            fake_json = st.text_area("가상 응답", height=240, key=PREFIX + "fake")
    else:
        fake_json = ""
        st.caption("선택한 모델에 입력을 전달합니다. 단가표는 모델 프롬프트에 포함하지 않습니다.")
    try:
        preview = Agent3Input.model_validate_json(raw_input)
        st.write(f"**요청 {preview.context.case_id}** · 기준선 {preview.context.baseline_id}")
        for item in preview.items:
            st.write("- " + item.text)
        for constraint in preview.constraints:
            st.caption("조건: " + constraint.text)
    except ValueError:
        st.caption("입력 JSON을 확인해주세요.")
    fingerprint = hashlib.sha256(json.dumps([raw_input, provider, model, base_url, region, fake_json], ensure_ascii=False).encode()).hexdigest()
    if st.button("영향 산정 실행", type="primary", key=PREFIX + "run", width="stretch"):
        for key in ("view", "workflow", "fingerprint", "notice"):
            st.session_state.pop(PREFIX + key, None)
        st.session_state.pop(PREFIX + "ack_schedule", None)
        try:
            inp = Agent3Input.model_validate_json(raw_input)
            with st.spinner("작업·공수·금액을 검토하고 있습니다…"):
                workflow = Agent3Workflow(LlmAgent3(_client(provider, model, base_url, api_key, region, fake_json)))
                view = workflow.prepare(inp)
            st.session_state[PREFIX + "workflow"] = workflow
            st.session_state[PREFIX + "view"] = view
            st.session_state[PREFIX + "fingerprint"] = fingerprint
        except AgentFailure as error:
            st.error(f"{error.error.code}: {error.error.message}")
        except (ValueError, ToolInputError, OSError) as error:
            st.error(f"입력·설정을 확인해주세요: {type(error).__name__}")
    view = st.session_state.get(PREFIX + "view")
    if view is None:
        st.caption("영향 산정을 실행하면 결과와 PM 검토 버튼이 표시됩니다.")
        return
    notice = st.session_state.pop(PREFIX + "notice", None)
    if notice:
        st.success(notice)
    workflow = st.session_state[PREFIX + "workflow"]
    changed = fingerprint != st.session_state[PREFIX + "fingerprint"]
    _show_result(view, changed)
    _edit_form(workflow, view, changed)
    _review_form(workflow, view, changed)
    st.subheader("4. 협의안 초안")
    if view["draft"]:
        if changed:
            st.caption("아래 협의안은 변경 전 입력의 승인본입니다.")
        markdown = _draft_markdown(view["draft"])
        st.markdown(markdown)
        st.download_button("협의안 Markdown 다운로드", data=markdown,
            file_name=f"{view['impact']['context']['case_id']}_협의안_초안.md", mime="text/markdown", key=PREFIX + "download")
    else:
        st.caption("PM 승인 후 작성됩니다. 고객 발송·계약 체결·청구는 수행하지 않습니다.")
    with st.expander("결과 JSON · 버전 · 변경 이력"):
        st.json(view)
    st.caption("이 화면의 검토자는 로컬 테스트용입니다. 저장은 테스트 세션의 서버 메모리이며 실제 인증·CN DB 저장은 연결 전입니다.")


def main() -> None:
    st.set_page_config(page_title="에이전트 3 · 영향 산정", page_icon="🧵", layout="wide")
    st.title("에이전트 3")
    render_agent3()


if __name__ == "__main__":
    main()
