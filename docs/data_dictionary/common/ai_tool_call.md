# ai_tool_call — calc_cost 호출 출처·입출력 기록

문서 v0.3 · 2026-10-28 · 케클업 / Weave · 공통 테이블 · 에이전트 3 연계 범위

[이 분류 목록](./README.md) · [전체 목록](../README.md) · [출처·판독 한계](../references/README.md) · [실행 JSON 대응](../agent3/runtime_mapping.md)

## 개요

v0.7에서 제안한 영속 도구 로그의 컬럼 후보다. 공통 관측/로그 저장소에서 충분히 보장하면 신규 업무 테이블 대신 사용할 수 있다. 금액이 실제 코드 계산으로 나온 것인지 추적한다.

현재 에이전트 3 연계에서 확인한 컬럼을 정리했다. 이 폴더의 분류는 전용 소유권을 뜻하지 않으며, 기존 ERD 전체 DDL 명세도 아니다.

**값 예시 안내:** UUID·시각·해시는 문서용 가상 값이다. 같은 의미의 참조 ID는 다른 문서에서도 동일하게 썼다. 각 행은 값의 형태를 설명하며 전체 INSERT 또는 실제 승인 기록이 아니다. 상태 코드가 확인되지 않은 곳은 미정으로 남겼다. [예시 ID 설명](../example_values.md) 참조.

## 추가 제안 컬럼

**아래 이름·타입·키·NULL 정책은 모두 제안이며 CN 합의·DB 구현 완료를 뜻하지 않는다.**

| 컬럼 | 타입 | 키 | NULL 허용 | 정의 | 값 예시 | 활용·작성 방식 |
|---|---|---|---|---|---|---|
| ai_tool_call_id | UUID | PK | 아니오(제안) | 호출 기록 ID | `eb808c1b-a771-5f12-946a-5f68b9996662` | 도구 호출 한 번마다 구분 |
| ai_analysis_id | UUID | FK → ai_analysis | 아니오(제안) | 관련 산정 실행 | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 현재 코드 result_id는 별도 매핑 |
| tool_name | VARCHAR(100) | — | 아니오(제안) | 도구 이름 | calc_cost | 출처 문자열만으로 호출 증명하지 않음 |
| tool_version | VARCHAR(50) | — | 아니오(제안) | 도구·계산 정책 버전 | 1 | 적용한 반올림·환산 계약을 추적 |
| input_ref | JSONB | — | 예(조건 제안) | 실제 계산 입력 또는 불변 참조 | `{"snapshot_ref":"doc-example:calc-input-001"}` | 해시만으로 재계산 입력 복원 불가 |
| output_ref | JSONB | — | 예(제안) | 실제 반환 또는 불변 참조 | `{"snapshot_ref":"doc-example:calc-output-001"}` | 반환 전 기술 실패면 null 가능 |
| input_hash | VARCHAR(128) | — | 아니오(제안) | 실제 입력 해시 | `37eef02926d424f1f3ee2dc718dd7b842d5bcd0e6623867f4fdea0973b36468a` | 입력 내용 비교 |
| output_hash | VARCHAR(128) | — | 예(제안) | 실제 반환 해시 | `a4528ec7815d44b051cf85d6e9b6e205f7ca5aa8fb83667c93fb3cd677d0ae89` | 반환 전 실패면 null |
| called_at | TIMESTAMPTZ | — | 아니오(제안) | 호출 시각 | `2026-10-28T10:00:00+09:00` | 호출 순서·운영 추적 |
| succeeded | BOOLEAN | — | 아니오(제안) | 함수 호출 성공 여부 | `true` | 정상 반환 NEEDS_INPUT도 함수 성공일 수 있음 |
| error_code | VARCHAR(100) | — | 예(제안) | 기술 오류 코드 | `null` | 오류 전문·비밀정보를 무분별하게 저장하지 않음 |

## 저장·갱신 규칙과 협의 사항

- v0.7에서 필요한 값은 제시했지만 위 물리 컬럼 이름은 새 구체화 후보다. CN의 공통 도구 로그와 맞추고 중복 테이블을 만들지 않는다.
- 현재 app/tools/trace.py는 TOOL_LOG 메모리 목록에 result_id/tool/version/in/out/at/ok를 남긴다. in/out은 일부 해시 문자열이며 실제 입력/반환 원문이나 영속 저장 구현이 아니다.
- 현재 로그의 ok=true는 함수가 반환했다는 뜻이다. 반환이 NEEDS_INPUT이면 계산 완료로 볼 수 없다. 실제 반환 상태도 보존한다.
- 로그에 접근 토큰·프롬프트 전문·자유 추론 전문을 넣지 않는다. 당시 단가·MD를 복원할 수 있는 입력 참조와 접근 권한·보존 기간을 합의한다.
