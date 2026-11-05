# Agent 2 오케스트레이션 연동 계약 v1

- 작성일: 2026-11-05
- 계약 식별자: `a2-orchestration/1.0.0-draft`
- 상태: **Agent 2 측 제안. 팀 공통 규격으로 확정되지 않았음.**
- 범위: 오케스트레이터 에이전트 ↔ 공통 실행 서버 ↔ Agent 2 도구. Agent 1·3 내부 구현을 변경하지 않는다.
- 기준: [통합 분석 설계](../prototype/integrated-request-analysis-plan.md) 4~6장, [Agent 1 연동 계약](../agent1/orchestration-integration-contract-v1.md), [Agent 3 연동 계약](../agent3/orchestration-integration-contract-v1.md). 용어는 Agent 3 계약과 같다.

## 1. 현재 구현 상태

| 구분 | 상태 |
|---|---|
| 범위 판단(`change_request`·`defect`·`issue_report`) | 구현. 도구 사용 에이전트 `ToolAgent2` + 인용 검증 가드 |
| 범위 확인 문의(`confirmation`) | 구현. 판정 라벨 + `confirmation_answer` |
| 조건부 후속(`conditional_followup`) | 판단 입력으로 전달. 전환은 호스트가 한다(4장) |
| 유효 기준선 검사 | 구현. 대체된 기준선은 `BASELINE_SUPERSEDED`로 거절 |
| 오케스트레이터 도구 adapter | 구현 `app/agents/agent2/orchestrator_adapter.py`. 공통 엔진·저장소에는 미연결 |
| Agent 1 잠정 질문 문서 해소 | **미구현. 입력 필드 합의 필요(7장)** |

Agent 2는 항목별로 독립 판단하므로 Agent 3과 달리 세션이 없다. 결과는 호스트가 저장한 최신 `Agent2Output`이고, 바뀐 항목만 다시 판단해 합친다.

## 2. 역할과 권한

| 주체 | 책임 |
|---|---|
| 오케스트레이터 에이전트 | 판단할 항목 ID와 추가 조사 관점(`focus`) 선택 |
| 공통 실행 서버 | 케이스·원문·기준선·답변·최신 Agent 2 결과·질문 관리, 입력 구성, revision 검증 |
| Agent 2 | 기준선 문서 조사, 범위 판정과 근거, 문의 답변, 필요한 질문 |
| PM | 판정 확정. Agent 2 판정은 제안이다 |

기준선·프로젝트·원문·답변·모델은 LLM 도구 인자로 받지 않는다.

## 3. 오케스트레이터 에이전트 도구

| 도구 | args | 처리 |
|---|---|---|
| `assess_scope` | `{item_ids: string[], focus?: string(≤200)}` | 범위 판단·문의 답변·추가 조사 |

```json
{
  "action": "assess_scope",
  "args": {"item_ids": ["CR-A-001-1"], "focus": "변경합의서의 사진 개수 조건"},
  "reason": "리뷰 기능이 이미 승인된 변경인지 확인합니다."
}
```

- 정의되지 않은 인자(예: `baseline_id`)는 거부한다.
- 대상은 intent가 `change_request`·`confirmation`·`defect`·`issue_report`인 현재 케이스 항목만. `status_check` 등은 `INVALID_ARGUMENT`.
- 같은 입력으로 판단한 항목은 모델을 다시 부르지 않고 저장된 결과를 돌려준다. 입력 = 항목·기준선·원문·제약·답변·Agent 1 결과 ID·실행 설정.
- `focus`가 있으면 입력이 같아도 그 관점으로 다시 조사한다. `focus`는 조사 범위일 뿐 판단 규칙을 바꾸지 않는다.
- 일부 항목만 요청하면 그 항목만 다시 판단하고 나머지 항목의 판정·질문은 유지한다.

## 4. 판정과 산정 연결

| label | 의미 | Agent 3 산정 |
|---|---|---|
| `in_scope` | 기준선 포함 기능 또는 그 결함 | 제외 |
| `out_of_scope` | 제외·미열거 신규 기능 | 대상(조건 충족 시) |
| `approved_change` | 기존 변경합의서로 승인됨. `approved_change_ref` 필수 | 제외(재청구 금지) |
| `unclear` | 근거·사실 부족 | 제외. 조사·질문 대상 |

