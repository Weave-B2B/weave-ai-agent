# 오케스트레이션·직접 실행 통합 DTO 수정 계획

- 작성일: 2026-11-10
- 상태: 진행 중 — 소유 구조 확정: **AI Server Orchestration + Backend State Management**. 구현 현황은 11장 참조
- 범위: Agent 1 항목화, Agent 2 범위 판정, Agent 3 공수 산출, Supervisor 기반 오케스트레이션, 자동실행·직접실행 화면
- 관련 문서: [오케스트레이션 설계](./orchestration-design.md)

## 1. 설계 목표

서비스는 기본적으로 **규칙 기반 Workflow + LLM Supervisor** 오케스트레이션을 제공하되, 사용자가 Agent 1·2·3을 직접 실행하고 결과를 수정한 뒤 필요한 단계를 다시 실행할 수 있어야 한다. 자동실행 화면과 직접실행 화면은 다른 분석을 만들지 않고 **같은 분석 run과 결과 이력**을 다룬다.

따라서 API는 두 종류의 진입을 제공한다.

1. **오케스트레이션 API**: Supervisor가 현재 Run 상태와 결과를 보고 다음 실행 대상과 실행 여부(`SupervisorDecision`)를 판단하고, 코드 기반 Runtime이 이를 검증·실행·상태 반영한다. 초기 구현은 Agent 1→2→3 고정 단계 구조 안에서 실행/생략/대기를 판단한다.
2. **Agent별 API**: 사용자가 특정 Agent를 직접 실행하거나 결과를 수정한다. 이 호출도 같은 run에 연결하고 의존 결과, revision, 무효화 규칙을 적용한다.

두 API 표면이 별도여도 run 상태, 결과 버전, 감사 이력, 식별자와 오류 모델은 공유해야 한다. Supervisor 출력은 실행 명령이 아니라 제안된 판단이며, Runtime이 선행조건·권한·revision·상태 전이 규칙을 코드로 검증한 후에만 수행한다. 필수 필드 검증, revision 검증, 재시도 정책, 상태 전이는 LLM에 맡기지 않는다.

**비목표**: Planner의 전체 작업 그래프 생성, 동적 작업 분해, 임의 task graph, 범용 병렬 실행, 범용 멀티에이전트 프레임워크, LangGraph 도입은 현재 필수 범위가 아니다.

## 2. 현재 상태 요약

### AI Agent 서버

| 구분 | 현재 DTO/API | 현황과 제한 |
|---|---|---|
| Agent 공통 계약 | `Context`, `Meta`, `Item`, `Question`, `Answer`, `AgentError`, `Evidence` | [공통 스키마](../../schema/schemas.py)는 엄격한 Pydantic 모델이며, Agent별 입력·출력의 공통 요소를 제공한다. |
| Agent 1 | `Agent1Input` → `Agent1Output` | 원문과 발신 정보에서 항목·질문을 만든다. 단독 실행 API는 [POST `/agent1/items`](../../app/api/agent1_routes.py)이며 결과를 이 API에서 저장하지 않는다. |
| Agent 2 | `Agent2Input` → `Agent2Output` | 대상 항목·제약·답변으로 판정한다. 단독 실행 API는 [POST `/agent2/assessments`](../../app/api/agent2_routes.py)이며 결과를 이 API에서 저장하지 않는다. |
| Agent 3 | `Agent3Input` → `Agent3Output` 및 세션 DTO | 입력에 `Agent2Output` 전체와 PM 검토가 들어간다. 기존 impact API와 별도 orchestration API가 있으며, Agent 3만 세션 상태를 관리한다. |
| 고정 오케스트레이션 | `WorkflowState`, `CaseRecord`, `RunRequest`, `ConfirmItems`, `SubmitPMReview` 등 | [엔진 API](../../app/api/routes.py)는 고정 `stage`/전이 모델이다. 케이스 상태는 현재 메모리 저장소에 있다. |
| 화면용 누적 응답 | `ContractRequestDTO`, `StructuredRequestDTO`, `ScopeAnalysisDTO`, `CostEstimationDTO`, `AnalysisResultDTO` | [AnalysisResultDTO](../../app/dto/analysis.py)는 단계마다 원 요청과 이전 결과를 중첩해 반복한다. Supervisor 기반 실행 API와 별도 상태 흐름이다. |

### Backend

