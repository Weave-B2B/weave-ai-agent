"""Backend 접수 채널 → Agent1Input.channel 변환 (#75: OTHER 채널 CR 조회 실패 수정)."""
import pytest

from app.orchestration.backend_project_bridge import agent1_channel
from schema.schemas import Agent1Input


@pytest.mark.parametrize("backend, expected", [
    ("EMAIL", "email"), ("email", "email"), ("OTHER", "manual"), ("MEETING", "manual"),
    ("PHONE", "manual"), ("MESSENGER", "manual"), ("", "manual"), (None, "manual"),
])
def test_backend_channel_maps_to_agent1_channel(backend, expected):
    assert agent1_channel(backend) == expected
    Agent1Input.model_validate({"context": {"project_id": "P", "case_id": "C"}, "received_at": "2026-11-24",
                                "sender_role": "CLIENT", "message_text": "원문", "channel": agent1_channel(backend)})
