# Agent 3 오케스트레이션 도구 연동

2026-11-04 · Agent 3 도구 구현. 중앙 의사결정 주체는 **슈퍼바이저 에이전트(Supervisor agent)**, 전체 구조는 **멀티에이전트 오케스트레이션**으로 구분한다. 슈퍼바이저 LLM의 자동 도구 선택·Agent 1/2 호출은 별도 연동 대상이다.

슈퍼바이저 담당에게 제안할 호출·상태·질문·승인 규격은 [Agent 3 멀티에이전트 오케스트레이션 연동 제안 계약 v1](orchestration-integration-contract-v1.md)을 따른다. 제안 adapter와 현재 구현을 구분하며, 이 문서는 현재 저수준 서비스·API를 설명한다.

Agent 3 측 adapter 진입점은 `Agent3SupervisorAdapter`다. ID 중심 호출, kind 대응, 결과 envelope, 질문 변경 정보, 호스트 CAS 포트와 PM 전용 수정·검토를 구현했다. 실제 공통 엔진 연결·슈퍼바이저 루프는 포함하지 않는다. 사용법은 [계약 11장](orchestration-integration-contract-v1.md#11-구현된-agent-3-측-진입점)을 참조한다.

## 이번 구현의 범위

- 기존 `/agent3/impacts`와 `Agent3Workflow`의 개별 PM 검토 경로는 유지한다.
- `Agent3Orchestration`은 PM 검토 기록을 만들지 않고 잠정 산정·메일·변경합의서 초안을 생성한다.
- 공통 `schema/schemas.py`를 변경하지 않고 `app/agents/agent3/contracts.py`에 별도 계약을 둔다.
- 공통 엔진의 기존 `lead_review`를 승인으로 사용하지 않는다. 통합 슈퍼바이저가 도구의 세션 ID·revision을 공통 실행 상태에 연결하고 실제 PM 최종 검토 때만 `review`를 호출해야 한다.
- 메모리 저장, 신뢰된 서버 호출용이다. 사용자 인증·권한, 업무 DB 저장·재시작 복구, 고객 발송·서명·기준선 반영은 추가하지 않았다.

## 입력과 산정 대상

`ProvisionalAgent3Input`은 기존 context, items, constraints, answers, rate_card_id, a2_result와 다음 필드를 받는다.

| 필드 | 의미 |
|---|---|
| `mode` | `provisional` |
| `target_item_ids` | 산정을 요청하는 항목. 서버가 다시 적합성을 검사한다. |
| `conditional_item_ids` | 원문에서 조건부 추가 의도가 확인된 confirmation 항목. 슈퍼바이저가 Agent 1의 원문 분석 결과로 채운다. |
| `customer_questions` | Agent 2 문서 조사 이후에도 남은 고객 확인 사항. 메일에 별도 표시한다. |
| `pm_review` | 생략 또는 null. PM 승인 객체는 받지 않는다. |

대상은 다음과 같다.

1. 검증된 범위 밖 `change_request`.
2. 조건부 의도가 보존되어 있고 범위 밖인 `confirmation`.
3. 명시적 `estimate_request`도 검증된 범위 밖 근거가 있어야 산정한다. 기존 범위 내·기승인 변경은 새 견적 산정에서 제외하고, 근거 없음·판단 보류는 추가 조사를 요청한다. 별도 견적 문의는 `quote_links`로 실제 작업 항목에 연결하며 문의 자체는 중복 산정하지 않는다.

단순 확인·하자·진행 문의는 산정하지 않는다. `estimate_only`와 `estimate_request`를 임의로 동일하게 해석하지 않는다. 이 구분은 Agent 1과 공통 슈퍼바이저에서 확정해야 한다.

서버는 기준선·프로젝트·요청 context와 Agent 2 결과 ID를 검사하고 `apply_guards`로 실제 인용을 재검증한다. `verified=true`를 전달했다고 신뢰하지 않는다. 보류 항목과 산정 가능한 항목을 분리하고, 항목 또는 전체에 블로킹 질문이 있으면 해당 산정을 멈춘다. 필수 산정 요청을 선택 목록에서 빠뜨리면 추가 확인 상태로 남긴다.

LLM은 작업·직군·공수 기준 ID·가정을 제안한다. 서버가 기준 MD를 적용하고 `calc_cost`로 금액을 계산한다. 임시 기준표는 계속 합성 데이터이며 실제 견적 기준이 아니다.

## 현재 Agent 3 서비스 메서드

| 도구 | 동작 | 다음 단계 |
|---|---|---|
| `estimate_impact(input)` | 적합한 항목만 잠정 산정 | 반환된 needs·산정 결과로 슈퍼바이저가 선택 |
| `reestimate(session_id, request)` | 작업·가정·Agent 2 근거 변경 후 선택 항목 재산정 | 공유 산정 그룹까지 확대 |
| `recalculate(session_id, request)` | MD 수정·단가 변경 재계산 | LLM 호출 없음, 초안·승인 무효화 |
| `draft_documents(session_id, request)` | 잠정 결과로 고객 문서 생성 | PM 최종 검토 전 실행 가능 |
| `review(session_id, request)` | 동일 버전 묶음을 실제 PM이 승인·보류·반려 | 고객 미합의 상태 유지 |

`draft_documents.kind`는 `bundle`, `email`, `agreement`, `scope_reply`다. 변경 건은 bundle로 메일과 변경합의서 초안을 함께 만들고, 확인 건은 scope_reply로 메일 답변을 만든다. 혼합 요청도 메일 한 통으로 정리한다. 단독 문서 생성 결과만으로 변경 건 전체를 최종 승인할 수는 없다.

반환값: `session_id`, `state_revision`, `impact`, `needs`, `documents`, `work_rows`, `snapshot_hash`, `can_final_review`, `approval`, `history`.

### 추가 조사 신호

`needs`는 `{kind, item_ids, topic, target, source, question_id, blocking}` 목록이다. `target`은 `agent2`/`pm`/`client`/`none`이며, `source`는 생성 주체를 보존한다. 고객 문서에는 고객용 질문(`target=client`)만 포함한다. 표현에 특정 단어가 들어갔는지로 고객 질문을 추정하지 않는다.

- `more_evidence`: 계약 조건·제외 범위 부족. 슈퍼바이저가 Agent 2를 `focus=topic`으로 호출하고 새 결과로 해당 항목을 reestimate한다.
- `client_input`: 의도·규모·질문 답변 또는 누락 대상 확인. 슈퍼바이저가 기존 문서로 해결 가능한지 확인한 뒤 필요시에만 PM/고객에게 질문한다.
- `missing_standard`: 공수 기준 또는 단가표 보완 필요. 문서 조사로 대신 해결했다고 처리하지 않는다.
- `not_required`: 해당 항목은 산정 대상 아님. 범위 확인 답변에는 포함할 수 있다.

이 도구가 Agent 2나 PM 질문 창을 자동 호출하지는 않는다. `AgentFailure`/HTTP 502와 업무상 정보 부족을 구별한다.

`more_evidence` 때문에 미산정이어도 PM 필수 질문을 별도로 만들지 않는다. `needs.target=agent2`로 유지하여 슈퍼바이저가 문서 조사부터 진행하도록 한다. 일반 질문 ID는 요청 버전·기준선·대상 항목·필드·대상·질문 내용에서 결정하며, 목록 순서 변경으로 다른 질문에 같은 ID가 배정되지 않는다. 질문 의미나 요청 버전이 바뀌면 새 ID를 사용한다.

최초 산정과 부분 재산정은 동일한 대상 선정 규칙을 사용한다. 연결된 견적 문의를 직접 산정 대상에서 제외해도 누락으로 처리하지 않으며, 연결된 작업의 산정 조건은 계속 검사한다.

## 수정·공유 작업·승인

- MD 수정은 `work_rows.work_id`와 새 MD, 사유를 보낸다. MD는 유한한 0 이상·0.5 단위다. 작업 구성·가정 수정은 새 input과 item_ids로 reestimate한다.
- 같은 기준표 항목·직군을 썼다는 이유로 합치지 않는다. 실제 공유 작업만 `shared_work_key`를 명시한다. 같은 키의 작업·직군·MD·가정이 다르면 충돌로 거부한다.
- 공유 작업은 연결된 항목을 한 산정 그룹으로 합쳐 한 번만 계산한다. 해당 그룹 일부만 재산정하도록 요청해도 전체 그룹을 재산정한다. 새 결과가 다른 그룹과 공유 작업을 갖게 되면 병합해 다시 계산한다.
- 부분 재산정 시 관계없는 항목·판정은 그대로 보존한다. 전체 조건·답변·전역 질문이 바뀌면 모든 항목을 다시 산정해야 한다.
- 현재 단가표가 바뀌면 재계산 후 새 문서·승인이 필요하다. 공수 기준표 자체가 바뀌면 단가 재계산만으로 통과하지 않고 새 산정을 요구한다.
- 산정·문서 변경 시 이전 문서·승인은 이력에 보존하고 현재 효력을 해제한다. 새 세션이 생긴 과거 세션은 조회만 가능하다.
- 최종 승인은 Agent 2 판정·산정·문서·단가/공수 기준 fingerprint를 포함한 snapshot_hash와 revision을 검증한다. 미해결 필수 항목·미산정이 남으면 승인하지 않는다. 일정 미산정은 명시적으로 확인받는다.
- PM 최종 승인은 고객 합의·서명·발송·청구 승인이 아니다. 문서는 계속 `customer_unagreed_draft` 상태다.

## 서버 API

기본 경로: `/agent3/orchestration`.

- `POST /impacts`: ProvisionalAgent3Input
- `GET /{session_id}`: 상태·결과·이력
- `POST /{session_id}/documents`: `{state_revision, kind}`
- `POST /{session_id}/reestimate`: `{state_revision, input, item_ids}`
- `POST /{session_id}/recalculate`: `{state_revision, edits:[{work_id,md}], rate_card_id?, reason}`
- `POST /{session_id}/review`: `{state_revision, snapshot_hash, request_id, reviewer_ref, decision, reason, acknowledge_unknown_schedule}`
- `POST /{session_id}/documents/edit`: `{state_revision, kind, narrative, editor_ref, reason}`. PM 전용 문구 수정이며 수치·근거는 서버 사실 블록으로 유지한다.

실제 PM의 확인을 받은 신뢰된 호출부만 review를 호출해야 한다. 요청의 reviewer_ref 문자열은 인증 수단이 아니다. 슈퍼바이저 LLM의 도구 목록에 무제한 승인 권한을 주면 안 된다.

## 검증 및 기존 화면 연동

- 별도 도구 테스트 패널은 추가하지 않는다. 기존 영향 산정·승인·협의 화면과 개별 API 동작은 유지한다.
- `tests/test_agent3_orchestration.py`에서 잠정 산정·문서 생성·최종 검토·부분 재산정·공유 작업·버전 충돌을 검증한다. GPT·Gemini 공급자 연결은 모의 응답으로 확인하며 실제 외부 호출은 하지 않는다.
- 로컬 API 확인을 위한 `/prototype-lab/agent3-orchestration` 경로는 `--lab`에서만 제공한다. 키는 기존 서버 설정에서 읽으며 브라우저에 노출하지 않는다.
- 실제 슈퍼바이저 연결 시 서버의 `impact`, `documents`, `needs`, `approval`을 기존 화면에서 사용하는 표시 형식으로 매핑한다. 실제 PM 확인은 슈퍼바이저의 최종 검토 단계에서 받는다. 기존 개별 테스트의 중간 승인 버튼을 통합 경로에서 다시 요구하지 않도록 한다.
- `/analysis` 신규 요청 화면은 여전히 가상 시연이다. Agent 3 도구 구현만으로 슈퍼바이저 자동 호출이 완성된 것은 아니다.

## 후속 슈퍼바이저 연동

슈퍼바이저 구현 담당은 도구 반환의 session_id/revision/needs를 공통 케이스 상태에 연결하고, Agent 2 추가 조사와 PM 답변 후 적절한 도구를 재호출한다. 최종 검토에서는 최신 snapshot만 보여주고 실제 사용자 응답으로 review를 호출한다. 이미 구현된 프론트 가상 요청은 실제 에이전트 결과인 것처럼 전달하지 않는다.
