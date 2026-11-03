# Agent 1 현재 구현 현황

- 문서 기준일: 2026-11-03
- 관련 Issue: `#5`
- 충돌 확인 당시 브랜치: `fix/#3-agent1`
- 범위: 요청 원문 구조화, 프롬프트 관리, 결정적 Assembly, 프로토타입 실행 UI

## 요약

Agent 1은 고객 요청 원문을 공통 스키마의 항목과 확인 질문으로 구조화하는 에이전트이다. LLM은 내부 초안 모델인 `Agent1Draft`를 생성하고, 애플리케이션 코드가 ID, 원문 span, 의도 정규화, 월말 마감 추론, 견적 요청 정규화를 수행한다.

현재 구현의 핵심은 다음과 같다.

- 실제 LLM 구조화 실행 지원
- `v0`/`v1` 프롬프트 버전 관리
- 원문 span과 항목의 근거 연결
- 확인 요청을 기능 변경 요청과 분리
- 월말·선택 요청·견적 요청을 결정적으로 정규화
- 프로토타입에서 공급자와 모델을 선택해 독립 실행
- 실행 결과를 브라우저 세션 기준으로 요청별 보관

## 실행 흐름

```text
Agent1Input
  -> LlmAgent1.run()
  -> structured completion (Agent1Draft)
  -> Agent 1 Assembly
  -> deterministic item/question normalization
  -> Agent1Output
```

실제 구현 경로는 다음과 같다.

1. [agent.py](../../app/agents/agent1/agent.py)의 `LlmAgent1.run()`이 구조화 호출을 실행한다.
2. [draft. py](../../app/agents/agent1/draft.py)의 `Agent1Draft`를 통해 LLM 초안을 검증한다.
3. [prompts.py](../../app/agents/agent1/prompts.py)가 버전별 system prompt와 사용자 프롬프트를 구성한다.
4. [assemble.py](../../app/agents/agent1/assemble.py)가 최종 `Agent1Output`을 조립한다.
5. 최종 결과에는 모델, 지연 시간, 토큰 사용량, 프롬프트 버전, Assembly 보고 정보가 메타데이터로 포함된다.

## 구현 완료 항목

### 1. 요청 항목 구조화

Agent 1은 다음 항목 유형을 처리한다.

- `function`: 기능 또는 변경 요청
- `question`: 확인 질문 또는 견적 요청
- `deadline`: 기한·마감
- `constraint`: 제약
- `dependency`: 외부 의존성

내부 의도는 `change_request`, `confirmation`, `estimate_request`, `defect`, `issue_report`, `status_check`, `constraint` 등을 사용한다.

공통 스키마는 [schemas.py](../../schema/schemas.py)에 정의되어 있다.

### 2. LLM과 Assembly 분리

LLM은 최종 ID나 원문 위치를 직접 생성하지 않는다.

- LLM: 항목 초안, 의도 초안, 질문 초안 생성
- Assembly: 항목 ID 및 표시 번호 생성
- Assembly: 원문 span 위치 계산
- Assembly: 원문에 없는 항목에 `inferred=true` 표시
- Assembly: 최종 Pydantic 스키마 검증

이를 통해 모델이 생성한 출력과 애플리케이션이 관리하는 식별자를 분리한다.

### 3. 프롬프트 버전 관리

Agent 1 프롬프트는 파일로 별도 관리된다.

- [a1-draft-v0.txt](../../app/agents/agent1/prompts/a1-draft-v0.txt): 기존 기본 프롬프트 보존용
- [a1-draft-v1.txt](../../app/agents/agent1/prompts/a1-draft-v1.txt): 현재 기능 강화 프롬프트

버전 선택 우선순위는 다음과 같다.

1. 환경변수 `AGENT1_PROMPT_VERSION`
2. `.env`의 `AGENT1_PROMPT_VERSION`
3. 기본값 `a1-draft-v1`

기본값과 파일 로딩은 [prompts.py](../../app/agents/agent1/prompts.py)에 구현되어 있다.

### 4. 확인 요청 분류

다음과 같은 표현은 기능 변경 요청이 아니라 확인 항목으로 정규화한다.

- `되는 거죠?`
- `맞나요?`
- `확인 부탁드립니다`
- `확인해주세요`
- `가능한가요?`
- `확인하려고요`

예시 출력:

```text
item_type: question
intent: confirmation
text: 검색 시 가격 범위(1만~3만원)·브랜드 필터 동시 적용 가능 여부 확인
```

확인 요청은 Assembly 단계에서 `function` 또는 일반 `question` 초안을 `question + confirmation`으로 정규화한다. 결함이나 오류 신고 의도는 이 정규화 대상에서 제외한다.

### 5. 월말 마감 추론

