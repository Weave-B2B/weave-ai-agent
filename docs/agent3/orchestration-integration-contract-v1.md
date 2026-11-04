# Agent 3 멀티에이전트 오케스트레이션 연동 제안 계약 v1

- 작성일: 2026-11-04
- 계약 식별자: `a3-orchestration/1.0.0-draft`
- 상태: **Agent 3 측 제안. 팀 공통 규격으로 확정되지 않았음.**
- 범위: 슈퍼바이저 ↔ 공통 실행 서버 ↔ Agent 3 도구. Agent 1·2 내부 구현을 변경하는 계약은 아니다.
- 기준: [통합 분석 설계 4·6장](../prototype/integrated-request-analysis-plan.md), [현재 Agent 3 서비스·API](orchestration-tools.md).

## 용어 정의

- **멀티에이전트 오케스트레이션(Multi-agent orchestration)**: 여러 에이전트의 실행·상태·상호작용을 조율하는 전체 구조와 과정.
- **슈퍼바이저 에이전트(Supervisor agent)**: 대화·실행 문맥과 중간 결과를 보고 다음에 호출할 전문 에이전트·도구를 선택하고, 반환 결과를 종합하는 중앙 에이전트. 이 문서의 조율 주체를 뜻한다.
- **패턴: Supervisor pattern / Agents as tools**: 전문 에이전트를 슈퍼바이저가 호출하는 도구로 노출한다. OpenAI 문서의 Manager pattern도 이 중앙 조율 구조를 가리킨다. 프레임워크별 표현 차이이며 하나의 독점적인 표준 명칭은 아니다.
- **공통 실행 서버**: 슈퍼바이저의 선택을 검증하고 상태·버전·권한·실제 호출을 관리하는 런타임. LLM 에이전트와 구분한다.

현재 설계는 한 번 분류해서 전달하는 Router보다, 결과를 보고 재조사·재산정·질문·종료를 반복 선택하는 Supervisor 패턴에 해당한다. 문서에서 ‘오케스트레이터’라는 넓은 표현 대신 의사결정 AI에는 ‘슈퍼바이저 에이전트’를 사용한다.

