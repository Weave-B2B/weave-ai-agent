# 예시 값과 UUID 읽는 법

문서 v0.3 · 2026-10-28 · [전체 목록](./README.md)

`산정 승인 UUID`는 값이 아니라 **어떤 ID인지 설명하는 말**이었다. v0.2부터 값 예시에는 실제 UUID 형식의 가상 문자열을 사용한다.

UUID는 레코드를 구별하는 식별자다. 예를 들어 산정 승인 레코드의 impact_approval_id와 협의안의 source_impact_approval_id가 같은 문자열이면, 그 협의안이 **그 승인 기록을 참조한다**는 뜻이다. ID 문자열 자체에 ‘승인 완료’라는 의미나 금액이 들어 있는 것은 아니다. 승인 상태는 approval_status, 대상은 target_ai_analysis_id, 금액은 별도 산정 결과에서 확인한다.

## 연결 예시

| 테이블·컬럼 | 가상 값 | 의미 |
|---|---|---|
| impact_approval.impact_approval_id | `1b3bb920-965a-5265-8429-465d51ba1769` | 산정 승인 기록 한 건의 ID |
| proposal_version.source_impact_approval_id | `1b3bb920-965a-5265-8429-465d51ba1769` | 위 산정 승인 기록을 참조 |
| impact_approval.target_ai_analysis_id | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` | 승인 대상인 산정 결과 |
| proposal_version.source_ai_analysis_id | `6cf6857e-a7a3-5339-8f50-f84749dd5c86` | 협의안 생성 결과. 산정 결과와 다른 ID |

## 예시 ID 목록

같은 참조는 같은 값을 반복해서 사용했다. 실제 DB나 고객의 식별자가 아니며, seed 데이터·승인 증빙도 아니다. 이 값들을 운영에 복사하지 않는다. examples/agent3/input.json의 논리 ID와 자동으로 동일한 DB 키인 것도 아니다.

| 설명용 별칭 | UUID 가상 값 |
|---|---|
| company | `bf969411-9ca9-5808-9acc-a1aeeffd19d3` |
| project | `ca3df8e9-f107-5e5a-a1da-789f0baccdec` |
| member | `4e74e319-db07-5c8e-8ff4-84a8964e9db1` |
| baseline | `31377702-fcfb-598c-873c-32fcf25c07d2` |
| review | `e23104cf-7dee-57cc-8f0f-8f79e9ed33ad` |
| change_request | `c54a4a5f-49ff-560d-b347-15879ac724ed` |
| item | `e19e8619-b02e-5636-affb-2e433a2299d7` |
| stable_item | `524a2369-c9d6-576c-8b6c-9d7af6c58e15` |
| source | `f0190294-4a98-5e13-9d21-ee7d9c95bf62` |
| analysis_impact | `8bc12b27-4197-512c-8e92-0d35cbdd1cae` |
| analysis_draft | `6cf6857e-a7a3-5339-8f50-f84749dd5c86` |
| analysis_a1 | `008788bf-130e-55ba-90f8-37e92b58baa0` |
| role_be | `e2e16915-55ba-54ce-aec8-d43390270de2` |
| role_rate_be | `bb2ae32c-16f1-520b-8719-2c4d5c8edf76` |
| rate_card | `cd57ac4d-6b6c-5cfc-b1cf-3082d30992c6` |
| estimate_line | `655a2c8f-14e4-50c3-b5c1-b6b78dcbd9e7` |
| proposal | `04c8df9e-8186-5d8b-8dd3-a404cc6e4147` |
| question | `a86430c2-e813-56db-9755-ea2b79f85836` |
| approval | `1b3bb920-965a-5265-8429-465d51ba1769` |
| workflow | `09642ab4-0e62-545f-81a4-325de9262e75` |
| tool_call | `eb808c1b-a771-5f12-946a-5f68b9996662` |
| pm_judgment | `e7700acb-9893-5192-8b97-109ac2bc487f` |

## 다른 예시의 범위

- 시각은 ISO 8601의 가상 값이며 실제 작성·승인 시각이 아니다.
- 해시는 형식 설명용 64자리 값이다. 실제 예시 산정 내용에 대한 승인 검증 해시를 산출한 것이 아니다.
- NULL을 허용하는 열에 UUID 예시가 적혀 있어도 항상 값을 넣어야 한다는 뜻은 아니다. NULL 정책은 별도 열과 조건을 따른다.
- 상태 코드가 원본에서 확인되지 않은 곳은 ‘미정’이라고 적었다. 설명용 가상 enum을 확정된 코드처럼 만들지 않는다.
- JSON 열에는 실제 JSON 형식의 작은 참조 예시 또는 전체 데이터/구조 링크를 넣었다. 큰 입력·출력 객체의 일부만으로 완전한 실행 payload나 INSERT가 된다고 해석하지 않는다.
- 각 행의 예시는 타입과 연결을 설명한다. 여러 테이블의 모든 행을 묶은 완전한 실행 시나리오·정답셋은 아니다. hours_per_md의 8.0은 가상 형식 예시이며 운영 환산 기준의 확정이 아니다.