백엔드에서 확인한 범위 분석 연동은 C 모듈이 `Agent2Input` 모양의 JSON을 생성하고 공통 AI 작업 저장소 G에 저장하는 흐름이다. [ScopeAnalysisInputBuilder](../../../weave-backend/src/main/java/com/weave/scope/analysis/ScopeAnalysisInputBuilder.java)는 Java의 완전한 Agent2 계약 DTO 대신 JSON을 구성하고, [ScopeAnalysisInput](../../../weave-backend/src/main/java/com/weave/common/ai/dto/ScopeAnalysisInput.java)는 이를 `JsonNode payload`로 감싼다. 결과는 [ScopeAnalysisResultReader](../../../weave-backend/src/main/java/com/weave/scope/analysis/ScopeAnalysisResultReader.java)가 JSON에서 C가 쓰는 범위 판정·근거만 읽어 [ScopeAnalysisResult](../../../weave-backend/src/main/java/com/weave/scope/analysis/ScopeAnalysisResult.java)로 투영한다.

확인한 Backend 코드에는 다음의 통합 타입이 없다.

- Agent 1·2·3의 전체 실행 요청/응답을 표현하는 공통 Java wire DTO
- 분석 Run 상태(Agent별 실행 상태, 현재 단계, 단계별 current/stale result 포인터)와 Supervisor 판단 DTO
- 직접실행과 오케스트레이션이 공유하는 편집·재실행 command DTO
- Agent 1·2·3 결과 및 사용자 수정 revision을 한 run에서 연결하는 공통 API DTO
- Agent 서버의 자체 `analysis_id`/`session_id`와 Backend의 `ai_analysis_id`/업무 검토 버전을 연결하는 공통 참조 계약

이는 Backend에 관련 데이터가 전혀 없다는 뜻이 아니다. 기존 `ai_analysis`는 JSON 입력 snapshot/result와 실행 상태를 보관하고, 각 도메인 모듈은 자체 응답 DTO를 가진다. 다만 전체 Agent 오케스트레이션 계약을 표현하는 타입과 연결은 현재 확인한 코드에 없다.

## 3. Agent별 현 DTO와 개선 방향

| 단계/개념 | 현재 DTO | 개선 DTO 방향 | 변경 이유 |
|---|---|---|---|
| 공통 실행 문맥 | `Context`: `project_id`, `case_id`, `request_revision`, `baseline_id`, `analysis_id` 등 | `RunRef` 또는 `ExecutionContext`: 위 도메인 문맥 + 명시적 `run_id`, `expected_state_revision`, 계약 버전. Backend ID와 AI ID의 매핑은 별도 정식 필드로 정의 | 모든 호출에 일관된 분석·동시성 문맥을 전달한다. 현재 `analysis_id` 하나로 AI run, Backend 분석, 업무 버전을 혼동하지 않는다. |
| Agent 1 입력 | `Agent1Input`: 원문, 수신 시각, 발신 정보, PM 메모, 답변 | `Agent1ExecuteRequest`: 원문·관련 답변·필요한 발신 맥락 + 공통 실행 envelope. 재실행 시 선택 항목/지시를 별도 command 필드로 전달 | 실행 입력과 run 상태·기존 결과를 분리한다. 평가 전용·불필요 필드가 운영 호출에 섞이지 않게 한다. |
| Agent 1 출력 | `Agent1Output`: `items`, `questions`, `status`, 공통 `meta` | `StageResult[Agent1Payload]`: 현재 DTO의 의미 필드를 유지하고 `result_id`, `revision`, `upstream_refs`, `run_id`를 공통 envelope로 관리 | Agent 판단 결과와 실행 출처는 필요하나, 오케스트레이션 상태까지 결과 payload에 넣지 않는다. |
| Agent 2 입력 | `Agent2Input`: `message_text`, `a1_result_id`, 항목·제약·답변 | `Agent2ExecuteRequest`: 현재 판단에 필요한 항목·제약·답변 + `upstream_result_refs` | 현재도 A1 전체 결과 대신 필요한 입력을 전달한다. 이 축약 원칙을 유지하고 원문 등 재사용 가능한 입력은 run snapshot/ref로 전달할지 합의한다. |
| Agent 2 출력 | `Agent2Output`: assessment·질문·상태·meta | `StageResult[Agent2Payload]`: 의미 결과는 유지. 수정 시 불변 결과의 새 revision 생성 | Backend projection과 wire 결과를 명시적으로 구분하고 결과 이력을 보호한다. |
| PM 수정/검토 | `PMReview`, `ItemReview`를 Agent3Input 및 엔진 API가 사용 | `HumanDecision`/`StageEdit` command와 revisioned result를 분리. 검토 주체·대상 result ID·사유·수정 항목을 명시 | 사람의 확정 값은 AI 생성 결과와 다른 출처·권한·이력을 가진다. 자동/직접 화면에서 동일한 변경 규칙을 적용한다. |
| Agent 3 입력 | `Agent3Input`: items, 전체 `a2_result`, `pm_review`, constraints, answers, `rate_card_id` | `Agent3ExecuteRequest`: 산정 대상, 해당 항목의 assessment/승인된 판단 요약, 필요한 제약·답변, 기준표의 불변 참조, `upstream_result_refs` | Agent3 프롬프트는 산정 대상 assessment를 사용한다. 전체 A2 결과 복사 대신 필요한 판정만 전달하되, 질문/상태 선행조건을 보존한다. |
| Agent 3 출력 | `Agent3Output`: estimates, draft, questions, 상태·meta. 별도 session/workflow 응답도 존재 | `StageResult[Agent3Payload]` + 필요 시 별도 `DocumentResult`; 동일 run/result lineage에 연결 | 세션 기반 API와 공통 실행 결과의 ID·revision·저장 소유권을 통합한다. 비용·문서 생성은 서로 다른 결과 유형이라면 분리한다. |
| supervisor | 현재 공통 스키마에 판단 DTO 없음. Agent2·3 adapter 문서는 일부 도구 호출 계약을 설명 | `SupervisorDecision`(= NextAction): `decision_id`, `run_id`, `based_on_state_revision`, `action`(`RUN_AGENT`/`WAIT_FOR_USER`/`COMPLETE`), `agent_type`, `reason`, `required_upstream_result_ids` | LLM 판단을 실제 실행과 분리한다. Runtime은 판단을 검증(선행 결과 current 여부, revision, 허용 전이)한 뒤에만 실행한다. `PlanProposal`/task 그래프는 만들지 않는다. |
| 실행 상태 | `WorkflowState`: 고정 `stage`, `RunStatus`, A1/A2/A3 ID, stale ID, history | `RunState`: Agent별 상태(`agents{agent1,agent2,agent3}`), 현재 단계, current result ID·revision, upstream refs, stale 결과, pending human action, `state_revision` | Agent 1·2·3의 고정 업무 단계를 우선 표현하고, Agent 추가·조건부 분기는 `agents` 키 확장으로 대응한다. |

