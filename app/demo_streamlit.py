"""Streamlit test UI for Agent1 and Agent3 workflows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypeVar

import streamlit as st
from pydantic import BaseModel

from app.agents.agent1.agent import LlmAgent1
from app.agents.agent1.prompts import SYSTEM
from app.agents.base import AgentFailure
from app.core.config import Settings, settings as env_settings
from app.llm.base import LlmClient, LlmResult
from app.llm.client import FakeLlm, LlmConfigError, build_llm
from app.tools.errors import ToolInputError
from schema.schemas import Agent1Input

T = TypeVar("T", bound=BaseModel)
GOOGLE_AI_STUDIO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class _RecordingLlm:
    """Capture one model request and validated draft for display in the demo."""

    def __init__(self, client: LlmClient) -> None:
        """Wrap an LLM client to expose only this run's request and parsed draft."""
        self.client = client
        self.system: str | None = None
        self.user: str | None = None
        self.schema_name: str | None = None
        self.tool_name: str | None = None
        self.draft: dict[str, Any] | None = None

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tool_name: str = "submit",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResult[T]:
        """Forward a structured request and keep its safe-to-display result."""
        self.system = system
        self.user = user
        self.schema_name = schema.__name__
        self.tool_name = tool_name
        result = self.client.complete_structured(
            system=system,
            user=user,
            schema=schema,
            tool_name=tool_name,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.draft = result.parsed.model_dump(mode="json")
        return result


def _fake_draft_template(message_text: str) -> str:
    """Create a transparent one-item fake response grounded in the entered text."""
    items = []
    if message_text.strip():
        items.append(
            {
                "item_type": "function",
                "intent": "change_request",
                "text": message_text.strip(),
                "quotes": [message_text.strip()],
                "inferred": False,
            }
        )
    return json.dumps({"items": items, "questions": []}, ensure_ascii=False, indent=2)


def _make_settings(
    *,
    provider: str,
    model: str,
    local_base_url: str,
    api_key: str,
    region: str,
) -> Settings:
    """Build isolated runtime settings from the current demo controls."""
    return Settings(
        _env_file=None,
        agent_mode="llm",
        llm_provider=provider,
        llm_model=model,
        llm_base_url=local_base_url,
        llm_api_key=api_key,
        aws_region=region,
    )


def _local_defaults() -> tuple[str, str, bool]:
    """Resolve Local provider defaults without exposing a configured API key."""
    return (
        env_settings.llm_base_url or GOOGLE_AI_STUDIO_BASE_URL,
        env_settings.llm_model,
        bool(env_settings.llm_api_key),
    )


def _render_agent1() -> None:
    """Render the Agent1 input form, provider settings, and structured result."""
    st.subheader("요청 구조화")
    st.caption("고객 요청을 항목과 확인 질문으로 구조화합니다. 계약 범위 판단은 수행하지 않습니다.")

    provider = st.selectbox(
        "LLM provider",
        ["fake", "local", "bedrock"],
        format_func={
            "fake": "Fake (오프라인 예시)",
            "local": "Local LLM",
            "bedrock": "AWS Bedrock",
        }.get,
    )

    model = ""
    local_base_url = ""
    api_key = ""
    region = "ap-northeast-2"
    if provider == "local":
        default_base_url, default_model, has_env_api_key = _local_defaults()
        local_base_url = st.text_input(
            "Local base URL",
            value=default_base_url,
        )
        model = st.text_input("모델 이름", value=default_model)
        api_key = st.text_input(
            "API key (선택)",
            type="password",
            placeholder="환경 변수 LLM_API_KEY 사용" if has_env_api_key else "",
        )
    elif provider == "bedrock":
        model = st.text_input("Bedrock model ID")
        region = st.text_input("AWS region", value=region)
        st.caption("AWS 자격 증명은 기본 AWS credential chain에서 가져옵니다.")

    project_id = st.text_input("프로젝트 ID", value="DEMO")
    case_id = st.text_input("케이스 ID", value="DEMO-001")
    baseline_id = st.text_input("기준선 ID (선택)", placeholder="예: P-COM-101:BL-2")
    subject = st.text_input("메일 제목 (선택)")
    sender_role = st.text_input("보낸 사람 역할", value="고객")
    message_text = st.text_area(
        "고객 요청 원문",
        value="로그인 화면에 소셜 로그인 버튼을 추가하고, 가능하면 버튼 문구도 영어로 보여주세요.",
        height=150,
        key="agent1_message_text",
    )

    fake_draft_json = ""
    if provider == "fake":
        previous_source = st.session_state.get("_agent1_fake_source")
        if previous_source != message_text:
            st.session_state["_agent1_fake_source"] = message_text
            st.session_state["agent1_fake_draft"] = _fake_draft_template(message_text)
        fake_draft_json = st.text_area(
            "Fake 응답 초안 JSON",
            key="agent1_fake_draft",
            height=220,
            help="실제 LLM 응답 대신 사용할 초안입니다. 필요하면 수정해 테스트할 수 있습니다.",
        )
        st.info("Fake는 LLM 추론이 아닙니다. 입력 원문을 항목 초안으로 돌려주는 오프라인 예시입니다.")

    run_clicked = st.button("Agent1 실행", type="primary", width="stretch")
    if not run_clicked:
        return
    if not message_text.strip():
        st.warning("요청 원문을 입력해 주세요.")
        return

    try:
        input_data = Agent1Input.model_validate(
            {
                "context": {
                    "project_id": project_id.strip(),
                    "case_id": case_id.strip(),
                    "request_revision": 1,
                    "baseline_id": baseline_id.strip() or None,
                    "analysis_id": f"DEMO-{case_id.strip()}",
                },
                "received_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "sender_role": sender_role,
                "subject": subject or None,
                "message_text": message_text,
            }
        )
        settings = _make_settings(
            provider=provider,
            model=model.strip(),
            local_base_url=local_base_url.strip(),
            api_key=api_key or env_settings.llm_api_key,
            region=region.strip(),
        )
        if provider == "fake":
            responses = [json.loads(fake_draft_json)]
            client: LlmClient = FakeLlm(responses=responses)
        else:
            client = build_llm(settings)

        recording_client = _RecordingLlm(client)
        output = LlmAgent1(recording_client).run(input_data)
    except AgentFailure as error:
        st.error(f"{error.error.code}: {error.error.message}")
        return
    except (LlmConfigError, ToolInputError, ValueError) as error:
        st.error(f"{type(error).__name__}: {error}")
        return

    st.success(f"처리 완료 · {output.status}")
    st.markdown("#### 조립된 결과")
    st.json(output.model_dump(mode="json"), expanded=True)

    if output.items:
        st.markdown("#### 항목과 원문 근거")
        st.dataframe(
            [
                {
                    "ID": item.item_id,
                    "순번": item.display_no,
                    "유형": item.item_type,
                    "의도": item.intent,
                    "항목": item.text,
                    "근거 구간": ", ".join(
                        f"{span.start}:{span.end} — {span.text}" for span in item.spans
                    ),
                    "후보 기능": ", ".join(item.feature_candidates),
                }
                for item in output.items
            ],
            hide_index=True,
            width="stretch",
        )

    with st.expander("중간 처리 보기"):
        st.markdown("**① 처리 순서**")
        st.code("요청 입력 → LLM 초안 → quote 위치 계산·ID 조립 → Agent1Output 검증", language="text")
        if recording_client.user is not None:
            st.markdown("**LLM 입력 프롬프트**")
            st.code(recording_client.user, language="text")
        if recording_client.draft is not None:
            st.markdown("**구조화된 초안**")
            st.json(recording_client.draft, expanded=True)
        st.markdown("**조립 리포트 및 호출 메타데이터**")
        st.json(output.meta.model_dump(mode="json"), expanded=True)


def main() -> None:
    """Render Agent1 and Agent3; keep Agent2 as a separate placeholder."""
    st.set_page_config(
        page_title="Scope Agent · Demo",
        page_icon="🧵",
        layout="wide",
    )
    st.title("Scope Agent")
    st.caption("Agent1 요청 구조화 · Agent3 영향 산정·PM 승인·협의안")

    agent1_tab, agent2_tab, agent3_tab = st.tabs(
        ["① Agent1 · 요청 구조화", "② Agent2 · 범위 판단", "③ Agent3 · 영향 산정"]
    )
    with agent1_tab:
        _render_agent1()
    with agent2_tab:
        st.caption("Agent2 화면은 아직 구현하지 않았습니다.")
    with agent3_tab:
        from app.demo_agent3 import render_agent3
        render_agent3()


if __name__ == "__main__":
    main()