`월말`, `말일`, `당월`, `월말 정산` 표현이 있는 경우 `received_at` 또는 실행 기준 날짜를 기준으로 당월 말일과 남은 일수를 계산한다.

예시:

```text
월말 정산 일정상 2026-11-30까지 약 28일 이내 수정이 필요할 것으로 추정됩니다.
```

특정 날짜를 직접 말하지 않은 경우 확정 표현 대신 추정 표현을 사용한다. 원문 근거가 없는 단순 `급합니다` 또는 `바쁩니다`만으로 날짜를 만들지 않는다.

### 6. 선택 요청과 항목 문맥 보존

`가능하면`, `여유가 되면`, `선택적` 요청은 필수 기능과 분리해 `(선택 사항)`으로 표현한다.

function 항목은 원문 문장을 그대로 복제하기보다 기능명과 핵심 세부 항목을 짧게 재작성한다.

예시:

```text
상품 리뷰 기능 추가 — 별점, 후기 텍스트, 사진 최대 3장(선택 사항)
```

후속 확인 항목은 필요한 경우 원문상 대상 항목을 명시한다.

### 7. 견적 요청 분류

원문에 명시적인 견적 요청이 있으면 확인 질문으로 만들지 않고 `question + estimate_request` 항목으로 만든다.

예시:

```text
item_type: question
intent: estimate_request
text: 견적 요청
```

견적 항목은 Agent 3 산정 요청으로 전달되며, 단순 세부 항목 확인 때문에 blocking 질문을 만들지 않는다.

### 8. 원문 span 및 근거 연결

Assembly는 draft의 `quotes`를 원문에서 정확히 찾는다.

- 정확 일치 검색
- 공백 무시 검색
- 중복 span 방지
- span 위치를 시작 순서로 정렬
- 원문에서 찾지 못한 항목을 inferred 또는 미연결 항목으로 보고

`Meta.debug`의 Assembly 보고에는 미연결 quote, 근거 없는 항목, 삭제된 질문 등의 정보가 포함된다.

### 9. 확인 질문 Assembly

Agent 1 내부 초안은 최대한 구조화된 질문을 생성할 수 있지만, 다음 항목은 Assembly에서 필터링하거나 정규화한다.

- 명시적 견적 요청을 confirmation 질문으로 변환하지 않음
- QA 목적만으로 추가 확인 질문을 생성하지 않음
- 확인형 원문을 `change_request` 질문으로 남기지 않음
- blocking 여부와 item number 유효성을 검증

### 10. 프로토타입 실행

기존 Agent 1 Lab API와 연결되는 독립 실행 UI가 구현되어 있다.

- 요청 원문 등록 화면
- 기존 합성 요청 목록
- Agent 1 공급자 선택
- 모델 선택
- Agent 1 실행 버튼
- 실행 결과 항목 표시
- 실행 메타데이터 표시
- 요청별 결과 브라우저 세션 보관

프로토타입 Lab API는 [lab.py](../../prototype/lab.py)에 있으며 주요 엔드포인트는 다음과 같다.

```text
POST /prototype-lab/agent1
POST /prototype-lab/agent2
```

API 모드 실행 예시:

```bash
python prototype/run_demo.py --mode api --lab
```

`--lab` 모드에서는 프로토타입 서버가 코드 변경을 감지하도록 reload가 활성화된다.

## 단독 REST API

Agent 1은 공통 오케스트레이션 API와 별도로 단독 HTTP 엔드포인트를 제공한다.

```text
POST /agent1/items
```

- 입력: `Agent1Input`
- 출력: `Agent1Output`
- 기본 fixture 모드: 데이터셋의 Agent 1 gold 결과 반환
- LLM 모드: 설정된 공급자와 모델로 `LlmAgent1.run()` 실행
- 결과 ID: API 계층에서 `A1-{case_id}-{uuid}` 형식으로 발급
- 생성 시각: API 계층에서 한국시간 ISO 8601 형식으로 발급
- 실행 결과 저장: 별도 저장소가 아니라 응답으로만 반환

실행 모드는 다음 우선순위로 결정한다.

1. `agent_mode=llm` 또는 `agent1_mode=llm`이면 실제 LLM Agent 1 실행
2. 그 외에는 fixture Agent 1 실행

`/prototype-lab/agent1`은 로컬 프로토타입 전용 공급자 선택 API이며, `/agent1/items`는 일반 백엔드 연동용 공개 라우터이다.

## 기존 Lab API와 이번 UI 연동의 차이

기존 코드에는 이미 `/prototype-lab/agent1` 엔드포인트와 `LlmAgent1` 실행 경로가 일부 존재했다.

이번 브랜치 작업으로 추가된 부분은 주로 다음과 같다.