## 4. 권장 공통 DTO

아래는 구조 제안이며 확정 API 필드명은 아니다. `StageResult`의 payload는 Agent별 기존 출력 스키마를 사용한다.

### 4.1 명령(envelope)

```json
{
  "contract_version": "1.0",
  "run_id": "run-...",
  "expected_state_revision": 8,
  "trigger": "manual",
  "command": {
    "type": "execute_stage",
    "stage": "agent2",
    "input": {
      "item_ids": ["..."],
      "upstream_result_ids": ["..."]
    }
  }
}
```

- `trigger`: 실행 출처(`orchestrator`, `manual`, 필요 시 `retry`)를 구분한다.
- Agent 입력은 단계별 DTO로 검증한다. 위 예시는 envelope를 보여주기 위한 형태이며, 모든 필드를 하나의 느슨한 map으로 만들라는 뜻은 아니다.
- Backend가 사용자 권한·프로젝트·검토 버전 등 신뢰 경계 정보를 주입한다. 모델이 임의로 선택한 ID를 그대로 권한으로 삼지 않는다.

### 4.2 단계 결과

```json
{
  "run_id": "run-...",
  "agent_type": "agent2",
  "result_id": "result-...",
  "result_revision": 2,
  "status": "COMPLETED",
  "upstream_result_ids": ["a1-result-..."],
  "payload": {
    "status": "READY_FOR_REVIEW",
    "assessments": [],
    "questions": []
  }
}
```

수정은 기존 payload를 덮어쓰지 않고 새 결과 revision으로 남긴다. 사람이 바꾼 필드와 Agent 제안 필드의 출처를 구분해야 한다.

### 4.3 RunState (고정 Agent 단계 우선)

```json
{
  "run_id": "run-...",
  "state_revision": 8,
  "status": "WAITING_PM_REVIEW",
  "agents": {
    "agent1": {"status": "COMPLETED", "current_result_id": "a1-...", "result_revision": 1, "upstream_result_ids": [], "stale": false},
    "agent2": {"status": "COMPLETED", "current_result_id": "a2-...", "result_revision": 2, "upstream_result_ids": ["a1-..."], "stale": false},
    "agent3": {"status": "NOT_STARTED", "current_result_id": null, "result_revision": null, "upstream_result_ids": [], "stale": false}
  },
  "pending_review": {"agent_type": "agent2", "target_result_id": "a2-...", "reason": "..."}
}
```

- 구현된 계약(`RunStateSnapshot`)과 같은 구조다. Agent 상태는 `NOT_STARTED/RUNNING/COMPLETED/FAILED/SKIPPED`이며 Run 전체 상태(`RunStatus`)와 구분한다. 사용자 검토 대기는 Agent 상태가 아니라 Run 상태 `WAITING_PM_REVIEW`와 `pending_review`로만 표현한다.
- `current_stage`는 필수 필드로 두지 않는다. 실행 중인 Agent는 `agents`의 `RUNNING`, 검토 대상은 `pending_review`로 확인하고 다음 실행 대상은 Runtime이 정한다. 화면에서 명시적 active stage가 필요하면 그때 검토한다.
- `current_result_id`/`result_revision`은 마지막으로 성공 커밋된 결과를 가리키며 실패한 시도는 포인터를 옮기지 않는다(잠정, Backend 합의 필요).

