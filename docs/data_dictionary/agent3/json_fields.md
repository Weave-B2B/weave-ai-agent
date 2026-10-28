# 에이전트 3 JSON 하위 필드

문서 v0.3 · 2026-10-28 · [목록](./README.md) · [공통 스키마](../../../schema/schemas.py)

이 문서는 **DB 컬럼이 아니라 JSON 내부 필드**를 설명한다. ai_analysis.result·당시 입력 snapshot 또는 불변 저장소에 보존한다. 공유 모델에 없는 필드를 Agent3Input/Output에 임의 추가하지 않는다. NULL·기본값은 공통 Python 모델 기준이며 DB 제약과 별개다.

## 산정 결과 — Agent3Output

| 경로 | 타입·기본값 | 의미·활용 |
|---|---|---|
| context | 객체 | 프로젝트·요청·원문 revision·기준선·분석 흐름. 논리 ID는 DB UUID와 매핑 |
| meta | 객체 | 스키마/결과/모델/에이전트 버전과 출처. 운영 결과 연결 시 result_id 확정 |
| status | 문자열 | IMPACT_DRAFT_READY / NEEDS_INPUT / WAITING_REVIEW / NOT_REQUIRED / ERROR |
| estimates | 배열 | 산정 그룹별 결과. NOT_REQUIRED이면 빈 배열 |
| questions | 배열, 기본 [] | 에이전트 3가 생성한 질문만. 앞 단계 질문 복제 금지 |
| error | 객체 또는 null | 기술 실패 정보. ERROR에서 사용하며 업무상 보류와 구분 |
| draft | 객체 또는 null | 산정 단계에서는 null. 승인 뒤 생성한 내부 초안 |
| review_required | true | 사람 검토가 필요한 결과 |
| billing_authorized | false | 이 출력이 청구 허가를 뜻하지 않음 |

## 산정 그룹·작업·금액·일정

| 경로 | 타입·기본값 | 의미·활용 |
|---|---|---|
| estimates[].item_ids | 문자열 배열 | 이 산정 그룹의 논리 항목 ID |
| estimates[].status | draft / not_estimable | 산정 초안 또는 산정 불가. workflow.phase와 구분 |
| estimates[].lines | 배열, 기본 [] | 작업·직군별 공수. not_estimable이면 비움 |
| lines[].task | 문자열 | 추가 작업 설명 → estimate_line.task |
| lines[].item_ids | 문자열 배열 | 작업이 관련된 항목들. 단일 request_item FK와 차이 주의 |
| lines[].role | PM / UX / FE / BE / QA | DB job_role와 매핑 |
| lines[].md | 숫자 | 0 이상·0.5 단위. 모름을 0으로 쓰지 않음 |
| lines[].assumption | 문자열, 기본 빈 문자열 | 작업 산정 가정 → assumptions |
| estimates[].rate_card_id | 문자열 | 적용한 단가 버전의 논리 ID |
| estimates[].totals | 객체 또는 null | calc_cost의 실제 반환 합계. 미완성 합계를 완성 총액으로 쓰지 않음 |
| estimates[].totals_source | calc_cost 또는 null | 코드 계산 출처. 이 문자열만으로 실제 호출 증명은 불충분 |
| totals.md / md_by_role | 숫자 / 객체 | 총 MD·직군별 MD |
| totals.cost / price | 정수 | KRW 원가·판매 산정가. VAT 별도 |
| totals.margin | 숫자 또는 null | (price-cost)/price. price=0이면 null |
| totals.currency / vat_included | KRW / false | 현재 공통 모델이 지원하는 통화·부가세 조건 |
| totals.rate_card_id | 문자열 | 금액 계산에 쓴 단가표 |
| schedule.business_days | 숫자 또는 null | null=일정 영향 미산정. MD를 달력일로 단순 환산하지 않음 |
| schedule.basis / assumptions | 문자열 배열, 기본 [] | 일정 근거·가정. 실제 값이 있으면 보존 |
| schedule.reason | 문자열, 기본 빈 문자열 | 일정 미산정·산정 불가 사유 |

