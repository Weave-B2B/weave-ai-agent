"""API 전용 요청 모델. 에이전트 간 공유 계약(schema/schemas.py)은 건드리지 않는다.

주의: PM 항목 확인 화면의 입력 스키마는 공통 스키마에 정의돼 있지 않다 (호출흐름 문서 '협의 사항').
아래 ConfirmItems는 임시안이며 팀 합의 후 확정한다.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from schema.schemas import Agent1Input, Answer, Item, PMReview


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(_Req):
    input: Agent1Input
    rate_card_id: str = Field(examples=["RC-SYNTH-2026H2-v1"])


class ConfirmItems(_Req):
    kind: Literal["items"]
    state_revision: int
    items: list[Item]


class SubmitPMReview(_Req):
    kind: Literal["pm_review"]
    state_revision: int
    review: PMReview
    new_evidence: bool = False   # True면 ②부터 재실행


class LeadDecision(_Req):
    kind: Literal["lead"]
    state_revision: int
    approved: bool
    reason: str = ""


ResumeRequest = Annotated[Union[ConfirmItems, SubmitPMReview, LeadDecision], Field(discriminator="kind")]


class AnswerRequest(_Req):
    state_revision: int
    answer: Answer


class RetryRequest(_Req):
    state_revision: int
