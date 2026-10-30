# Agent 3 — 단가표·LLM 연결·협의안 흐름 준비

작성일: 2026-10-28 / Asia/Seoul. 이번 목표는 **공수 제안 → 단가표 기반 계산 → PM 확인·수정·승인 → 내부 협의안 초안**이다.
이 문서는 구현된 연결 준비와 후속 계획을 구분한다. API 키는 아직 받지 않았고 실제 외부 모델을 호출하지 않았다. 테스트는 HTTP mock과 합성 입력으로 수행했다.

## 현재 코드에서 확인한 것

- `app/agents/agent3/prompts.py`: LLM은 작업·직군·MD·가정·질문만 제안한다. 단가·금액·마진·일정 일수·협의안을 모델이 생성하지 못하도록 한다.
- `app/agents/agent3/draft.py`, `agent.py`: 제안 JSON 스키마, 대상 번호와 범위, 0.5 MD 단위, 미산정·질문을 검증한다.
- `app/tools/calc_cost.py`: 직군별 MD와 선택한 단가표로 원가·판매 산정가·마진을 코드 계산한다.
- `app/agents/agent3/workflow.py`: 승인 전 초안 없음, 수정 시 새 결과와 재승인, 승인 시 해당 결과로 내부 초안 조립, 단가표 fingerprint 변경 시 기존 승인 차단을 구현한다.
- `app/llm/local.py`: 이름은 local이지만 `/chat/completions`와 Bearer 인증을 사용하는 OpenAI 호환 HTTP 클라이언트다. API 주소·모델·키와 제공업체 호환성을 확인해야 한다.
- `prototype/run_demo.py`: fake를 기본값으로 유지하고 명시적 Gemini/API 모드, 무호출 `--check`, 외부 단가 파일 선택을 지원한다.

기존 계약의 자세한 사용법은 [Agent 3 MVP](agent3-mvp.md), 화면 범위는 [구현 계획](../prototype/implementation-plan.md)을 따른다.

## 단가표는 계산용 구조화 데이터로 관리

단가의 원본은 임베딩 검색 결과가 아니라 **검증된 직군별 단가 데이터**로 두는 것을 권장한다.
LLM이 제안한 작업·직군·공수에 정확한 버전의 단가를 조회해 계산한다. RAG 검색으로 비슷한 가격을 찾아 금액을 확정하지 않는다.

| 받은 자료 | 적용 준비 방식 | 이번 단계 |
|---|---|---|
| 기존 형식의 JSON | 표 ID·직군·단가·단위·VAT 기준을 검증하고 외부 파일 경로로 지정 | 기존 경로 재사용 및 숫자·단위·VAT 검증 구현 |
| CSV/Excel | 열/직군/단위 매핑 → 구조화 변환 → 담당자 확인 → 불변 버전으로 확정 | 추후 수신 형식이 정해지면 구현. 새 업로드 서버를 지금 만들지 않음 |
| PDF/계약서/이미지 | 표 추출 → 숫자/통화/인일 단위 확인 → 담당자 검토 → 동일 구조로 확정 | 추출 결과를 자동 견적 원본으로 확정하지 않음 |
| 설명·계약 근거 | 필요하면 문서 검색으로 근거를 찾음 | 검색 근거와 계산 단가의 원본 역할을 분리 |

현재 형식은 `rate_cards` 아래 표 ID, 그 아래 `rates`와 직군별 `cost`·`price`다.
현재 계산은 KRW/MD·VAT 별도 기준이다. 다른 통화·시간 단위·VAT 포함 가격은 명시적인 변환 없이 그대로 적용하면 안 된다.
직군별 단가가 없으면 부분 합계를 완성 견적으로 표시하지 않고 입력 필요 상태로 둔다.

