# review_version — 검토 업무 버전

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

변경 요청의 문서 기준선·항목·산정 검토를 묶는 기존 업무 버전이다. 모델 실행 result_id와 다른 식별자다. 에이전트 3는 이 버전의 산정 결과와 승인 내용을 연결한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| review_version_id | UUID | PK | 아니오 | 검토 버전 ID | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 산정·승인·협의안 연결 |
| change_request_id | UUID | FK → change_request | 아니오 | 대상 변경 요청 | `c54a4a5f-49ff-560d-b347-15879ac724ed` | 버전의 소속 |
| project_id | UUID | FK → project | 아니오 | 대상 프로젝트 | `ca3df8e9-f107-5e5a-a1da-789f0baccdec` | 다른 프로젝트 데이터 혼용 방지 |
| baseline_id | UUID | FK → baseline | 아니오 | 판단 기준선 | `31377702-fcfb-598c-873c-32fcf25c07d2` | 원본은 NOT NULL. 운영 산정 시 확정 기준선 연결 |
| review_version_no | INTEGER | —; UQ는 주석 | 아니오 | 요청 내 검토 순번 | 2 | UQ(change_request_id, review_version_no)는 원본 컬럼 주석 |
| review_status | VARCHAR(30) | — | 아니오 | 검토 진행 상태 | 미정 — CN 상태 코드 확인 후 작성 | 전용 phase와 매핑 필요 |
| review_commercial_decision | VARCHAR(30) | — | 예 | 검토 권고 방향 | 미정 — CN 상태 코드 확인 후 작성 | 범위 판단 및 상업적 결정과 구분 |
| commercial_reason | TEXT | — | 예 | 상업적 판단 사유 | 고객 관계에 따른 무상 검토 | AI가 자동 확정하지 않음 |
| schedule_status | VARCHAR(30) | — | 아니오 | 일정 산정 상태 | 미정 — CN 상태 코드 확인 후 작성 | 공통 JSON의 상태값과 동일하다고 가정하지 않음 |
| additional_days | NUMERIC(10, 2) | — | 예 | 추가 소요일 | null | 미산정은 null, 실제 추가 없음은 0 |
| schedule_assumptions | TEXT | — | 예 | 일정 가정 | 인력 배정 확인 필요 | Schedule.assumptions를 가독성 있게 투영 |
| completion_required | BOOLEAN | — | 아니오 | 이행 관련 정책 플래그 | `true` | A3가 임의 확정하지 않음 |
| acceptance_required | BOOLEAN | — | 아니오 | 검수 관련 정책 플래그 | `true` | AI 실행 완료와 구분 |
| review_content_hash | CHAR(64) | — | 예 | 검토 내용 해시 | `903bb513b78ece9b0bb22a45c1ae00a0a579a83111ee4508023eb80a60b9c4a8` | 산정 승인 해시와 적용 범위 협의 |
| frozen_at | TIMESTAMPTZ | — | 예 | 동결 시각 | `null` | 동결 이후 변경은 새 업무 버전 |
| created_at | TIMESTAMPTZ | — | 아니오 | 버전 생성 시각 | `2026-10-28T10:00:00+09:00` | 모델 실행 시각과 구분 |
| created_by | UUID | FK 주석 → company_member (제약문 없음) | 아니오 | 버전 생성자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 원본 FK 정의·주석과 기존 사용자 참조 기준 활용 |

## 저장·갱신 규칙과 협의 사항

- 동결된 승인 대상 수정은 CR-09·D-01·D-08의 새 검토 버전·재승인 방향을 활용한다. 미동결 버전의 모든 모델 재실행에 새 review_version을 만들지는 않는다.
- 실제 승인한 작업·공수·금액·일정·가정이 바뀌면 기존 산정 승인은 재사용하지 않는다. 해시의 포함 필드·정규화·무효화 범위는 CN과 맞춘다.
- Schedule은 현재 결과에서 산정 그룹별로 존재한다. 여러 그룹을 review_version의 단일 additional_days로 합치는 규칙은 미확정이며 단순 합계하지 않는다. 그룹별 원본은 결과 JSON에 유지한다.
- 이 테이블의 기존 필수 필드 전체를 에이전트 3가 작성한다는 뜻은 아니다. 기존 BE 생성·권한·정책을 따른다.

## 실행 데이터와의 관계

Context.request_revision은 요청 원문 revision이다. review_version_no, meta.result_revision, state_revision과 서로 대체하지 않는다.