Agent 의존 순서(1→2→3)는 코드 규칙이 가지며 Run에는 저장하지 않는다. 새 Agent나 조건부 분기는 `agents` 키와 규칙 표를 확장해 대응한다. task 그래프·task dependency는 두지 않는다.

### 4.4 SupervisorDecision

```json
{
  "decision_id": "dec-...",
  "run_id": "run-...",
  "based_on_state_revision": 8,
  "action": "RUN_AGENT",
  "agent_type": "agent3",
  "reason": "범위 밖 항목이 PM 확인되어 공수 산출이 필요하다",
  "required_upstream_result_ids": ["a2-..."]
}
```

- `action`: `RUN_AGENT`, `WAIT_FOR_USER`, `COMPLETE`. 이 밖의 실행 제어(재시도, 실패 전환)는 Runtime이 결정한다.
- Supervisor는 상태 요약과 결과 요약만 입력으로 받고 Agent payload 전체를 복사해 받지 않는다.
- 코드 기반 Runtime이 검증한다: `based_on_state_revision`이 현재 revision과 일치, `agent_type`의 선행 결과가 current이고 stale 아님, 허용된 상태 전이, 중복 실행 아님. 위반 시 Supervisor 판단을 거부하고 상태 재조회 후 다시 판단하거나 사용자 검토로 전환한다.
- Supervisor 판단 이력(`decision_id`, 이유)을 Backend `history`에 남길지는 합의가 필요하다(9장).

## 5. Backend와 맞춰야 할 필드·계약

| 항목 | 현재 관찰된 차이/누락 | 수정 계획 |
|---|---|---|
| 입력 DTO | Backend SCOPE는 `JsonNode payload`; AI Agent는 엄격한 `Agent2Input` | 공통 schema/version을 명시하고, Backend 요청 생성 시 Agent 입력과 동일한 계약 검증을 수행한다. 전체 Agent 통합 전까지 기존 SCOPE 계약은 호환 유지한다. |
| ID 체계 | AI `analysis_id`, stage `result_id`, Agent3 `session_id`; Backend `ai_analysis_id`, `review_version_id`, `change_request_id` | `run_id`를 기준 키로 정의하고 각 시스템 ID는 명시적 external ref로 매핑한다. 각 ID의 발급자·유일 범위를 문서화한다. |
| 원문 revision | Backend 입력 빌더에서 `request_revision=1` 고정 | 실제 변경요청/검토 버전과 연결되는 revision 규칙을 만든다. 원문 수정과 답변·항목 편집 revision을 구별한다. |
| 분석 시작점 | Backend에서 확인된 C 흐름은 Agent2 범위 분석 입력 생성 | Agent 1 입력 원문 및 Agent 3 입력에 필요한 승인/산정 기준을 Backend에서 제공하거나 참조할 경계를 추가한다. |
| 항목 intent/type | Backend builder가 기능은 `change_request`, 그 외는 `constraint`로 매핑 | Agent 스키마 enum과 Backend 도메인 enum의 양방향 mapping table을 정하고 손실 변환을 검증한다. |
| 답변 | Builder가 `questionAnswerId`를 `answer_id`와 `question_id` 모두에 사용하고 `answered_at`은 null 가능 | 질문 ID와 답변 이벤트 ID를 별도 매핑한다. 필수 시각의 출처/nullable 정책을 통일하고 answered_by를 실제 주체에서 만든다. |
| 결과 응답 | Backend는 A2 결과를 C 도메인 사용 필드로 투영 | 이는 합리적 경계이므로 유지한다. 다만 운영 wire 계약의 필수 검증과 도메인 projection을 별도 단계로 두고 projection이 버리는 필드를 명시한다. |
| 영속 상태 | `ai_analysis`는 분석 snapshot/result 보관, 확인한 워크플로 상태 제안은 별도 체크포인트 개념 | Run 상태와 `state_revision`의 영속 소유자는 Backend로 확정(6-1장). 서로 다른 DB 간 트랜잭션을 가정하지 말고 operation/idempotency와 재조정 정책을 정한다. |
| API 직접 실행 | AI 단독 A1/A2 실행 API는 저장하지 않음. A3는 별도 세션 상태 보유 | 세 Agent direct call을 같은 run API를 통해 실행하고, 결과 저장·current 포인터 이동·stale 처리 후 응답하는 계약을 추가한다. |

### Backend 계약 검증 범위

