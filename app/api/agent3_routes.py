"""Agent3 MVP endpoints for trusted backend integration, separate from shared engine."""

from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.workflow import Agent3Workflow, EditImpact, ReviewImpact, GenerateDraft, WorkflowConflict
from app.agents.base import AgentFailure
from app.core.config import settings
from app.llm.client import build_llm
from app.tools.errors import ToolInputError
from schema.schemas import Agent3Input
from app.agents.agent3.contracts import ProvisionalAgent3Input
from app.agents.agent3.orchestration import (
    Agent3Orchestration, DraftDocuments, Reestimate, Recalculate, FinalReview, EditDocument,
)

router = APIRouter(prefix="/agent3", tags=["Agent3 MVP"])


@lru_cache
def get_workflow() -> Agent3Workflow:
    llm = build_llm(settings)
    if settings.llm_provider == "fake":
        from app.llm.fake import FakeLlm
        draft_llm = FakeLlm([{"narrative": "안녕하세요. 변경 요청의 검토 내용을 전달드립니다.\n\n[[APPROVED_FACTS]]\n\n내용을 검토하신 뒤 함께 협의 부탁드립니다. 감사합니다."}] * 200)
        return Agent3Workflow(LlmAgent3(llm), draft_llm=draft_llm)
    return Agent3Workflow(LlmAgent3(llm))


def _guard(call):
    try:
        return call()
    except KeyError:
        raise HTTPException(404, "Agent3 session not found") from None
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except AgentFailure as exc:
        raise HTTPException(502, exc.error.model_dump(mode="json")) from None
    except (ToolInputError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "configured Agent3 data is unavailable") from None


@router.post("/impacts")
def estimate(inp: Agent3Input):
    return _guard(lambda: get_workflow().prepare(inp))


@router.get("/impacts/{session_id}")
def get_impact(session_id: str):
    return _guard(lambda: get_workflow().get(session_id))


@router.post("/impacts/{session_id}/edit")
def edit_impact(session_id: str, req: EditImpact):
    return _guard(lambda: get_workflow().edit(session_id, req))


@router.post("/impacts/{session_id}/review")
def review_impact(session_id: str, req: ReviewImpact):
    return _guard(lambda: get_workflow().review(session_id, req))


@router.post("/impacts/{session_id}/draft")
def generate_draft(session_id: str, req: GenerateDraft):
    return _guard(lambda: get_workflow().generate_draft(session_id, req))


@lru_cache
def get_orchestration() -> Agent3Orchestration:
    workflow = get_workflow()
    return Agent3Orchestration(workflow.agent, draft_llm=workflow.draft_llm)


@router.post("/orchestration/impacts")
def provisional_impact(inp: ProvisionalAgent3Input):
    return _guard(lambda: get_orchestration().estimate_impact(inp))


@router.get("/orchestration/{session_id}")
def orchestration_state(session_id: str):
    return _guard(lambda: get_orchestration().get(session_id))


@router.post("/orchestration/{session_id}/documents")
def provisional_documents(session_id: str, req: DraftDocuments):
    return _guard(lambda: get_orchestration().draft_documents(session_id, req))


@router.post("/orchestration/{session_id}/reestimate")
def reestimate_items(session_id: str, req: Reestimate):
    return _guard(lambda: get_orchestration().reestimate(session_id, req))


@router.post("/orchestration/{session_id}/recalculate")
def recalculate_items(session_id: str, req: Recalculate):
    return _guard(lambda: get_orchestration().recalculate(session_id, req))


@router.post("/orchestration/{session_id}/review")
def final_review(session_id: str, req: FinalReview):
    return _guard(lambda: get_orchestration().review(session_id, req))


@router.post("/orchestration/{session_id}/documents/edit")
def edit_provisional_document(session_id: str, req: EditDocument):
    """Trusted PM caller only; not an orchestrator-callable capability."""
    return _guard(lambda: get_orchestration().edit_document(session_id, req))


class ProposalDraftRequest(BaseModel):
    case_id: str
    project_id: str
    review_version_id: str
    request_items: list[str] = []
    tasks: list[str] = []
    total_price: int = 0
    currency: str = "KRW"
    model: str = "mock-llm-v1"


class ProposalDraftResponse(BaseModel):
    subject: str
    body: str
    model: str


@router.post("/proposal-draft", response_model=ProposalDraftResponse)
def generate_proposal_draft(req: ProposalDraftRequest):
    """백엔드 E 파트(협의안 발급)에서 호출하는 제안서 초안 문안 생성 API."""
    subject = f"[{req.case_id}] 변경 요청 검토 및 협의 제안서"
    items_desc = "\n".join(f"- {it}" for it in req.request_items) if req.request_items else "- 추가 변경 항목"
    tasks_desc = "\n".join(f"- {t}" for t in req.tasks) if req.tasks else "- 제안 작업 수행"
    price_str = f"{req.total_price:,} {req.currency}" if req.total_price else "별도 협의"

    body = (
        f"안녕하세요. 요청해주신 변경 요청 건({req.case_id})에 대한 검토 내용 및 협의 제안서를 전달드립니다.\n\n"
        f"[요청 항목]\n{items_desc}\n\n"
        f"[수행 작업]\n{tasks_desc}\n\n"
        f"[제안 금액]\n{price_str} (VAT 별도)\n\n"
        "상기 제안 내용을 검토하신 후 의견이나 승인 여부를 회신해 주시기 바랍니다.\n"
        "감사합니다."
    )
    return ProposalDraftResponse(subject=subject, body=body, model=req.model)