외부 표를 받으면 `RATE_CARD_FILE`로 **저장소 밖 파일**을 지정하고 입력의 `rate_card_id`로 표를 선택한다.
새 버전은 새 ID와 내용 해시로 구분하며, 사용한 버전을 산정·승인과 연결한다. 현재 memory MVP의 fingerprint 검사는 영속 버전 보관을 대신하지 않는다.
운영에서 실제 파일/버전을 보관할 책임과 업로드 API는 CN 담당과 후속 합의한다. 이번에는 DB/업로드 업무 서버를 새로 설계하지 않는다.

## Gemini 연결 준비 결과

2026-10-29 공식 문서 확인 기준 선택한 stable model ID는 `gemini-2.5-flash-lite`다.
고정 preset을 사용하며, 향후 모델 변경은 `--model`로 사용자가 명시적으로 선택한다.
이 모델은 structured outputs를 지원한다. Google은 과거 2.5 모델을 사용한 계정에 접근을 제한한다고 안내하므로 키별 사용 가능 여부를 확인해야 한다. [Google 모델 문서](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite).

기존 `LocalLlm` HTTP 클라이언트를 재사용한다. `LLM_PROVIDER=local`이라는 내부 이름은 유지되며
API metadata는 `local:gemini-2.5-flash-lite`다. 새 SDK·대형 provider·업무 서버를 추가하지 않았다.
공식 호환 API의 base URL은 `https://generativelanguage.googleapis.com/v1beta/openai/`,
인증은 Gemini 키의 Bearer 헤더이고 `/chat/completions`를 호출한다.
[Google OpenAI 호환 API](https://ai.google.dev/gemini-api/docs/openai).

실행은 [실행 안내](../prototype/run.md)의 `--mode gemini` (`api` 별칭)를 사용한다.
`GEMINI_API_KEY` > `LLM_API_KEY` 순으로 서버 환경 키를 선택한다. 키를 화면·로그·보고서에 쓰지 않는다.
이전 provider의 환경 설정이 선택을 바꾸지 않도록 endpoint/provider/JSON 모드는 preset으로 덮어쓴다.
모델 선택은 `--model` > preset이며 기존 `LLM_MODEL` 값은 무시한다. A1/A2는 fixture를 유지한다.
`--check`는 HTTP client·서버를 만들지 않고 설정·단가표 형식만 검증한다. 인증 성공 검사는 아니다.
실패 시 fake fallback이 없다. 실제 API 실행은 이번에 수행하지 않았다.

### JSON Schema와 로컬 검증 경계

요청은 `response_format.type=json_schema`, `json_schema.strict=true`를 사용한다.
Google endpoint에만 Pydantic `$ref`를 인라인하고 `default`를 제거하며 모든 property를 명시적 required,
`additionalProperties=false`로 정규화한다. 다른 endpoint의 기존 prompt/schema 모드는 유지한다.
이는 optional/default로 인한 strict-envelope 불일치를 줄이기 위한 구현 결정이다.
Google이 이 정확한 Agent 3 스키마를 실제로 수락했다는 증거는 HTTP mock만으로 얻을 수 없다.

Gemini는 JSON Schema 일부를 지원하며 문자열 길이 등의 제약을 생성 시 강제하지 않을 수 있다.
응답은 원본 Pydantic 모델로 다시 검증하고, 코드에서 0.5 MD·대상 범위·전 대상 정확히 한 번 포함·
추가 금액 필드 금지를 검사한다. 잘못된 출력은 한 번 수정 요청 후 `OUTPUT_INVALID`다.
400/401은 provider 거부 오류, timeout은 재시도 가능 `MODEL_TIMEOUT`으로 표시하고 내부 오류·키는 숨긴다.
[Google structured outputs 및 한계](https://ai.google.dev/gemini-api/docs/structured-output).
실제 schema 수락·모델 공수 품질·비용·지연·안전 차단·토큰 제한은 키를 받은 뒤 별도 검증할 항목이다.

## 프롬프트와 승인 책임

입력은 PM이 산정 대상으로 선택한 요구, 제약, 확인된 답변이다. 원문의 지시는 비신뢰 업무 데이터로 취급한다.
모델은 대상 번호를 보존하고 작업·직군·공수·가정·필요한 질문을 JSON으로 제안한다.
부족한 정보가 있으면 임의의 확정 공수나 일정 대신 미산정 사유/질문을 남긴다. 공수 추정치는 PM이 수정·확인할 대상이다.

단가표·금액·ID·승인 정보는 모델이 발명하지 않는다. 제안 스키마와 코드의 대상/공수 검증을 통과한 뒤 계산한다.
PM이 승인한 정확한 결과/버전·단가표 fingerprint를 기준으로 내부 협의안 템플릿을 조립한다.
협의안 문구를 자연어 LLM으로 다듬는 기능은 현재 필수 경로가 아니다. 추가한다면 승인된 수치·조건·출처를 변경하지 못하게 별도 검증해야 한다.
이번 MVP는 내부 초안까지이며 고객 발송·고객 합의·청구는 하지 않는다.

### 현재 프롬프트의 입출력 예

다음은 형식 확인용 합성 예시이며 실제 공수·견적의 정답이 아니다. 실행 프롬프트 원본은
`app/agents/agent3/prompts.py`, 출력 스키마 원본은 `app/agents/agent3/draft.py`다.

입력에는 원문 전체의 내부 식별자·단가·정답 데이터 대신 필요한 대상과 제약만 보낸다.

```json
{
  "targets": [{"number": 1, "text": "구매자 별점·후기 등록, 가능하면 사진 최대 3장", "intent": "change_request"}],
  "constraints": [{"type": "constraint", "text": "오픈일 유지 필요, 가용 인력과 선행 작업은 미확인"}],
  "answers": []
}
```

정상 제안은 다음처럼 작업·직군·MD·가정을 가진다. `item_numbers`는 PM이 선택한 대상 번호다.

```json
{
  "estimates": [{
    "item_numbers": [1],
    "status": "draft",
    "lines": [
      {"task": "리뷰 등록 API", "item_numbers": [1], "role": "BE", "md": 2.5, "assumption": "기존 저장 구조 재사용"},
      {"task": "리뷰 입력 화면", "item_numbers": [1], "role": "FE", "md": 2.0, "assumption": "선택적 사진 최대 3장 포함"},
      {"task": "등록·첨부 검증", "item_numbers": [1], "role": "QA", "md": 0.5, "assumption": "기존 테스트 환경 재사용"}
    ],
    "reason": ""
  }],
  "questions": []
}
```

요구가 부족하면 `status=not_estimable`, `lines=[]`, 이유와 필요한 질문을 반환한다.
이 출력에 `totals`, 가격, 단가, 승인 상태, 협의안 필드를 추가하면 검증 실패로 처리한다.
프롬프트가 맞는 JSON을 요구하는 것과 실제 공수 추정의 품질이 확보되는 것은 별도다.

승인 후 초안은 `summary / scope / impact / cost / open_questions`로 구성한다.
PM이 확인한 항목·근거, 승인된 공수·가정·실제 제출 제약, 서버 계산 금액·단가표 ID, 미산정 사유와 남은 질문을 담는다.
새 LLM 호출이 문구나 수치를 임의 변경하는 경로는 현재 흐름에 없다.

### 단가 authority 검증

`app/kb/rates.py`는 파일을 매번 읽어 모든 표를 검증한다. 금액은 유한한 비음수 JSON 숫자로 제한하고
문자열·boolean·음수·NaN/무한대는 거부한다. 표의 단위는 KRW/MD, VAT는 false이며 명시 currency는 KRW다.
누락/null 단가는 그대로 보존하고 양수 공수가 있을 때 기존 `NEEDS_INPUT`/전체 합계 없음 흐름을 유지한다.
공수 입력도 유한한 비음수 숫자·0.5 단위를 검사한다. 단가 파일 교체 후 기존 fingerprint 승인 차단은 유지한다.
중복 JSON 필드·NaN/Infinity 상수도 거부하며, 역할 행에 단위/통화/VAT가 명시되면 표 기준과 일치해야 한다.
`synthetic`가 있으면 JSON boolean이어야 하며 선언값만 표시하므로 실제 회사 단가 승인 여부를 증명하지 않는다.
파일의 모든 표를 검증하므로 사용하지 않는 표의 잘못된 값도 수정해야 실행할 수 있다.

## 화면과 검증의 범위

이번 핵심은 C05 영향 산정과 C06 내부 협의안이다. C02~C04와 A1/A2는 기존 합성 입력을 사용하는 범위로 둔다.
QA도 Agent 3의 다음 흐름에 집중하며 다른 파트의 전체 기능을 다시 검증하지 않는다.

1. 합성 단가표와 전달받은 형식의 외부 표에 따른 계산 결과, 누락·잘못된 단가의 처리.
2. LLM 제안의 대상·직군·0.5 MD 단위·추가 금액 필드 검증과 금액의 코드 계산.
3. 수정 후 재계산·새 결과·재승인, 단가표 교체 후 기존 승인 차단.
4. 승인 전 초안 없음, PM 승인/반려, 승인된 값으로만 작성한 협의안.
5. 화면의 조건은 해당 산정의 실제 제출 snapshot을 표시하고, 미재산정 편집과 구분.
6. 키 없이 fake/HTTP mock으로 실행 가능, 실제 API 모드의 설정 확인, 키의 브라우저/보고서 노출 없음.

## 실제 실행한 검증과 미검증

이번 연결 준비는 Python 3.12.14 임시 환경에서 focused A3/LLM/rates 테스트로 확인한다.
신규 `tests/test_agent3_gemini.py`는 HTTP mock의 endpoint·인증·모델·strict schema·usage와
prepare → edit → PM review → deterministic draft를 검사한다. malformed 2회·timeout·401·400이
502 오류로 표시되고 fake 전환/초안/세션이 생성되지 않는 것도 확인한다.
`tests/test_prototype_runner.py`는 preset/override·키 비노출·`.env` 비접근·네트워크 객체 미생성 check를,
`tests/test_rate_cards.py`는 잘못된 단가/단위/VAT·누락/null·잘못된 공수를 검사한다.
실행 결과 (Python `/private/tmp/weave-ui-venv-20261028/bin/python`, 3.12.14):

```bash
python -m pytest tests/test_prototype_runner.py tests/test_rate_cards.py tests/test_agent3_gemini.py tests/test_agent3_agent.py tests/test_agent3_workflow.py tests/test_llm_factory.py tests/test_llm_contract.py -k 'not optional' -q
# 115 passed, 3 deselected; Starlette/httpx deprecation warning 1
python -m pytest tests/test_tools.py -k calc_cost -q
# 2 passed, 5 deselected
python -m pytest tests/test_agent3_gemini.py -q
# 6 passed, 같은 경고 1; strict-schema 전체 property를 채운 mock payload 최종 확인
```

변경 Python 파일 `py_compile` 및 소유 경로 `git diff --check`는 통과했다.
격리 환경 `env -i`의 fake `--check`는 exit 0, Gemini 키 없는 `--check`는 exit 1로 확인했다.
전체 `git diff --check`는 기존 사용자 수정 SQL의 EOF 빈 줄을 보고했으며 해당 파일은 수정하지 않았다.
실행기 `--check`는 네트워크 객체 생성과 `.env` 접근을 막은 테스트에서도 통과했다.
이 기록은 개발자 자체 확인이며 독립 UI QA 결과와는 별개다.

실제 원격 호출·키 인증·Gemini 스키마 수락·추론 공수의 적정성·Python 3.11.9·운영 인증/DB는 미검증이다.
CSV/Excel/PDF 업로드·추출은 후속 검토→버전 확정 계획이며 이번 구현에 포함하지 않는다.