1. Python 공통 schema에서 버전이 붙은 JSON Schema/OpenAPI 또는 동등한 계약 산출물을 관리한다.
2. Backend builder의 실제 입력 JSON을 Agent 입력 모델로 검증하는 contract test를 둔다.
3. AI 결과의 필수 필드·enum·schema version을 Backend가 저장/반영 전에 검증한다.
4. ID 매핑, UUID/text 변환, nullability, timezone, 금액 단위와 revision 규칙을 fixture로 양방향 테스트한다.

## 6. 제거·대체·유지 후보

“제거”는 API 호환성과 사용처 확인 후 수행한다. 아래 항목은 현재 호출을 일괄 삭제하라는 지시가 아니라 통합 시의 정리 목록이다.

### 통합 DTO로 대체할 후보

| 현 DTO/필드 | 계획 |
|---|---|
| `ContractRequestDTO` | 단계 결과마다 원 입력을 중복 내장하므로 `RunInputRef`/run 조회 응답으로 대체한다. 원문 전체는 권한 있는 별도 입력 조회 또는 snapshot 참조로 제공한다. |
| `StructuredRequestDTO` | A1 결과의 wrapper이므로 `StageResult[Agent1Output]`로 대체한다. |
| `ScopeAnalysisDTO` | 원 요청과 A1 결과를 복사하는 wrapper이므로 `StageResult[Agent2Output]` 및 upstream ref로 대체한다. |
| `CostEstimationDTO` | 앞 단계 payload 중첩을 없애고 `StageResult[Agent3Output]` 또는 분리된 estimate/document result 참조로 대체한다. |
| `AnalysisResultDTO` | 중첩 누적 응답을 run summary + `RunState` + 단계 결과 참조/별도 조회로 나눈다. |
| `WorkflowState.stage`의 `pm_items`/`pm_review`/`lead_review` 혼합 Literal | `agents`별 상태와 `pending_review`로 분리한다. 초기에는 고정 단계 구조를 유지한다. |
| `WorkflowState.rerun_from` | 기존 `RERUN_TABLE` 규칙을 유지하되 결과는 stale result refs와 재실행 대상 Agent로 표현한다. |
| Agent3의 독립 session ID 흐름 | `run_id`/`result_id`와 명시적으로 연결하고, 새 통합 계약에서는 session ID를 내부 구현으로 숨긴다. |

### 단계적으로 제거 검토할 필드

| 필드 | 검토 이유 / 처리 조건 |
|---|---|
| `Context.scenario_id`, `Item.gold_origin`, `Assessment.gold_ref`, `channel_src`, `duplicates` | 데이터셋·평가 전용 용도를 설명하는 필드다. 운영 API 계약에서 제외하거나 평가 전용 확장 schema로 이동한다. 모든 fixture/eval 사용처를 보존한 뒤 결정한다. |
| `Meta.debug` | 자유형 디버그 값은 운영 계약·저장 결과에 불필요한 정보가 섞일 수 있다. 개발 telemetry로 분리하고 운영 wire output에서는 허용 목록/크기를 제한한다. |
| `AnalysisResultDTO`의 `Context` import | `analysis.py`에서 직접 DTO 필드로 쓰이지 않는 import로 보이며, 구조 변경 시 미사용 코드를 정리한다. |
| `ScheduleReferenceDTO.milestones: list[dict]` | 구조화된 schedule response의 내부 타입을 정의해 무형식 dict를 줄인다. 단, schedule reference 자체는 제품 기능 필요성에 따라 유지한다. |

### 유지해야 할 의미

- `result_id`, `result_revision`, `upstream_result_ids`: 결과 불변성·재현·계보에 필요하다.
- `state_revision` 또는 동등한 ETag: 자동/직접 화면의 동시 수정과 오래된 화면 제출을 차단한다.
- `stale_result_ids` 또는 대체 가능한 lineage 이벤트: 수정 후 과거 결과를 현재 결과처럼 쓰지 않게 한다.
- `Question`, `Answer`, `PMReview` 등 사람 상호작용의 출처와 상태: 자동 진행이 멈추는 이유 및 직접실행 화면의 편집 내용을 추적한다.
- Agent 출력의 `status`, `error`, schema version 및 근거 검증 정보: Backend가 올바르게 표시·반영할 때 필요하다.

## 6-1. 확정 아키텍처: AI Server Orchestration + Backend State Management + Rule-based Workflow + LLM Supervisor

Planner-Worker 패턴은 채택하지 않는다(전체 작업 그래프·동적 작업 분해·범용 병렬 실행 제외). Supervisor 중심 구조를 쓴다.

**Backend 책임**: Run 생성·식별자 관리, Run 상태와 `state_revision` 영속화, Agent별 current/stale 결과 관리, 결과·사용자 수정 이력 저장, 사용자 권한·승인·상태 변경 검증, AI 서버에 상태 조회/결과 저장 API 제공. Backend는 실행 순서를 제어하지 않으며 AI 서버가 요청한 상태 변경을 검증하고 영속화한다.

