# impact_approval — 협의안 작성 전 산정 PM 승인

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 에이전트 3

[목록](./README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](./runtime_mapping.md)

## 개요

v0.7에서 제안한 신규 테이블이다. PM이 에이전트 3의 작업·공수·금액·일정·가정을 확인하고 내린 결정을 남긴다. 승인 대기는 워크플로가 관리하고, 이 테이블에는 제출된 결정을 기록하는 안이다. 기존 범위 PMReview 및 협의안 작성 후 internal_approval과 대상·시점이 다르다.

에이전트 3 관련 컬럼을 정리한 문서다. 기존 ERD 전체 DDL 명세는 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| impact_approval_id | UUID | PK | 아니오(제안) | 산정 검토 결정 ID | `1b3bb920-965a-5265-8429-465d51ba1769` | 서버 발급 |
| review_version_id | UUID | FK → review_version | 아니오(제안) | 검토 대상 업무 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 산정 결과와 일치 |
| target_ai_analysis_id | UUID | FK → ai_analysis | 아니오(제안) | PM이 확인한 산정 결과 | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 현재 선택된 결과인지 검사 |
| company_member_id | UUID | FK → company_member | 아니오(제안) | 결정한 PM | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 인증·회사·산정 승인 권한 검사 |
| approval_status | VARCHAR(30) | — | 아니오(제안) | 결정 상태 | `APPROVED` | CHANGES_REQUESTED 등 전체 enum 협의 |
| approval_reason | TEXT | — | 예(조건 제안) | 결정 이유 | 공수 및 미산정 일정 확인 | 반려·수정 요청에는 이유 기록 |
| approved_content_hash | VARCHAR(128) | — | 예(조건 제안) | 승인 대상 내용 해시 | `903bb513b78ece9b0bb22a45c1ae00a0a579a83111ee4508023eb80a60b9c4a8` | APPROVED이면 비워둘 수 없음 |
| decided_at | TIMESTAMPTZ | — | 아니오(제안) | 결정 시각 | `2026-10-28T10:05:00+09:00` | 현재 클라이언트 임의 시각을 신뢰하지 않음 |
| request_id | VARCHAR(100) | 복합 UQ 제안 | 아니오(제안) | 결정 요청 중복 방지값 | `impact-approve-CR-A-001-001` | UQ(review_version_id, request_id) 후보 |
| state_revision_at_review | INTEGER | — | 아니오(제안) | PM 화면이 확인한 상태 순번 | 1 | 최신 상태와 다르면 충돌 |

## 저장·갱신 규칙과 협의 사항

- 승인 시 버전·선택 결과·내용 해시·상태 순번이 모두 현재 대상과 맞아야 한다. 이후 내용을 바꾸면 기존 승인을 재사용하지 않는다.
- 같은 request_id라도 결정 내용이 다르면 충돌이다. 요청 해시 또는 정규화된 원본 결정 payload가 필요하며, v0.7은 물리 컬럼을 확정하지 않았다. 아래 JSON 보완으로 그 계약을 남긴다.
- **딕셔너리 작성 중 확인된 v0.7 후속 보완:** 현재 로컬 코드는 미산정 일정 확인값 acknowledge_unknown_schedule과 요청 해시 request_hash를 보관한다. 운영 승인에도 이 확인 근거·멱등 비교 payload를 영속 보존할 필요가 있다. 이번에는 별도 확정 DB 컬럼으로 단정하지 않고 승인 상세 JSON/공통 감사 기록 또는 신규 컬럼 중 CN 협의 대상으로 둔다. v0.7 원문은 수정하지 않았다.
- 원본 산정 결과 연결·해시만으로 당시 PM 수정본이 복원되지는 않는다. 승인 대상 전체 snapshot 또는 불변 결과 참조를 함께 유지한다.
- 미산정 일정 승인 허용은 현재 로컬 동작과 운영 정책을 구분한다. 운영에서 같은 정책을 채택할지는 미확정이다. company_member.can_approve가 산정 승인에도 적용되는지 확인한다.
- 기존 승인 테이블을 확장해 동일한 관계·이력을 보장할 수 있다면 대체 가능하다. DB 미구현 상태다.

## 실행 데이터와의 관계

현재 ReviewImpact와 승인 dict의 target_result_id/snapshot_hash/reviewer_ref/reviewed_at/approved를 DB UUID·해시·결정 상태로 매핑하는 후보안이다. reviewer_ref 문자열은 인증된 company_member가 아니다. [실행 대응표](./runtime_mapping.md) 참조.
