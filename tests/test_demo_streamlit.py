from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.demo_streamlit import (
    GOOGLE_AI_STUDIO_BASE_URL,
    _fake_draft_template,
    _local_defaults,
)
from app.agents.agent1.draft import Agent1Draft
from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[1]


def test_fake_demo_template_is_valid_and_grounded_in_input() -> None:
    """Keep the offline demo response schema-valid and source-grounded."""
    message = "로그인 화면에 소셜 로그인 버튼을 추가해주세요."

    draft = Agent1Draft.model_validate_json(_fake_draft_template(message))

    assert len(draft.items) == 1
    assert draft.items[0].text == message
    assert draft.items[0].quotes == [message]


def test_local_demo_uses_google_compatible_url_and_env_model_without_exposing_key(monkeypatch) -> None:
    """Read model and key from environment-backed settings without displaying the key."""
    monkeypatch.setattr(
        "app.demo_streamlit.env_settings",
        Settings(
            _env_file=None,
            llm_model="gemini-3.5-flash-lite",
            llm_api_key="secret-test-key",
        ),
    )

    base_url, model, has_api_key = _local_defaults()

    assert base_url == GOOGLE_AI_STUDIO_BASE_URL
    assert model == "gemini-3.5-flash-lite"
    assert has_api_key is True
    assert "secret-test-key" not in repr((base_url, model, has_api_key))


def test_streamlit_demo_renders_tabs_and_runs_agent1_with_fake_provider() -> None:
    """Smoke-test visible tabs and an end-to-end offline Agent1 run."""
    app = AppTest.from_file(str(ROOT / "app" / "demo_streamlit.py")).run()

    assert not app.exception
    assert "① Agent1 · 요청 구조화" in [tab.label for tab in app.tabs]
    assert "② Agent2 · 범위 판단" in [tab.label for tab in app.tabs]
    assert "③ Agent3 · 영향 산정" in [tab.label for tab in app.tabs]

    app.button[0].click().run(timeout=10)

    assert not app.exception
    assert any("처리 완료" in alert.value for alert in app.success)
    assert any("중간 처리 보기" in expander.label for expander in app.expander)
    assert any("Agent2 화면은 아직 구현하지 않았습니다." == caption.value for caption in app.caption)
    assert any(button.label == "영향 산정 실행" for button in app.button)