**AI Server 책임**: Supervisor(LLM: 현재 Run 상태와 결과를 보고 다음 실행 대상·실행 여부·이유 판단), Orchestration Runtime(코드: 판단 검증, Agent 호출, 상태 전이, 결과 커밋, 재시도, 중단·재개, 복구), Agent 1·2·3(각 전문 분석)과 Agent 간 데이터 전달·의존성 관리. 실행 흐름은 소유하지만 Run의 최종 상태와 결과를 독자적으로 영속 관리하지 않는다.

**직접/자동 실행 통합**: 두 경로는 동일한 AI Runtime과 결과 저장 규칙, 동일한 `run_id`·Backend 상태를 쓴다. 직접 실행은 사용자가 Agent를 지정하고 자동 실행은 Supervisor가 대상을 판단하고 Runtime이 검증한다. 직접→자동 전환 시 새 Run을 만들지 않는다.

**흐름**
1. Backend: Run 생성, 초기 상태 저장
2. AI Server: Supervisor 판단 → Runtime 검증 → Agent 실행
3. AI Server: 결과 검증 후 Backend에 저장 요청(`expected_state_revision`, `operation_id`)
4. Backend: revision 검증 → 결과 저장 → 상태 갱신
5. AI Server: 저장된 상태 확인 → 다음 Agent 실행 또는 사용자 검토 대기
6. Backend: 최종 상태·결과 조회 제공

**추가 설계 항목**: 단계별 결과 저장·체크포인트, AI 서버 재시작 시 Backend 상태 기반 복구, 중복 실행 방지·idempotency, `state_revision` 낙관적 동시성 제어, 직접/자동 실행 동시 접근 제어, 장시간 실행용 비동기 Job과 상태 조회.

> 이전 버전의 "Backend가 실행 루프를 구동" 서술은 폐기한다. 5장의 "영속 상태" 결정과 9장 첫 항목은 이 확정으로 해소되었다.

## 7. API와 화면 경계

다음은 수정 DTO의 사용처 구분이다. URL은 후속 API 설계 때 확정한다. 오케스트레이션·Agent별 API는 AI 서버가, Run 상태·결과 저장/조회 API는 Backend가 제공한다.

| 화면/기능 | API 유형 | 공유 상태 반영 |
|---|---|---|
| 자동실행 시작·계속 | run 생성 또는 orchestration advance(비동기 Job) | Supervisor 판단을 Runtime이 검증한 뒤 Agent를 실행하고 결과를 같은 run에 커밋 |
| Agent 1/2/3 직접 실행 | agent execute(같은 Runtime·커밋 경로) | 같은 run에 result 저장; 선행 조건 및 upstream revision 검사. Supervisor 호출은 생략 |
| 결과 상세 보기 | run summary + stage result 조회 | current/stale revision을 명시 |
| 결과 수정/승인 | stage edit 또는 human decision command | 새 immutable result/revision 기록; 종속 작업만 stale 처리 |
| 자동 화면에서 직접 화면으로 이동 | 조회/내비게이션 | `run_id`, agent_type/result ID 전달; 별도 run 생성 금지 |
| 직접 화면에서 재분석 | agent execute 또는 orchestration advance | 새 결과가 current가 되면 종속 결과를 stale 처리하고 Supervisor가 상태를 재평가 |

## 8. 변경 순서

1. **소유권·ID 합의**: run 상태·결과 snapshot·revision을 Backend와 AI 서버 중 어디서 영속 소유할지, ID 매핑을 확정한다.
2. **계약 고정**: Agent별 입력/결과 wire DTO, nullability, schema version, 오류 envelope와 ID 의미를 정하고 contract fixture를 만든다.
3. **Run DTO 추가**: 기존 `WorkflowState`와 `AnalysisResultDTO`를 바로 삭제하지 않고 동적 `RunState`와 단계 결과 참조 API를 추가한다.
4. **직접 실행 연결**: A1/A2 무상태 호출, A3 세션 호출을 공통 Runtime·결과 커밋 경로 뒤에 연결한다. 직접 편집은 immutable revision으로 기록한다.
5. **Supervisor·Runtime 연결**: `SupervisorDecision` 판단과 Runtime 실행·상태 전이를 분리한다. 검증·재시도·전이·커밋은 코드가, 다음 Agent 선택과 이유는 Supervisor가 맡는다. 먼저 Supervisor 없는 규칙 기반 Runtime(고정 순서)을 만들고 그 위에 LLM Supervisor를 얹는다.
6. **누적 DTO 폐기**: 자동/직접 화면과 Backend 소비자가 새 API를 사용하고 contract/integration test가 통과한 뒤 중첩 wrapper와 구 API를 deprecation 후 제거한다.

## 9. 아직 합의가 필요한 결정

