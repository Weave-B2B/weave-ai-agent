# change_request — 변경 요청과 현재 검토 버전

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 요청 관리 · 에이전트 3 입력 참조

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

변경 요청 한 건과 현재 검토 버전을 식별하는 기존 테이블이다. 에이전트 3는 연결된 항목·범위 PM 검토·산정 결과를 받아 사용하며, 요청 제목·우선순위를 임의 변경하지 않는다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 기존 ERD에서 활용하는 컬럼

**ERDCloud v0.6에서 내보낸 PostgreSQL SQL과 대조했다.** 타입·NULL·PK/FK는 내보내기 정의 기준이며 실제 DB 적용 검증은 아니다. 기본값은 원본에 없으면 임의 지정하지 않는다. UQ·CHECK 주석은 실행 제약과 구분한다.

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| change_request_id | UUID | PK | 아니오 | DB 변경 요청 ID | `c54a4a5f-49ff-560d-b347-15879ac724ed` | Context.case_id와 매핑 |
| project_id | UUID | FK → project | 아니오 | 대상 프로젝트 | `ca3df8e9-f107-5e5a-a1da-789f0baccdec` | 회사·프로젝트 접근 확인 |
| company_member_id | UUID | FK → company_member | 예 | 담당 구성원 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 기존 업무 역할 확인 |
| review_version_id | UUID | FK → review_version | 예 | 현재 검토 버전 포인터 | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` | 워크플로 체크포인트와 별개 |
| cr_number | VARCHAR(100) | —; UQ는 주석 | 아니오 | 업무용 CR 번호 | CR-A-001 | 프로젝트 내 유일성은 주석 규칙 |
| change_request_title | VARCHAR(500) | — | 아니오 | 변경 요청 제목 | 리뷰 기능 추가 요청 | 화면 식별 |
| priority | VARCHAR(30) | — | 아니오 | 요청 우선순위 | 미정 — CN 상태 코드 확인 후 작성 | 산정 가능 여부와 구분 |
| progress_status | VARCHAR(30) | — | 아니오 | 요청 전체 진행 상태 | 미정 — CN 상태 코드 확인 후 작성 | A1 READY나 A3Status를 그대로 대입하지 않음 |
| needs_reanalysis | BOOLEAN | — | 아니오 | 재분석 필요 표시 | `false` | 상위 내용 변경 시 처리 정책 확인 |
| created_at | TIMESTAMPTZ | — | 아니오 | 요청 생성 시각 | `2026-10-28T10:00:00+09:00` | 원문 received_at과 구분 |
| created_by | UUID | FK 주석 → company_member (제약문 없음) | 아니오 | 생성자 | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` | 원본 FK 정의·주석과 기존 사용자 참조 기준 활용 |

## 저장·갱신 규칙과 협의 사항

- case_id=CR-A-001 같은 문자열, change_request_id UUID, cr_number의 대응은 공통으로 정한다. 단독 cr_number를 전사 유일키로 보지 않는다.
- A3가 받는 received_at은 request_source의 원문 수신 시각이다. 이 테이블의 created_at으로 대체하지 않는다.
- 최초 요청·추가 원문·PM 메모·첨부는 request_source/request_note/request_attachment의 책임이다. A3는 호출부가 조립한 버전 고정 입력으로 사용한다.