- 변경 요청 등록 화면
- 등록 요청을 Agent 1 입력으로 변환하는 UI 흐름
- Agent 1 실행 버튼과 결과 카드
- 공급자·모델 선택 UI
- 탭별 Agent 1 안내 문구
- 요청별 Agent 1 세션 메모리
- 실행 결과가 상위 요청 화면으로 유지되는 표시 흐름

따라서 "Agent 1 독립 실행 Lab 자체 신규 구현"이라고 표현하기보다는 **기존 Lab API 위에 프로토타입 화면과 등록·실행 UX를 연결한 구현**으로 보는 것이 정확하다.

## 저장소 위치

| 영역 | 주요 파일 |
|---|---|
| Agent 실행 | `app/agents/agent1/agent.py` |
| 프롬프트 로더 | `app/agents/agent1/prompts.py` |
| v0 프롬프트 | `app/agents/agent1/prompts/a1-draft-v0.txt` |
| v1 프롬프트 | `app/agents/agent1/prompts/a1-draft-v1.txt` |
| 내부 초안 스키마 | `app/agents/agent1/draft.py` |
| 최종 Assembly | `app/agents/agent1/assemble.py` |
| 공통 스키마 | `schema/schemas.py` |
| 프로토타입 Lab API | `prototype/lab.py` |
| 프로토타입 UI | `prototype/app.js` |
| 실행 서버 | `prototype/run_demo.py` |

## 검증 현황

현재 다음 수준의 검증이 구현되어 있다.

- 프롬프트 버전 로딩 및 환경변수 선택 테스트
- 월말 마감 reference date 계산 테스트
- 선택 요청 문구 정규화 테스트
- 견적 요청을 question 항목으로 정규화하는 테스트
- 확인 요청을 `question + confirmation`으로 정규화하는 테스트
- 원문 quote와 span 위치 검증
- gold dataset round-trip 검증
- Agent 1 structured output 호출 검증
- 모델 실패와 invalid draft 처리 검증
- 프로토타입 Lab API 테스트
- 프로토타입 JavaScript 문법 검사

현재 작업 기준으로 확인된 주요 검증 명령은 다음과 같다.

```bash
pytest -q tests/test_agent1_agent.py tests/test_agent1_assemble.py tests/test_prototype_runner.py
node --check prototype/app.js
python -m compileall -q app/agents/agent1 prototype/run_demo.py
```

마지막 확인 시 Agent 1 및 프로토타입 관련 테스트 37개가 통과했다.

## 현재 미완료 또는 제한 사항

### 답변 분석 미구현

`LlmAgent1.analyze_answer()`는 현재 `NotImplementedError`로 구현되어 있다. 확인 질문에 대한 PM·고객 답변을 Agent 1이 해석해 항목 구성과 재실행 범위를 결정하는 기능은 별도 구현이 필요하다.

### 공급자별 프롬프트 계약 차이

LLM 공급자별 구조화 호출 방식과 응답 형식을 공통화하고 있지만, API 공급자별 실제 운영 품질은 공급자·모델별로 별도 검증이 필요하다.

### 서버 메모리와 브라우저 메모리

프로토타입 Agent 1 결과는 브라우저 `sessionStorage`에 요청별로 보관된다.

- 탭 이동 및 요청 목록 이동 시 유지
- 브라우저 세션 종료 시 휘발
- Agent 1 서버 재시작 시 서버 실행 상태와 독립적으로 초기화

프로토타입 UI의 결과 저장은 영구 데이터 저장소가 아니다.

### 확인 분류의 범위

현재 확인 의도 정규화는 명시적인 표현 목록을 기준으로 한다. 자연어 표현이 매우 다양한 경우 프롬프트 분류와 Assembly 정규화가 모든 표현을 완전히 보장하지는 않는다.

### 항목 후보 검색

function 항목은 기준선 문서 검색 기능 후보를 조회할 수 있다. confirmation 항목은 기능 후보 검색 대상에서 제외된다.

### PM 항목 승인 흐름

공유 오케스트레이션 엔진에서는 Agent 1 실행 후 `WAITING_PM_REVIEW`까지 상태를 전환할 수 있다. 다만 PM 답변을 Agent 1이 재분석해 rerun_from을 결정하는 답변 분석 기능은 완성되지 않았다.

## 권장 다음 단계

1. `AnswerAnalysisInput/Output` 기반 Agent 1 답변 분석 구현
2. 확인 질문의 자연어 변형을 gold dataset에 추가 확대
3. 실제 API 모델별 원문 span 안정성 평가
4. Agent 1 결과와 downstream Agent 2/3 요청의 영속 저장소 연동
5. 프로토타입 실행 결과와 공유 오케스트레이션 상태 동기화
6. 프롬프트 버전별 회귀 테스트를 별도 CI 검사로 유지