- (확정) 권위 있는 `RunState`·current pointer는 Backend, 실행 흐름은 AI 서버가 소유한다. 6-1장 참조
- 사용자 직접 수정 결과를 Backend 도메인 결과로 보관할지, AI 결과와 동일한 불변 결과 저장소에 별도 actor/source로 보관할지
- Supervisor가 판단할 범위(Agent 건너뛰기/반복 허용 범위)와 판단 입력으로 줄 Run·결과 요약 형식
- Supervisor 판단 이력(`decision_id`, 이유)을 Backend 이력에 남길지
- A2 판단의 사람 승인과 A3 산출물의 최종 승인 등 사람 검토 게이트를 고정 정책으로 둘지, Agent별 정책으로 표현할지
- 원문 전체를 각 Agent 호출에 반복 전송할지, 권한 있는 snapshot/ref를 전달하고 Agent 실행 계층이 가져올지
- Backend의 현재 `SCOPE` 분석을 기존 단독 흐름으로 유지·점진 이관할 기간과 종료 조건

## 10. 완료 기준

- 자동/직접 화면이 동일 `run_id`와 `state_revision`에서 일관된 현재 결과를 표시한다.
- 직접 수정은 과거 결과를 덮어쓰지 않고 새 revision 및 수정 주체를 남긴다.
- Agent 일부 실행·재실행·건너뛰기를 `agents` 상태와 result 참조로 표현할 수 있고, Supervisor 판단과 Runtime의 검증·실행을 구분한다.
- A1/A2/A3 입력은 실제 소비 필드만 포함하며 이전 단계 전체 결과를 무조건 중복하지 않는다.
- Backend가 만든 요청과 AI 결과가 양쪽 contract test에서 검증된다.
- 오래된 revision, 잘못된 upstream, 중복 요청, 부분 실패 및 재시도에서도 current/stale 상태가 모호해지지 않는다.

## 11. 구현 진행 기록

### 1단계 — AI Runtime의 Agent 1·2 실행 primitive (완료, 범위 한정)

- AI 서버에 `POST /api/v2/runs/{run_id}/agent1/items`, `POST /api/v2/runs/{run_id}/agent2/assessments` 추가. Runtime이 호출할 Agent 실행 단위이자 직접 실행 진입점이다.
- 요청: `expected_state_revision`, `trigger`(orchestrator/manual/retry), 기존 Agent 입력. 응답: `run_id`, `based_on_state_revision`, `trigger`, `stage`, 기존 Agent output.
- Agent 2 결과의 `meta.upstream_result_ids`에 입력의 `a1_result_id`를 보존한다.
- 기존 `/agent1/items`, `/agent2/assessments` 등은 호환 유지.
- 이 엔드포인트는 상태를 읽거나 저장하지 않는다. Backend 저장 요청과 revision 검증은 아직 없다.
- 테스트: `tests/test_execution_api.py` 포함 `test_execution_api/test_analysis_api/test_engine/test_api` 33건 통과. 기존 `test_agent1_api.py::test_unknown_fixture_case_is_404`, `test_agent2_api.py::test_fixture_unknown_case_is_404`는 이번 변경 이전(stash 기준)부터 실패하는 별도 이슈다.

### 2단계 — RunStateClient 계약과 Fake (AI 서버 측만 완료)

- `app/orchestration/run_state.py`: `RunStateClient` Protocol(`get_state`, `commit_result`, `record_edit`, `request_review`), 요청/영수증/스냅샷 DTO, 오류 계층.
- `app/orchestration/fake_run_state.py`: 메모리 Fake. 계약의 기대 동작(revision 증가, operation_id 멱등, 충돌, 상태 전환)을 테스트로 고정.
- 재사용: `Stage`, `RunStatus`, `_M`(extra=forbid). 새 ID 체계는 만들지 않고 `Meta.result_id`/`upstream_result_ids`를 사용한다.
- 결정: `result_id`는 AI가 만들고 `result_revision`은 Backend가 저장 시 부여해 영수증으로 돌려준다.
- 같은 `operation_id`+같은 내용 재요청은 오류가 아니라 기존 영수증(`replayed=true`)을 반환한다. 다른 내용이면 `OperationConflict`.
- 오류 재시도 가능 여부: `StatePersistenceError`만 재시도 가능. `StateRevisionConflict`는 상태 재조회 후 새 operation_id로 요청한다.
- 아직 미확정(Backend 합의 필요): 전환표(Fake의 `_ALLOWED`는 잠정), 수정 시 하위 결과 stale 처리, 수정이 새 result revision을 만드는지, HTTP 매핑, 인증.
- 미구현: HTTP 기반 실제 client, Backend API.

### 2-1단계 — RunStateSnapshot Agent별 상태 확장