- **산정 가능(`estimable`)** = `out_of_scope` + 인용 검증된 근거 1개 이상 + 해당 항목에 열린 블로킹 질문 없음. (#22 잠정 산정 조건)
  - `confirmation` 항목은 `conditional_followup`이 있을 때만 산정 가능. 묻기만 한 문의는 범위 밖이어도 견적을 만들지 않는다.
- 범위 밖 판단 전 기준선의 변경합의서마다 승인 대상을 확인한다. 확인 없이 단계 상한에 걸린 `out_of_scope`는 `unclear`로 내린다.
- `confirmation` 항목: label이 문의한 기능의 포함 여부 판정이고 `confirmation_answer`에 고객 답변 요약을 담는다. `unclear`면 답변 없음. 고객이 이번에 무엇을 원하는지는 문서로 대신 답하지 않는다.
- `conditional_followup`이 있는 확인 항목이 `estimable`이면 호스트가 잠정 변경 대상으로 전환할 수 있다(Agent 1 계약). Agent 2는 전환하지 않는다.

## 5. 결과

```json
{
  "contract_version": "a2-orchestration/1.0.0-draft",
  "operation_id": "op-...",
  "status": "ready | needs_input | error",
  "requested_item_ids": ["CR-A-001-1"],
  "processed_item_ids": ["CR-A-001-1"],
  "result_ids": ["a2r-..."],
  "summary": "요청한 범위 판단을 완료했습니다.",
  "items": [{"item_id": "CR-A-001-1", "intent": "change_request", "label": "approved_change",
             "evidence_level": "strong", "verified_evidence": 2, "approved_change_ref": "CO-A-002-v1.0",
             "confirmation_answer": null, "conditional_followup": false, "estimable": false}],
  "needs": [],
  "error": null
}
```

- `items`는 오케스트레이터 에이전트에 주는 요약이다. 근거 인용 전문은 넣지 않는다. 전체 결과는 호스트가 `result_ids`로 조회한다.
- `processed_item_ids`가 비어 있으면 저장된 판단을 재사용한 것이다.
- `status=needs_input`은 열린 블로킹 질문이 있을 때만이다.

### needs

| kind | target | 의미 | 오케스트레이터 다음 행동 |
|---|---|---|---|
| `client_input` | `pm`·`client` | Agent 2 질문. `question_id` 포함 | 공통 질문 목록에 병합, 답변 후 재호출 |
| `more_evidence` | `agent2` | 질문 없이 근거 부족(`unclear`) | 다른 `focus`로 재조사 또는 미완료 검토 |
| `resplit` | `agent1` | 포함·제외 기능이 한 항목에 섞임 | Agent 1에 해당 항목만 재분리 요청 후 바뀐 항목만 재호출 |

## 6. 호스트 상태와 오류

호스트는 `Agent2StatePort`를 구현한다. 약속은 Agent 3 포트와 같다.

| 메서드 | 조건 |
|---|---|
| `load(case_id)` | 분리된 `Agent2CaseSnapshot`: revision, context, 원문, 항목, 제약, 답변, 최신 결과, 항목별 입력 fingerprint |
| `read_operation(case_id, operation_id)` | 저장된 영수증 |
| `commit(case_id, expected_revision, update, operation_id, receipt)` | revision 비교·결과 반영·영수증 저장을 원자적으로. `update=None`이면 영수증만 |

`Agent2Update`는 합친 `a2_result`, 항목별 fingerprint, 결과 연결(`agent1`·`agent2`), 열린 질문, 해소·대체된 질문 ID를 담는다. 질문 ID는 이전 질문과 겹치지 않게 다시 붙이며, 다시 판단한 항목에서 같은 질문이 또 나오면 이전 질문은 `superseded`(해소 아님)로 구분한다.

| code | retryable | 처리 |
|---|---|---|
| `INVALID_ARGUMENT` | false | 인자 수정 |
| `OPERATION_CONFLICT` | false | 같은 작업 ID에 다른 인자. 새 작업 ID |
| `STATE_CONFLICT` | false | 최신 상태 재조회 후 재계획 |
| `BASELINE_SUPERSEDED` | false | 현재 기준선으로 다시 요청(호스트) |
| `INVALID_INPUT` | false | 입력 오류(기준선 누락 등) |
| `MODEL_TIMEOUT`·`RETRIEVAL_UNAVAILABLE` | true | 호출 예산 안에서 재시도 |
| `OUTPUT_INVALID` | true | 유한 횟수 재시도, 초과 시 미완료 검토 |

오류 envelope는 Agent 3과 같은 `status=error`, `error={code,message,retryable,stage}`다.

## 7. 합의가 필요한 항목 (제안)

1. **Agent 1 잠정 질문 문서 해소** (#22 5장). 제안: `Agent2Input`에 선택 필드 `pending_questions: list[Question]`을 추가하고, Agent 2가 질문별로 `resolution: "resolved_by_document" | "needs_answer" | "not_found"`와 근거(문서·조항·인용)를 반환한다. 고객의 현재 의도를 묻는 질문은 문서로 해소하지 않는다. 공통 스키마 변경이라 Agent 1 담당자와 합의 후 구현한다.
2. **`confirmation_answer` 필드**: 공통 `Assessment`의 선택 필드로 추가했다. 기존 결과와 호환된다.
3. **항목 분리 표식**: 질문 `field="item_split"`을 `resplit` 신호로 쓴다. Agent 1 재분리 입력 형식과 맞춰야 한다.
4. **결과 ID 발급**: 현재 adapter가 `a2r-<hash(case, operation)>`로 발급한다. 공통 서버가 발급하면 그 값으로 바꾼다.
5. **답변 영향 범위**: 답변이 어떤 항목에 영향을 주는지 Agent 2가 알 수 없어 답변이 바뀌면 요청된 항목을 모두 다시 판단한다. `analyze_answer`의 `affected_item_ids`를 넘겨받으면 줄일 수 있다.

## 8. 사용 진입점

```python
from app.agents.registry import build_agent2
from app.agents.agent2.orchestrator_adapter import (
    Agent2OrchestratorAdapter, Agent2StatePort, tool_schemas, action_schema,
)

def bind_agent2(port: Agent2StatePort, resolve_llm):
    return Agent2OrchestratorAdapter(port, lambda snapshot: build_agent2(resolve_llm(snapshot.execution_config_revision)))

result = adapter.execute(case_id, expected_state_revision, operation_id, call)   # 앞의 세 인자는 서버가 주입
```

검증: `tests/test_agent2_orchestrator_adapter.py`. 메모리 포트는 테스트에만 둔다. 실제 LLM으로 adapter 경로를 실행한 검증은 아직 없다.
