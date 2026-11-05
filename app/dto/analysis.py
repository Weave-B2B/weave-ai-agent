from typing import Optional

from pydantic import BaseModel, ConfigDict

from schema.schemas import (
    Agent1Input,
    Agent1Output,
    Agent2Output,
    Agent3Output,
    Context,
    WorkflowState,
)


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ContractRequestDTO(StrictDTO):
    input: Agent1Input


class StructuredRequestDTO(StrictDTO):
    request: ContractRequestDTO
    structured: Agent1Output


class ScopeAnalysisDTO(StrictDTO):
    request: ContractRequestDTO
    structured: Agent1Output
    scope: Agent2Output


class CostEstimationDTO(StrictDTO):
    request: ContractRequestDTO
    structured: Agent1Output
    scope: Agent2Output
    cost: Agent3Output


class ScheduleReferenceDTO(StrictDTO):
    """일정·기한 조건 항목이 있을 때 계약 문서 기준의 현재 일정을 보여 주는 결정적 참고 정보."""

    item_ids: list[str]
    as_of: Optional[str] = None
    contract_end: str
    contract_end_ref: str
    applied_change_orders: list[str]
    milestones: list[dict]
    note: str


class AnalysisResultDTO(StrictDTO):
    analysis_id: str
    case_id: str
    contract_request: ContractRequestDTO
    structured_request: Optional[StructuredRequestDTO] = None
    scope_analysis: Optional[ScopeAnalysisDTO] = None
    cost_estimation: Optional[CostEstimationDTO] = None
    schedule_reference: Optional[ScheduleReferenceDTO] = None
    workflow: WorkflowState
