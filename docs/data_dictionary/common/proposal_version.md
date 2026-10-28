# proposal_version — 협의안 초안 버전

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

산정 PM 승인을 받은 내용으로 작성한 협의안 본문·금액·버전을 담는 기존 테이블이다. 이 MVP의 산출물은 내부 검토용이며 작성 자체가 내부 상업 승인·고객 합의·발송·청구를 뜻하지 않는다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| proposal_version_id | UUID | PK | 아니오 | 협의안 버전 ID | `04c8df9e-8186-5d8b-8dd3-a404cc6e4147` | 이후 internal_approval의 승인 대상 |
| review_version_id | UUID | FK → review_version | 아니오 | 근거 검토 버전 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 산정 승인과 같은 버전 |
| proposal_version_no | INTEGER | — | 아니오 | 협의안 순번 | 1 | 검토 버전 순번과 별개. UQ 범위 확인 |
| proposal_text | TEXT | — | 아니오 | 협의안 본문 | 리뷰 기능 변경 협의안 | 승인된 범위·근거·공수·일정·금액·질문을 조립 |
| proposed_amount | NUMERIC(18, 2) | — | 아니오 | 판매 제안금액 | 6425000 | 승인한 코드 계산 결과. 합성 예시 |
| proposal_currency | VARCHAR(3) | — | 아니오 | 통화 | KRW | 당시 계산 통화와 일치 |
| proposal_content_hash | CHAR(64) | — | 예 | 협의안 본문 승인용 해시 | `5d4e5c7e86c462ccc5375d7d2809e1e52417be654cb067766f532a144e503598` | 산정 승인 해시와 구분 |
| frozen_at | TIMESTAMPTZ | — | 예 | 협의안 동결 시각 | `null` | 동결 이후 변경 정책 적용 |
| created_at | TIMESTAMPTZ | — | 아니오 | 생성 시각 | `2026-10-28T10:00:00+09:00` | 본문 버전 이력 |
| created_by | UUID | FK 주석 → company_member (제약문 없음) | 아니오 | 생성자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 자동 생성의 업무 주체 기록 방식 CN 확인 |

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| source_impact_approval_id | UUID | FK → impact_approval | 예(제안) | 생성 근거 산정 승인 | `1b3bb920-965a-5265-8429-465d51ba1769` | 신규 A3 협의안에는 유효한 승인 연결 |
| source_ai_analysis_id | UUID | FK → ai_analysis | 예(제안) | 협의안을 생성한 실행 결과 | `6cf6857e-a7a3-5339-8f50-f84749dd5c86` | 산정 실행 결과와 구분 |

## 저장·갱신 규칙과 협의 사항

- 승인 대상 산정 결과는 impact_approval.target_ai_analysis_id로 찾는다. source_ai_analysis_id는 협의안 생성 단계의 기록을 참조하는 제안이다. 분석 행 단위 합의 전 두 값을 같은 것으로 취급하지 않는다.
- 신규 A3 협의안은 승인 대상 버전·해시와 일치해야 한다. 과거/수동 작성 레코드의 참조 NULL 정책은 별도 결정한다.
- 기존 proposed_amount는 NOT NULL이다. 금액 미산정 협의안을 허용하려면 nullable+명시적 미산정 상태 변경을 합의해야 하며, 그 전에는 저장을 보류한다. 임의 0원으로 채우지 않는다.
- 원가·마진·내부 질문이 포함될 수 있다. 고객용 필드 선택·발송은 별도 승인 경계다. 내부 검토용 초안의 review_required=true, billing_authorized=false를 유지한다.
- 현재 코드에는 동일 Draft에 여러 산정 그룹의 금액이 문자열로 나올 수 있다. 단일 proposed_amount의 집계 범위·중복 제거는 저장 어댑터에서 공통 합의 후 처리한다.

## 실행 데이터와의 관계

Draft.summary/scope/impact/cost/open_questions를 proposal_text로 렌더링하는 안이다. source_result_ids는 출처 목록으로 보존하며 문자열 목록 자체가 물리 FK는 아니다. [JSON 정의](../agent3/json_fields.md) 참조.
