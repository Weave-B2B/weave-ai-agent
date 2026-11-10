"""Run-scoped execution primitives for Agent 1 and Agent 2. They do not read or persist run state."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.agent1_routes import _run as run_agent1
from app.api.agent2_routes import _run as run_agent2
from schema.schemas import Agent1Input, Agent1Output, Agent2Input, Agent2Output


class _ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Agent1ExecutionRequest(_ExecutionModel):
    expected_state_revision: int = Field(ge=0)
    trigger: Literal["orchestrator", "manual", "retry"]
    input: Agent1Input


class Agent2ExecutionRequest(_ExecutionModel):
    expected_state_revision: int = Field(ge=0)
    trigger: Literal["orchestrator", "manual", "retry"]
    input: Agent2Input


class Agent1ExecutionResponse(_ExecutionModel):
    run_id: str
    based_on_state_revision: int
    trigger: Literal["orchestrator", "manual", "retry"]
    stage: Literal["agent1"] = "agent1"
    output: Agent1Output


class Agent2ExecutionResponse(_ExecutionModel):
    run_id: str
    based_on_state_revision: int
    trigger: Literal["orchestrator", "manual", "retry"]
    stage: Literal["agent2"] = "agent2"
    output: Agent2Output


router = APIRouter(prefix="/api/v2/runs", tags=["Run Agent Execution"])


@router.post("/{run_id}/agent1/items", response_model=Agent1ExecutionResponse)
def execute_agent1(run_id: str, req: Agent1ExecutionRequest) -> Agent1ExecutionResponse:
    output = run_agent1(req.input)
    return Agent1ExecutionResponse(
        run_id=run_id,
        based_on_state_revision=req.expected_state_revision,
        trigger=req.trigger,
        output=output,
    )


@router.post("/{run_id}/agent2/assessments", response_model=Agent2ExecutionResponse)
def execute_agent2(run_id: str, req: Agent2ExecutionRequest) -> Agent2ExecutionResponse:
    output = run_agent2(req.input)
    upstream_result_ids = list(output.meta.upstream_result_ids)
    if req.input.a1_result_id and req.input.a1_result_id not in upstream_result_ids:
        upstream_result_ids.append(req.input.a1_result_id)
    if upstream_result_ids != output.meta.upstream_result_ids:
        output = output.model_copy(
            update={
                "meta": output.meta.model_copy(
                    update={"upstream_result_ids": upstream_result_ids}
                )
            }
        )
    return Agent2ExecutionResponse(
        run_id=run_id,
        based_on_state_revision=req.expected_state_revision,
        trigger=req.trigger,
        output=output,
    )