현재 calc_cost는 직군별 MD×KRW/MD 단가를 합계 후 원 단위 ROUND_HALF_UP, margin은 소수 4자리 ROUND_HALF_UP으로 계산한다. HOUR 자동 환산은 없다. 이는 현재 코드 동작이며 CN 계산 API 계약 확정과는 다르다.

## 입력 snapshot — Agent3Input

| 경로 | 타입 | 보존 목적 |
|---|---|---|
| context | 객체 | 대상 프로젝트·요청·원문 revision·판단 기준선 |
| items | Item 배열 | PM 확인 후 산정 항목. item_id/type/intent/text/spans/source/inferred 보존 |
| constraints | Item 배열, 기본 [] | 일정·제약·의존 문맥. 별도 기능 공수로 중복 계산하지 않음 |
| a2_result | Agent2Output | 범위 판단·근거·불확실성·승인된 변경 참조의 불변 원본 |
| pm_review | PMReview | 범위 PM 검토. target_result_id/항목별 pm_label/estimate 선택과 이유 |
| answers | Answer 배열, 기본 [] | 이번 산정에 실제 사용한 답변·출처·검증 상태 |
| rate_card_id | 문자열 | 적용할 고정 단가 버전. ID만으로 당시 내용이 복원된다고 가정하지 않음 |
| received_at | 문자열 시각 | 요청 원문 수신 시각. 모델 호출 시각과 별개 |

A2 인용의 doc_id/doc_version/section_id/page/line_start/line_end/locator/extraction_version/quote/role/match_type/verified를 보존한다. verified는 true/false/null을 구분하며, 현재 협의안 코드는 true인 인용만 복사한다. label이나 section_id만 남기는 방식으로 축약하지 않는다.

## 질문·답변

| 경로 | 타입·허용값 | 의미·활용 |
|---|---|---|
| Question.question_id / case_id / stage | 문자열 / agent1·2·3 | 원래 질문 식별·발생 단계 |
| Question.raised_by / item_ids | agent·pm / 문자열 배열 | 생성 주체·대상 항목 |
| Question.field / text / reason | 문자열(null 허용 필드 별도) | 부족 정보·질문 본문·질문 이유 |
| Question.basis_refs / target | 문자열 배열 / pm·client | 근거 참조·답변 대상 |
| Question.blocking | boolean, 기본 false | 진행 차단 여부. 적용 단계·승인 정책은 공통 합의 |
| Question.status | open / answered / resolved / dismissed | 답변 접수와 해결을 구분 |
| Answer.answer_id / question_id | 문자열 | 불변 답변 이벤트와 질문 연결 |
| Answer.text / answered_by / answered_at | 문자열 / pm·client / 시각 | 답변 내용·주체·시각 |
| Answer.source_ref / verified | 문자열 또는 null / boolean | 원본 채널 증빙·검증 여부 |

원래 Question.status 4상태와 기존 DB question_status OPEN/ANSWERED는 다른 계약이다. 복수 item_ids·판정 이력을 원본 JSON 또는 공통 연결 구조에 유지한다. gold_origin 등 평가 전용 필드는 운영 출처로 쓰지 않는다.

## 협의안 — Draft

| 경로 | 타입·기본값 | 의미·활용 |
|---|---|---|
| summary | 문자열 | 내부 검토용 초안 제목·요약 |
| scope | 문자열 | A2 및 PM 검토의 범위·검증된 계약 근거 복사 |
| impact | 문자열 | 승인된 작업·MD·일정·가정 |
| cost | 문자열 또는 null | 코드 계산한 판매 금액 표기. 새로운 금액 추론 금지 |
| open_questions | 문자열 배열, 기본 [] | 남아 있는 질문 표시 |
| source_result_ids | 문자열 배열, 기본 [] | A2·범위 PMReview·산정·산정 승인 참조. 자동 물리 FK는 아님 |

현재 전용 workflow는 Draft를 별도 view에 보관한다. JSON 안에 없던 새 승인 필드를 공통 Agent3Output에 추가한 구현이 아니다. proposal_version.proposed_amount는 표시 문자열에서 모델로 다시 추론하지 말고 승인된 계산값에서 가져온다.