- `current_results`/`ResultRef`를 제거하고 `agents: {agent1,agent2,agent3: AgentState}`로 대체했다. `AgentState`: `status`, `current_result_id`, `result_revision`, `upstream_result_ids`, `stale`.
- `RunStateClient` 메서드 시그니처와 요청 DTO(`CommitResultRequest` 등)는 변경하지 않았다. 새 Run은 세 Agent 모두 `NOT_STARTED`다.
- 결과 커밋은 `COMPLETED`/`FAILED`를 반영한다. `RUNNING`/`SKIPPED`는 DTO로 표현만 가능하며 Fake의 테스트 전용 `set_agent_status`로만 만든다.
- `current_stage`는 추가하지 않았다.

### 3단계 — 규칙 기반 Orchestration Runtime (Fake 위에서만 검증)

- 계약 확장: `StartAgentRequest`·`start_agent`(Backend 승인 후에만 Agent 실행, RUNNING 전이·revision 증가), `get_result`/`CommittedResult`(커밋된 upstream 결과 조회), `ResultNotFound(RunNotFound)`, `AGENT_PREREQUISITES`(agent2←agent1, agent3←agent1+agent2). 기존 메서드·DTO 의미는 유지.
- `workflow_rules.py`: 순서·시작 가능 여부(순수 함수). `runtime.py`: `OrchestrationRuntime.run(run_id)`(get_state → start_agent → 실행 → commit_result → 재조회 반복), `RunInputs`/`RunInputProvider`, `RuntimeResult`(payload 전문 미포함).
- 충돌·재시도: start/commit revision 충돌 시 재조회, commit 충돌은 Agent 재실행 없이 반영 여부 확인 후 새 operation_id로 재커밋, 저장 실패는 같은 operation_id로 최대 3회. 실패는 FAILED로 커밋하고 이후 Agent를 실행하지 않는다.
- A3 시작 전 PM 검토가 없으면 `request_review`로 대기하고 아무것도 실행하지 않는다. 완료 Agent는 재호출해도 재실행하지 않는다.
- 테스트: Fake 9건(`tests/test_run_state_start_agent.py`) + Runtime 16건(`tests/test_orchestration_runtime.py`). 실제 LLM·Backend 호출 없음 — Fake 통과는 Backend 연동 검증이 아니다.
- 미구현/한계: HTTP client, Backend API, Supervisor, 비동기 Job, RUNNING 복구(재시작 후 RUNNING이면 `AGENT_RUNNING`으로 멈춤), 재호출 시 replay된 start는 `START_REPLAYED`로 멈춤(보수적), stale 전파 정책, NEEDS_INPUT을 검토 대기로 표현(WAITING_INPUT 전이 없음), 리드 검토·PM 항목 확인 미모델링, PM 검토·rate_card 출처(Backend 합의 필요). 범위 필터 규칙은 `engine.py`와 중복(`workflow_rules.SCOPE_INTENTS`).

### 설계 변경 — Planner-Worker → Supervisor 중심 (문서만 변경, 코드 변경 없음)

- `PlanProposal`, task 그래프, `RunState.tasks`를 제거하고 `SupervisorDecision`과 Agent별 상태(`agents`) 중심 `RunState`로 대체했다.
- 1·2단계 코드(v2 실행 API, `RunStateClient`, Fake)는 이 변경과 무관하게 유지된다. `RunStateSnapshot`은 이후 `agents` 구조로 확장했다(아래 2-1단계).
- 위 설계는 모두 미구현이다.

### 구현 현황 (확정 아키텍처 기준)

| 항목 | 상태 |
|---|---|
| Agent 1·2 단일 실행 API(v2) | 완료(상태 비저장) |
| Backend 상태 조회/결과 저장 계약 DTO + AI 측 client port + Fake | 완료(HTTP client는 미구현) |
| Backend run/state/result API·영속 (`feat/#59`) | 미착수 |
| 규칙 기반 Runtime 루프 + `start_agent`/`get_result` 계약 | 완료(Fake 기반 테스트만, Backend 미연동). 기존 `Engine`은 그대로 존재 |
| `SupervisorDecision` DTO + LLM Supervisor | 미착수 |
| `RunStateSnapshot`의 Agent별 상태(`agents`) 확장 | 완료(계약·Fake·테스트). `RUNNING`/`SKIPPED`로 바꾸는 정식 메서드는 미정, `stale` 갱신 규칙 미구현 |
| Agent 3 run 연결(session ID 은닉) | 미착수 |
| 재시작 복구, idempotency, 비동기 Job·상태 조회, 직접/자동 동시성 제어 | 미착수 |

### 다음 단계 제안

4단계: Backend API 계약 확정(`start_agent`, `get_result`, 입력·PM 검토 출처, WAITING_INPUT, stale 정책, RUNNING 복구)과 HTTP client. 이후 `SupervisorDecision` DTO와 LLM Supervisor를 Runtime 검증 뒤에 연결한다. 이전의 Planner 단계는 삭제했다.