근거: [LangChain — Subagents / Supervisor vs. Router](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents), [OpenAI Agents SDK — Agent orchestration](https://openai.github.io/openai-agents-js/guides/multi-agent/). 특정 SDK를 도입했다는 뜻은 아니다.

## 1. 채택할 구조와 현재 구현 상태

슈퍼바이저 LLM은 필요한 작업과 대상 ID를 선택한다. **공통 실행 서버가 입력·버전·권한을 채우고 검증하며, Agent 3은 잠정 산정·문서 생성 도구로 동작한다.** PM 승인은 마지막 검토에서 한 번만 받는다.

| 구분 | 현재 상태 | 본 계약의 제안 |
|---|---|---|
| Agent 3 잠정 산정·재산정·재계산·문서·검토 | 서비스 메서드와 `/agent3/orchestration` API 구현 | 기존 도구 재사용 |
| 슈퍼바이저 도구 입력 | `Agent3SupervisorAdapter`의 ID 중심 호출·변환 구현 | 공통 실행 서버가 `Agent3StatePort`를 연결 |
| 질문·추가 조사 | `needs`의 대상·출처와 질문 ID 제공 | 공통 질문 목록에 병합하고 조사·답변·재개 연결 |
| 상태·버전 | adapter의 참조·CAS 포트·격리 실행 구현. 실제 공통 엔진은 미연결 | 공통 상태 소유권과 영속 저장 연결 |
| 문서별 대상 선택·수동 문안 수정 | 전체 묶음 생성·kind 대응·PM 문안 수정 API 구현 | 부분 문서 범위는 명시적으로 거부. 수치는 서버 사실 블록으로 고정 |
| 통합 UI·슈퍼바이저 루프 | 신규 요청 화면은 가상 시연 | 실제 실행 결과를 기존 상세 화면에 연결 |

**Agent 3 측 연결 계층은 구현했으며, 슈퍼바이저 루프·Agent 1/2·공통 저장소에는 아직 연결하지 않았다.** 기존 입력 스키마·엔드포인트는 유지하고 별도 adapter를 제공한다. 사용 진입점은 11장을 참조한다.

## 2. 역할과 권한

| 주체 | 책임 |
|---|---|
| 슈퍼바이저 LLM | 허용된 도구 중 선택, 대상 ID·문서 종류·재검토 이유 제안 |
| 공통 실행 서버 | 케이스·원문 버전·기준선·최신 결과·질문·승인 관리, 입력 구성, 허용 행동 검증, 결과 반영 |
| Agent 3 | 작업·기준·가정 제안, 서버 계산, 필요한 정보 반환, 고객 문서 초안 생성 |
| PM | 질문 답변, 공수·가정·문안 검토, 최종 승인·수정 지시·보류·반려 |

프로젝트·기준선·모델·단가표·승인자·승인 상태는 LLM 도구 인자로 받지 않는다. 모델은 서버 실행 설정을 사용한다. 승인·MD 직접 수정·단가 변경·문안 직접 수정 API는 PM 화면/신뢰된 서버 전용이며 LLM이 대신 호출할 수 없다.

## 3. 서버가 주입할 실행 정보

슈퍼바이저가 반환한 `{action,args,reason}`에 아래 정보를 공통 서버가 결합한다. 아래 이름은 **제안 필드**이며 현재 `WorkflowState`에 모두 존재하는 것은 아니다.

| 필드 | 의미·검증 |
|---|---|
| `case_id`, `project_id`, `request_revision`, `analysis_id` | 현재 실행의 소유 케이스·원문 버전 |
| `baseline_id`, `baseline_as_of` | 해당 실행에 고정된 유효 기준선 |
| `expected_state_revision` | 공통 상태의 동시 수정 방지 버전 |
| `operation_id` | 공통 서버 발급 작업 ID. 동일 작업 재전송·복구에 사용 |
| `a1_result_id`, `a2_result_id` | 입력으로 사용하는 최신 비무효화 결과 |
| `rate_card_id`, 공수 기준 fingerprint | 계산 기준의 버전. 실행 도중 변경되면 결과 확정 거부 |
| `a3_session_id`, `a3_state_revision`, `a3_snapshot_hash` | 현재 Agent 3 세션을 가리키는 서버 전용 참조 |

공통 revision과 Agent 3 revision은 서로 다른 값이다. 한쪽 값을 다른 쪽 자리에 복사하지 않는다. LLM에게는 판단에 필요한 결과 요약만 제공한다.

## 4. 슈퍼바이저 도구 입력

모든 `args`는 미정의 필드를 거부한다. `item_ids`는 중복 없는 현재 케이스의 실제 ID이며 표시용 R1·R2 문자열을 대신 사용하지 않는다.

| 도구 | args | 서버 사전 조건·처리 |
|---|---|---|
| `estimate_impact` | `{item_ids: string[]}` | 최초 잠정 산정 또는 변경 항목 재산정. 서버가 실행 상태로 입력을 구성 |
| `draft_documents` | `{kind: enum, item_ids: string[]}` | 최신 판정·산정으로 문서 생성. PM 중간 승인 불필요 |

`reason`은 사용자에게 보여줄 짧은 작업 설명이다. 실제 API 오류 메시지나 문서 전문을 넣지 않는다.

```json
{
  "action": "estimate_impact",
  "args": {"item_ids": ["CR-A-001-1"]},
  "reason": "범위 밖으로 확인된 변경 작업의 잠정 공수와 금액을 계산합니다."
}
```

### 4.1 산정 대상과 adapter 동작

1. 공통 서버는 항목·의도·조건부 후속·견적 연결 관계로 **이번 요청의 전체 산정 검토 대상 집합**을 만든다. 잠정 입력의 `target_item_ids`에 반영한다. 판단 보류 항목은 서버 가드에서 추가 조사로 분리된다.
2. **v1 최초 호출은 그 전체 집합을 받아 한 세션으로 시작한다.** 일부 항목만 선택해 나머지 필수 대상을 누락시키지 않는다. 전체를 처리할 수 없는 호출은 실행 전 `INVALID_ARGUMENT`으로 반환한다. 선택적 최초 부분 실행은 후속 확장이다.
3. 현재 세션이 있고 동일 입력·기준·결과이면 저장된 결과를 반환한다. Agent 2 결과·작업·가정이 바뀌었으면 `item_ids`를 재산정 요청으로 사용한다. 공유 작업·공유 질문·견적 연결로 영향 범위가 넓어지면 실제 처리 ID 목록을 반환한다.
4. 공통 조건·답변·전역 질문 변경으로 부분 재산정이 불가능하면 `REQUIRES_FULL_REESTIMATION`을 반환한다. 숨겨서 전체를 호출하거나 PM 승인을 생성하지 않는다.
5. 원문 버전·분석 흐름·기준선이 달라졌으면 새 세션을 시작하고 이전 결과는 이력으로 남긴다. 늦게 끝난 과거 실행은 공통 상태를 덮어쓰지 못한다.

산정 조건은 **범위 밖 판정 + 기준선 내 검증된 근거 + 미해결 블로킹 질문 없음**이다. 범위 내·기승인 변경은 새 추가비 산정에서 제외하고, 판단 보류·근거 없음은 조사 대상으로 반환한다. 금액은 서버 `calc_cost`만 생성한다.

### 4.2 Agent 1·2 결과에서 필요한 연결 정보

- `quote_links`: 견적 문의 항목 → 실제 견적 대상 항목 ID 목록. 별도 문의를 중복 산정하지 않는다.
- `conditional_item_ids`: 원문상 조건부 추가 의도가 보존된 항목만 지정한다. 서버가 근거 없이 조건부 요청을 만들어 넣지 않는다.
- Agent 2 항목별 판정·근거·질문과 결과 ID. `verified=true` 전달만으로 검증을 생략하지 않는다.
- 질문·답변 ID 연결과 출처. `estimate_only`는 `estimate_request`로 자동 치환하지 않는다. 의미가 미합의된 입력은 명시적으로 되돌린다.

Agent 1·2의 실제 출력에서 위 정보를 만드는 adapter는 별도 구현해야 한다. 필드가 없으면 자료 부족으로 반환하고 임의 추론으로 메우지 않는다.

### 4.3 문서 종류와 현재 API 대응

| 제안 kind | 의미 | 현재 Agent 3 kind |
|---|---|---|
| `negotiation_email` | 변경·확인 결과를 합친 고객 메일 | `email` |
| `change_agreement` | 고객 협의 전 변경합의서 초안 | `agreement` |
| `scope_response` | 범위 확인 답변 | `scope_reply` |

`bundle`은 서버 내부 묶음 생성 옵션이며 슈퍼바이저 kind에는 노출하지 않는다. 확인·변경이 섞이면 메일은 한 통으로 합친다.

**v1 문서 대상은 세션의 전체 문서 검토 대상 집합이다.** 현재 API에 `item_ids` 필터가 없으므로 adapter는 전체 집합과 일치하는지 검사한다. 부분 집합 요청은 `UNSUPPORTED_DOCUMENT_SCOPE`로 거부하며, 인자를 무시하고 전체 문서를 생성하지 않는다.

현재 생성 API는 호출 때마다 기존 문서 묶음을 교체한다. 따라서 변경 건에서는 adapter가 `bundle`로 메일·합의서를 함께 생성·보관하고, 슈퍼바이저가 요청한 종류의 결과 ID를 반환한다. 동일 snapshot의 다른 문서를 요청하면 보관된 결과를 반환한다. `email` 후 `agreement`를 순차 호출해 앞 문서를 없애는 연결은 금지한다.

## 5. 슈퍼바이저에 반환할 결과

반환 형식은 아래 제안 envelope로 통일한다. 실제 메모리 세션 객체를 그대로 LLM에 보내지 않는다.

```json
{
  "contract_version": "a3-orchestration/1.0.0-draft",
  "operation_id": "op-a3-001",
  "status": "needs_input",
  "requested_item_ids": ["CR-A-001-1"],
  "processed_item_ids": ["CR-A-001-1"],
  "result_ids": ["a3-impact-example"],
  "summary": "해당 항목의 계약 제외 범위를 추가 확인해야 합니다.",
  "needs": [{
    "need_id": "need-a3-example",
    "kind": "more_evidence",
    "item_ids": ["CR-A-001-1"],
    "topic": "사진 저장 기능의 계약 제외 조항",
    "target": "agent2",
    "source": "agent3",
    "question_id": null,
    "blocking": true
  }],
  "error": null
}
```

- `status`: `ready` / `needs_input` / `not_required` / `error`.일부 성공은 항목별 저장하며 미해결 대상이 남으면 `needs_input`이다. 미산정을 0 MD·0원으로 바꾸지 않는다.
- `result_ids`: 공통 저장 계층에 보관된 결과 참조. 화면용 작업표·금액·문서 전문은 서버 상태에서 조회한다.
- `need_id`: adapter가 실행 문맥·kind·항목·대상·주제에서 결정해 반복 신호를 병합한다. Agent 3 adapter가 발급한다. 기존 저수준 API 응답에는 이 필드가 없다.
- 서버 내부 참조에는 실제 Agent 3 결과 ID, 세션·revision·snapshot과 기준 fingerprint를 함께 보존한다.
- 이 envelope는 제안이며 현재 HTTP 응답인 `{session_id,state_revision,impact,needs,...}`와 구분한다.

## 6. 조사·질문·답변 연결

| kind / target | 슈퍼바이저·서버 처리 |
|---|---|
| `more_evidence` / `agent2` | `assess_scope(item_ids,focus=topic)`를 먼저 호출. 결과 반영 후 영향 항목 재산정 |
| `client_input` / `pm` | 문서로 해결되지 않은 사실 확인을 PM 질문 목록에 병합 |
| `client_input` / `client` | 고객 확인 사항. 비블로킹이면 고객 메일에 포함, 블로킹이면 PM을 통해 답변 수집 |
| `missing_standard` / `pm` | 공수·단가 기준 보완 요청. 계약 조사 완료로 대신 해결하지 않음 |
| `not_required` / `none` | 제외 사유 표시. 질문이나 승인 대기로 만들지 않음 |

PM/고객 대상 신호라도 문서로 확인 가능한 내용은 Agent 2 조사 후 남은 것만 묻는다. 슈퍼바이저의 선택을 서버 허용 행동 검사로 제한한다. 신호가 반복되면 조사·질문을 중복 생성하지 않는다.

질문은 `question_id` 기준으로 공통 목록에 병합하고 `target`, `source`, `item_ids`를 보존한다. 답변은 `question_id`와 `answer_id`로 연결한다. 답변 도착은 해소가 아니며, Agent 1의 답변 해석·Agent 2 조사·Agent 3 재산정 결과로 해소 여부를 확인한다.

같은 문맥·대상·질문이면 순서가 달라져도 ID를 유지한다. 의미가 바뀐 질문의 옛 기록은 `superseded` 이력으로 남긴다. 부분 재산정에서 다른 항목의 열린 질문을 지우지 않는다. 임의로 `resolved`나 PM 승인 기록을 만들어 진행하지 않는다.

호출 한도 초과·추가 조사 실패·자동 처리 불가 시 `request_incomplete_review`로 전환한다. 미완료 검토를 정상 최종 승인으로 처리하지 않는다.

## 7. 공통 상태와 버전의 소유권

- **공통 엔진이 케이스의 최신 결과·질문·최종 검토 상태를 소유한다.** Agent 3 세션은 계산·문서 도구의 내부 실행 참조다.
- adapter는 실행 전 공통 `expected_state_revision`을 확인하고, Agent 3에는 저장된 `a3_state_revision`을 전달한다. 완료 후에도 입력으로 사용한 상위 결과·공통 revision을 다시 검사해 결과를 반영한다.
- 뒤늦게 끝난 과거 원문·Agent 2 결과·동일 원문에 대한 과거 실행은 최신 상태를 덮어쓰지 못한다. Agent 3의 원문 revision 비교만으로 이 검사를 대신하지 않는다.
- DB 저장 시 공통 상태 갱신은 조건부 갱신으로 처리한다. 현재 Agent 3 메모리 세션은 재시작·다중 프로세스 복구를 제공하지 않으므로 운영 연동에는 저장 어댑터가 필요하다.
- 동일 `operation_id` 재전송은 같은 결과를 반환한다. 다른 args로 ID를 재사용하면 충돌이다. adapter는 `read_operation`과 원자적 `commit` 포트를 사용한다. 실제 공통 저장소가 중복 방지·결과 재조회·트랜잭션을 구현해야 한다.

## 8. 최종 PM 검토와 수정

정상 경로는 **잠정 판정·산정·문서 준비 → 최종 PM 검토 1회**다. 사실 확인 답변은 승인이 아니다.

| PM 동작 | 신뢰된 서버의 처리 |
|---|---|
| 공수·단가 수정 | `recalculate`. MD·대상 작업·단가 버전·수정 사유 검증. LLM 호출 없음 |
| 작업·가정 수정 지시 | 지시를 구조화해 영향 항목 재산정. 공유 의존 항목까지 포함 |
| 문구 직접 수정 | PM 전용 문서 수정 API에서 새 문서 버전 생성. 서버 사실 블록은 고정하고 주변 문구만 수정 |
| 승인 | 동일 판정·산정·문서 묶음과 기준 버전의 snapshot을 확인해 최종 승인 |
| 보류·반려 | 사유와 검토 대상 버전을 보존. 고객 합의나 청구 상태를 만들지 않음 |

PM 이벤트에는 서버가 인증한 검토자, `review_request_id`, 공통 expected revision, Agent 3 snapshot, decision, 수정/검토 내용, 일정 미산정 확인을 결합한다. LLM이 입력한 reviewer 문자열을 인증으로 쓰지 않는다.

공통 서버가 실제 PM 응답을 받은 후 Agent 3 `review`를 호출해 검증 영수증을 보관한다. **이것은 두 번째 PM 승인 단계가 아니다.** 공통 최종 승인과 영수증의 연결은 재시도·장애 복구에서도 같은 검토 요청 ID를 사용한다.

원문·근거·작업·MD·단가·문구가 바뀌면 이전 승인은 현재 결과에 적용하지 않는다. 필수 미해결·미산정·문서 누락이 있으면 정상 승인을 거부한다. 승인된 문서도 고객 미합의 초안이며, 발송·서명·새 기준선 반영은 별도 업무다.

## 9. 오류와 재시도

| 분류 | 예시 | 처리 |
|---|---|---|
| 업무상 미완료 | 근거·규모·기준 부족 | `needs_input` + needs. 기술 오류로 재시도하지 않음 |
| 입력 오류 | 다른 케이스 ID, 부분 문서 범위, 잘못된 도구 args | `INVALID_ARGUMENT`/`UNSUPPORTED_DOCUMENT_SCOPE`. 인자 수정 |
| 버전 충돌 | 오래된 공통 revision·Agent 3 snapshot | `STATE_CONFLICT`. 최신 상태 재조회 후 재계획 |
| 부분 처리 불가 | 전역 조건·답변 변경 | `REQUIRES_FULL_REESTIMATION`. 명시적 전체 재호출 |
| 모델 오류 | 잘못된 항목 커버리지·공유 작업 충돌 | `OUTPUT_INVALID`. 유한 횟수 재시도, 초과 시 미완료 검토 |
| 기술 실패 | 모델/도구 timeout·연결 실패 | 오류 code·retryable·실패 단계를 반환. 예산 안에서만 재시도 |

오류 envelope는 `status=error`, `error={code,message,retryable,stage}`다. 현재 HTTP 404/409/422/502/503과 오류 코드를 adapter에서 대응한다. 상세 오류에 키·문서 전문을 넣지 않는다. 현재 모든 오류 경로의 retryable 값이 이 제안과 일치하는지 계약 테스트가 필요하다.

타임아웃으로 성공 여부를 모르면 새 작업을 무조건 시작하지 않고 `operation_id`로 조회·복구한다. 재시도는 공통 호출 예산에 포함한다. 로컬 모델의 긴 실행은 서버 작업 상태 조회로 처리하며, 기존 브라우저의 짧은 fetch 제한을 실행 한도로 사용하지 않는다.

## 10. 채택 전 구현·완료 기준

| 과제 | 완료 기준 |
|---|---|
| 슈퍼바이저 도구 adapter | ID args로 서버가 전체 입력 구성. LLM의 실행 문맥 변경 거부 |
| 결과·질문·상태 매핑 | 위 envelope와 현재 Agent 3 응답 대응, 결과·답변의 버전 연결 |
| 문서 묶음 매핑 | kind 이름 대응, 부분 범위 거부, 순차 요청에도 필요한 문서 보존 |
| 최종 검토 연결 | 실제 PM 이벤트만 승인 호출, 수정 시 효력 해제, 장애 후 중복 승인 방지 |
| 문안 수정 | 새 문서 버전·수치 검증·재검토 지원 |
| 저장·복구 | 다중 실행·오래된 완료·재시작·operation_id 중복 요청 검증 |
| 기존 화면 연결 | 기존 원문·범위·영향·승인·협의 화면에 실제 결과 표시. 별도 도구 테스트 UI 추가하지 않음 |

필수 계약 테스트: 다항목 일부 보류, 조건부 변경, 견적 연결 중복 방지, 조사 후 재산정, 질문 순서 변경과 답변 연결, 공유 작업·질문 영향 범위, MD 수정 무모델 재계산, 문구 수정 후 재검토, 문서 순차 요청, 오래된 결과 반영 차단, 중복 호출·승인, 미완료 상태의 승인 차단.

팀 확인 항목은 `quote_links`·조건부 의도 출처, Agent 2 `focus`·비기능 항목 입력, intent 이름, 공통 질문/needs 규격, 최종 승인 저장 주체·모델 실행 설정이다. 합의 전에는 다른 담당자의 스키마나 엔진이 이 계약을 지원한다고 가정하지 않는다.

## 11. 구현된 Agent 3 측 진입점

구현: `app/agents/agent3/supervisor_adapter.py`. 코드의 이름은 `Supervisor`를 사용하며, 특정 에이전트 프레임워크에는 의존하지 않는다.

```python
from app.agents.agent3.agent import LlmAgent3
from app.agents.agent3.orchestration import Agent3Orchestration
from app.agents.agent3.supervisor_adapter import (
    Agent3StatePort, Agent3SupervisorAdapter, tool_schemas, action_schema,
)

def bind_agent3(port: Agent3StatePort, resolve_llm):
    # resolve_llm은 서버 설정 revision으로 클라이언트를 구성하는 호스트 함수다.
    def factory(snapshot):
        return Agent3Orchestration(
            LlmAgent3(resolve_llm(snapshot.execution_config_revision))
        )
    return Agent3SupervisorAdapter(port, factory)
```

- `tool_schemas()`: `estimate_impact`, `draft_documents`의 인자 스키마.
- `action_schema()`: 구조화된 `{action,args,reason}` 전체 스키마. 함수 호출형 SDK를 쓰면 호스트가 함수 이름·args와 진행 설명을 이 envelope로 변환한다.
- `adapter.execute(case_id, expected_state_revision, operation_id, call)`: 앞의 세 인자는 신뢰된 실행 서버가 주입한다. `call`만 슈퍼바이저 결정이다. PM 승인 도구는 포함되지 않는다.
- `adapter.execute_pm(case_id, expected_state_revision, operation_id, command, actor_ref=...)`: 호스트가 실제 PM 응답·인증 주체를 검증한 뒤 호출한다. `command.action`은 `recalculate` / `edit_document` / `review`다. `review`는 PM이 본 `expected_snapshot_hash`를 요구한다.
- 반환은 `ToolResult`. 본문의 결과 envelope를 구현하며 오류도 같은 형식으로 반환한다. 자동 재시도·Agent 2 호출은 수행하지 않는다.

### 호스트가 제공할 포트

`Agent3StatePort`는 다음 세 메서드다. 실제 공통 엔진용 구현은 이번 Agent 3 변경에 포함하지 않는다.

| 메서드 | 호스트 구현 조건 |
|---|---|
| `load(case_id)` | 분리된 `Agent3CaseSnapshot` 반환. 원문·정규화 항목·Agent 1 결과 ID·Agent 2 결과·조건·답변·단가·연결 관계·공통 revision·현재 Agent 3 참조 포함 |
| `read_operation(case_id, operation_id)` | 이미 성공한 작업의 `OperationReceipt` 반환 |
| `commit(case_id, expected_revision, update, operation_id, receipt)` | revision 비교, 결과/질문/참조 반영, 영수증 저장을 원자적으로 수행. 충돌은 `WorkflowConflict`. update가 없으면 결과 캐시 조회의 영수증만 저장 |

`Agent3Update`에는 다음을 보낸다.

- `a3_ref`: 저수준 세션 ID·revision·snapshot, 원본 입력 fingerprint, 실행 설정 revision.
- `view`: 작업·문서·승인·이력 전체. 화면/저장용이며 LLM 결과 envelope와 구별한다.
- `source_result_ids`: Agent 1·2·3 결과 연결.
- `rate_card_id`: PM 재계산에서 선택한 유효 단가표. 호스트 입력에도 같은 버전을 반영한다.
- `active_questions`, `resolved_question_ids`, `superseded_question_ids`: 호스트가 공통 질문 목록에 병합할 변경 정보. 기존 질문 이력은 삭제하지 않는다.

Agent 3은 격리한 도구 세션에서 실행하고 호스트 commit이 성공한 뒤 해당 세션을 적용한다. 공통 revision이 실행 중 바뀌면 계산·문서 결과를 현재 상태에 적용하지 않는다. 기존 세션도 부분적으로 수정된 상태로 남기지 않는다. 영수증은 과거 작업 결과이므로 새 케이스 상태에 다시 적용하지 않는다.

포트 저장이 영속적이어도 현재 Agent 3 도구 세션은 메모리다. 프로세스 재시작·여러 워커 간 세션 복구는 지원하지 않으며, 세션 소실 시 새 전체 산정이 필요하다. 운영용 저장·라우팅은 별도 연결 과제다. 한 케이스의 도구 실행은 같은 adapter/세션 소유자로 전달해야 한다.

### PM 문안 수정

PM 전용 command 예시다. Agent 3은 `[[APPROVED_FACTS]]` 자리에 최신 판정·근거·수치를 서버에서 삽입한다. 실제 PM 화면에서는 사실 영역을 읽기 전용으로 두고 이 표식을 그대로 노출하지 않는다.

```json
{
  "action": "edit_document",
  "kind": "negotiation_email",
  "narrative": "안녕하세요. 검토 내용을 안내드립니다.\n\n[[APPROVED_FACTS]]\n\n검토 후 의견 부탁드립니다.",
  "reason": "고객 전달 전 PM 문안 검수"
}
```

수치·금액·새 확정 표현을 임의로 덧붙이는 수정은 거부한다. 문서 한 건을 수정해도 같은 산정의 다른 문서는 보존하고, 기존 최종 승인은 해제한다. 새 문구의 전체 묶음을 PM이 다시 확인해야 한다.

저수준 API에도 `POST /agent3/orchestration/{session_id}/documents/edit`가 추가되었다. body는 `state_revision`, `kind`(`email`/`agreement`), `narrative`, `editor_ref`, `reason`이다. 기존 Agent 3 API와 같이 신뢰된 서버 호출용이며 실제 사용자 인증 API가 아니다.

검증: `tests/test_agent3_supervisor_adapter.py`. 메모리 호스트 예제는 테스트에만 두며, 기존 공통 상태 저장소를 대체하지 않는다. 기존 프로토타입 화면에는 새 도구 영역을 추가하지 않았다.
