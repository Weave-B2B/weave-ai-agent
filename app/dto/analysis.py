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


class AnalysisResultDTO(StrictDTO):
    analysis_id: str
    case_id: str
    contract_request: ContractRequestDTO
    structured_request: Optional[StructuredRequestDTO] = None
    scope_analysis: Optional[ScopeAnalysisDTO] = None
    cost_estimation: Optional[CostEstimationDTO] = None
    workflow: WorkflowState
