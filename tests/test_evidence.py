"""근거 출처 공통 구조: 원문·계약 문서·진행 자료를 구분하고, 범위 판단에는 계약 근거만 허용한다."""
import pytest
from pydantic import ValidationError

from app.orchestration.evidence import (
    NonContractEvidence, contract_only, parse_basis_ref, parse_basis_refs, require_contract,
)
from schema.schemas import EvidenceRef

MESSAGE = "안녕하세요. 그 부분 바꿔서 반영해 주세요."


def test_message_span_becomes_request_evidence_with_quote():
    ref = parse_basis_ref("message_text[7:19]", message_text=MESSAGE)
    assert (ref.source_type, ref.system) == ("request", "message")
    assert ref.title == MESSAGE[7:19].strip()


def test_message_span_outside_text_keeps_ref_without_quote():
    ref = parse_basis_ref("message_text[5:999]", message_text=MESSAGE)
    assert ref.source_type == "request" and ref.title is None


def test_doc_section_becomes_contract_evidence_with_title():
    ref = parse_basis_ref("FSP-TB-001-v2.0:F-11", doc_titles={"FSP-TB-001-v2.0": "기능 명세서"})
    assert (ref.source_type, ref.system, ref.title) == ("contract", "doc", "기능 명세서 · F-11")


def test_doc_outside_known_baseline_is_not_contract_evidence():
    assert parse_basis_ref("OTHER-001:F-1", doc_titles={"FSP-TB-001-v2.0": "기능 명세서"}) is None
    # 기준선 목록을 모르면(조회 실패) 문서 ID로 남긴다
    assert parse_basis_ref("OTHER-001:F-1", doc_titles={}).title == "OTHER-001 · F-1"


@pytest.mark.parametrize("raw, system", [
    ("TRIP-123", "jira"), ("Weave-B2B/weave-ai-agent#88", "github"), ("a1b2c3d4e5f6", "github"),
])
def test_jira_and_github_refs_are_activity_evidence(raw, system):
    ref = parse_basis_ref(raw)
    assert (ref.source_type, ref.system) == ("activity", system)


@pytest.mark.parametrize("raw", ["", "QA", "임의 문장"])
def test_unknown_formats_are_not_converted(raw):
    assert parse_basis_ref(raw) is None


def test_parse_many_drops_unknown_and_duplicates():
    refs = parse_basis_refs(["FSP-A:F-1", "FSP-A:F-1", "QA", "TRIP-1"])
    assert [r.ref for r in refs] == ["FSP-A:F-1", "TRIP-1"]


def test_source_type_and_system_must_match():
    with pytest.raises(ValidationError):
        EvidenceRef(source_type="contract", system="jira", ref="TRIP-1")
    with pytest.raises(ValidationError):
        EvidenceRef(source_type="activity", system="doc", ref="FSP-A:F-1")


def test_scope_judgment_accepts_only_contract_evidence():
    contract = parse_basis_ref("FSP-A:F-1")
    assert require_contract([contract]) == [contract]
    for other in (parse_basis_ref("TRIP-1"), parse_basis_ref("message_text[0:5]", message_text=MESSAGE)):
        with pytest.raises(NonContractEvidence, match="계약 문서 근거만"):
            require_contract([contract, other])
    assert contract_only([contract, parse_basis_ref("TRIP-1")]) == [contract]
