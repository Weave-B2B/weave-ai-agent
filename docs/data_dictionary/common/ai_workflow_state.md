# ai_workflow_state — PM 승인 대기·재개 체크포인트

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

v0.7의 공통 영속 상태 제안을 컬럼 후보로 풀어 쓴 문서다. 같은 역할의 오케스트레이션 체크포인트가 있으면 재사용한다. 신규 테이블 채택이나 물리 컬럼이 합의된 것은 아니다. 현재 코드의 프로세스 메모리를 DB 구현으로 오해하지 않는다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| workflow_id | UUID | PK | 아니오(제안) | 워크플로 상태 ID | `09642ab4-0e62-545f-81a4-325de9262e75` | 로컬 session_id와 대응 관계 협의 |
| change_request_id | UUID | FK → change_request | 아니오(제안) | 대상 변경 요청 | `c54a4a5f-49ff-560d-b347-15879ac724ed` | 회사·프로젝트 범위까지 검사 |
| review_version_id | UUID | FK → review_version | 아니오(제안) | 현재 업무 검토 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 현재 검토 버전 포인터만으로 단계 상태를 대체하지 않음 |
| phase | VARCHAR(40) | — | 아니오(제안) | 현재 처리 단계 | WAITING_IMPACT_APPROVAL | 코드 전용 enum의 운영 채택은 협의 |
| wait_reason | TEXT | — | 예(제안) | 대기 사유 | PM 산정 검토 대기 | 질문·오류·승인 대기를 구분 |
| resume_step | VARCHAR(40) | — | 예(제안) | 다음 재개 위치 | GENERATE_DRAFT | 단계 이름은 공통 합의 |
| current_ai_analysis_id | UUID | FK → ai_analysis | 예(제안) | 현재 선택 산정 결과 | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 생성 전 null. 초안 실행 결과와 혼용하지 않음 |
| current_impact_approval_id | UUID | FK → impact_approval | 예(제안) | 현재 유효 산정 결정 | `1b3bb920-965a-5265-8429-465d51ba1769` | 수정 시 새 승인 필요 |
| current_proposal_version_id | UUID | FK → proposal_version | 예(제안) | 현재 협의안 | `04c8df9e-8186-5d8b-8dd3-a404cc6e4147` | 승인·생성 전 null |
| state_revision | INTEGER | — | 아니오(제안) | 상태 변경 순번 | 1 | 원자적 비교·갱신으로 경합 방지 |
| open_question_refs | JSONB | — | 예(제안) | 열린 질문 참조 목록 | `[]` | 논리 ID 목록은 물리 FK 제약이 아님 |
| recovery_input_ref | JSONB | — | 예(조건 제안) | 복구용 불변 입력 참조 | `{"ai_analysis_id":"8bc12b27-4197-512c-8e92-0d35cbdd1cae","field":"input_snapshot"}` | 다른 곳에서 완전히 복구 못 하면 보관 |

## 저장·갱신 규칙과 협의 사항

- 물리 컬럼 이름·타입은 이 딕셔너리의 구체화 후보다. 행 생성/갱신 시각·이력 저장소 등 공통 관리 필드는 CN과 정한다. 전체 DDL 확정본은 아니다.
- current 포인터는 과거 결과·승인·협의안 이력을 지우지 않고 이동한다. 같은 요청의 여러 실행 중 무엇이 현재인지와 유일성 범위를 정해야 한다.
- 저장·상태 갱신·이벤트 발행의 원자성, 중복 처리, 재시작 복구를 오케스트레이션·CN 공통으로 설계한다. 현재 코드의 RLock은 다중 worker/서버 간 동시성 제어가 아니다.
- 계층별 current review_version 포인터와 workflow checkpoint는 역할이 다르다. ERD execution은 실제 업무 이행 테이블이다.
- 현재 코드 phase는 WAITING_INPUT / WAITING_UPSTREAM_REVIEW / WAITING_IMPACT_APPROVAL / DRAFT_READY / NOT_REQUIRED / FAILED다. 업무 공통 RunStatus나 A3Status와 같은 enum이 아니다.

## 실행 데이터와의 관계

현재 Agent3Workflow._Session(inp, view, rate_hash, history), _current[(project_id, case_id)]를 메모리에 보관한다. DB에 영속화된 workflow_id는 아직 없다. 당시 단가 fingerprint도 복구할 수 있는 값/불변 단가 참조로 보존해야 한다.
