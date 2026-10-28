# 업무 범위 판단 조정 Agent

> 고객의 변경 요청이 계약 범위에 포함되는지 판단하고, 범위 밖인 경우 영향 산정과 협의용 초안을 준비하는 AI 에이전트 서버입니다.

현재 프로젝트는 **REST API와 결정적 오케스트레이션 엔진을 검증하는 MVP**입니다. 실제 LLM 에이전트와 영속 저장소는 아직 연결되지 않았으며, 기본 실행 모드인 `fixture`는 평가 데이터셋의 예시 출력을 사용합니다.

## 목차

- [주요 기능](#주요-기능)
- [기술 처리 흐름](#기술-처리-흐름)
- [기술 스택](#기술-스택)
- [설치 및 실행](#설치-및-실행)
- [환경 변수](#환경-변수)
- [API](#api)
- [프로젝트 구조](#프로젝트-구조)
- [테스트 및 평가](#테스트-및-평가)
- [현재 범위와 참고 사항](#현재-범위와-참고-사항)

## 주요 기능

| 기능 | 설명 | 상태 |
|---|---|---|
| 요청 구조화 (①) | 고객 요청을 작업 항목과 확인 질문으로 정리합니다. | Fixture 출력으로 흐름 검증 |
| 계약 범위 판단 (②) | 기준선 문서에서 근거를 찾고 변경 요청의 범위를 판단합니다. | 검색 도구·인용 검증 가드 구현 |
| 영향 산정 및 초안 (③) | 공수·금액·일정 영향과 협의용 초안을 준비합니다. | 계산 도구·호출 전 검사 구현 |
| 사람 검토 단계 | PM 항목 확인, PM 검토, 리드 승인을 단계 사이에 둡니다. | API 및 상태 전이 구현 |
| 안전한 재실행 | 변경 종류에 따라 후속 결과를 무효화하고 이전 결과를 보존합니다. | 엔진 구현 |
| REST API | 케이스 실행·상태 조회·재개·질문 답변·실패 재시도를 제공합니다. | 구현 |

## 기술 처리 흐름 (MVP)

```text
요청 입력
   │
   ▼
① 요청 구조화 ── 확인 질문이 있으면 입력 대기
   │
   ▼
[PM 항목 확인] (기능 확장 시 자동화 처리)
   │
   ▼
② 문서 검색 · 계약 범위 판단
   │
   ▼
[PM 검토]
   │
   ▼
③ 영향 산정 · 협의용 초안
   │
   ▼
[리드 검토] ── 승인 ──▶ 완료
```

### 처리 원칙

- 엔진은 단계의 순서, 대기, 재개, 재실행과 상태 저장을 담당합니다. 계약 판단이나 공수 산정 자체를 대신하지 않습니다.
- ②의 인용은 `verify_quote`를 통과해야 합니다. 유효한 근거가 없으면 판단을 `unclear`로 낮춥니다.
- 금액은 `calc_cost` 도구에서 계산합니다. LLM이 직접 작성한 금액은 허용하지 않는 구조입니다.
- PM 검토가 끝나지 않았거나 입력·버전 검사가 실패하면 ③을 호출하지 않습니다.
- `state_revision`으로 오래된 화면의 덮어쓰기를 막습니다. 충돌 시 HTTP `409`를 반환합니다.
- 재실행으로 무효화된 결과도 삭제하지 않고 보관합니다.

## 기술 스택

- **Python** · **FastAPI** · **Pydantic v2**
- **Uvicorn** — ASGI 개발 서버
- **pytest** · **HTTPX** — 테스트
- 현재 상태 저장소: 메모리 기반
- 현재 문서 검색: 문자 bigram 어휘 검색 기준선

## 설치 및 실행

프로젝트 기준 Python 버전은 **3.11.9**입니다. `.python-version`을 지원하는 버전 관리 도구(예: pyenv)는 이 파일을 기준으로 인터프리터를 선택할 수 있습니다.

Python 환경을 준비한 다음 저장소 루트에서 실행합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

환경 변수는 기본값으로도 실행할 수 있습니다. 필요하다면 예시 파일을 복사해 `.env`를 만들고 값을 설정합니다.

```bash
cp .env.example .env
```

개발 서버를 실행합니다.

```bash
uvicorn app.main:app --reload
```

- 헬스 체크: <http://127.0.0.1:8000/health>
- 대화형 API 문서: <http://127.0.0.1:8000/docs>
- OpenAPI 스키마: <http://127.0.0.1:8000/openapi.json>

기본 `AGENT_MODE=fixture`에서는 평가 데이터셋에 정의된 케이스를 사용해 API와 오케스트레이션 흐름을 확인합니다. 임의의 케이스 ID는 Fixture에 없으면 처리할 수 없습니다.

Agent1 입력과 구조화 출력을 확인하는 Streamlit 데모는 별도 터미널에서 실행합니다.

```bash
python -m streamlit run app/demo_streamlit.py
```

데모에서는 Fake, Local LLM, Bedrock 중 provider를 선택할 수 있습니다. Fake는 입력 원문을 그대로 활용하는 예시 초안을 사용하며 실제 추론은 하지 않습니다. Local LLM은 OpenAI 호환 서버 설정이, Bedrock은 모델 ID·리전과 로컬 AWS 자격 증명이 필요합니다. Agent2·Agent3 탭은 아직 구현되지 않았습니다.

Google AI Studio의 OpenAI 호환 API를 Local provider로 사용할 때는 `.env`에 `LLM_MODEL`과 `LLM_API_KEY`를 설정합니다. `LLM_BASE_URL`을 생략하면 `https://generativelanguage.googleapis.com/v1beta/openai`를 사용합니다. Streamlit은 모델과 키를 서버 측 `.env` 설정에서 읽으며, 키 값은 화면에 표시하지 않습니다.

## 환경 변수

설정은 프로젝트 루트의 `.env`와 프로세스 환경 변수에서 읽습니다. 환경 변수가 `.env` 값보다 우선합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENT_MODE` | `fixture` | 에이전트 실행 모드. 현재 정상적으로 사용할 수 있는 모드는 `fixture`입니다. |
| `DATA_DIR` | `data/` | 기준선, 요율표, 일정 및 문서 데이터 경로 |
| `EVAL_DIR` | `eval/dataset/` | Fixture가 읽는 평가 데이터셋 경로 |
| `STAGE_TIMEOUT_SEC` | `60` | 오케스트레이션 단계별 제한 시간(초) |
| `TOOL_MAX_RETRIES` | `2` | 재시도 가능한 도구 실패의 최대 자동 재시도 횟수 |
| `LLM_MODEL` | 빈 문자열 | 향후 실제 LLM 연결을 위한 설정값. |
| `LLM_API_KEY` | 빈 문자열 | 향후 실제 LLM 연결을 위한 설정값. |
| `DATABASE_URL` | 빈 문자열 | 향후 DB 체크포인터·영속 저장소 연결을 위한 설정값. 현재 미사용 |

`AGENT_MODE`를 `llm`로 설정해도 실제 LLM 연동이 완료된 것은 아닙니다. 실제 에이전트 구현 및 LLM 클라이언트 연결 전에는 기본값인 `fixture`를 사용하세요.

`AGENT_MODE`는 에이전트를 fixture로 쓸지(`fixture`) 실제 LLM 에이전트로 쓸지(`llm`)를 고르고, `LLM_PROVIDER`는 실제 에이전트가 쓸 LLM(`fake`·`local`·`bedrock`)을 고릅니다. 서로 다른 축입니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | 서버 상태 확인 |
| `POST` | `/cases/{case_id}/run` | 요청으로 케이스 실행 시작 |
| `GET` | `/cases/{case_id}/state` | 케이스의 현재 상태와 단계별 결과 조회 |
| `POST` | `/cases/{case_id}/resume` | PM 항목 확인, PM 검토 또는 리드 결정으로 흐름 재개 |
| `POST` | `/cases/{case_id}/questions/{question_id}/answer` | 확인 질문에 답변 |
| `POST` | `/cases/{case_id}/retry` | 기술 오류로 실패한 단계 재시도 |

요청·응답 스키마와 단계별 사용 예시는 서버 실행 후 [`/docs`](http://127.0.0.1:8000/docs)에서 확인할 수 있습니다. 잘못된 입력은 `422`, 잘못된 단계 전이 또는 상태 버전 충돌은 `409`, 존재하지 않는 케이스 조회는 `404`를 반환합니다.

## 프로젝트 구조

```text
.
├── app/
│   ├── main.py                  # FastAPI 앱
│   ├── api/
│   │   ├── routes.py            # REST 엔드포인트
│   │   └── models.py            # API 요청 모델
│   ├── agents/
│   │   ├── base.py              # 에이전트 인터페이스
│   │   ├── registry.py          # 에이전트 구성
│   │   ├── fixture.py           # 평가 데이터 기반 Fixture
│   │   ├── agent1/              # 요청 구조화
│   │   ├── agent2/              # 범위 판단 및 가드
│   │   └── agent3/              # 영향 산정 및 가드
│   ├── core/config.py           # 환경 설정
│   ├── kb/                      # 지식베이스 로더
│   ├── llm/                     # LLM 클라이언트 인터페이스 (연동 예정)
│   ├── orchestration/           # 상태 전이, 사전 검사, 저장소
│   └── tools/                   # 비용·일정·문서 검색 및 검증 도구
├── data/
│   ├── kb/                      # 문서 인덱스, 기준선, 요율표, 일정
│   └── documents/               # 기준 문서 PDF
├── eval/
│   ├── dataset/                 # 평가 입력·기대 출력·Fixture 데이터
│   └── run_eval.py              # 검색 및 사전 검사 평가
├── schema/
│   └── schemas.py               # 공통 입출력 스키마 원본
├── tests/                       # API·엔진·도구·데이터 무결성 테스트
├── .env.example                 # 환경 변수 예시
└── requirements.txt             # Python 의존성
```

## 테스트 및 평가

```bash
python -m pytest -q
python -m eval.run_eval
```

테스트는 API, 오케스트레이션 전이, 가드, 사전 검사, 도구 및 데이터셋 무결성을 확인합니다. 평가 스크립트는 문서 검색 지표와 ③ 호출 전 검사를 점검합니다.

평가 데이터의 정답은 개발 중 Fixture 및 회귀 확인에 사용합니다. 데이터셋과 가상 문서가 팀 검수 전 초안이므로, 평가 결과를 실제 모델의 독립 성능이나 운영 품질의 근거로 해석하지 마세요. 자세한 기준은 [`eval/dataset/EVALUATION.md`](eval/dataset/EVALUATION.md)를 참고하세요.

## 현재 범위와 참고 사항

- **실제 LLM 에이전트는 미구현**입니다. 인터페이스·프롬프트·가드만 준비되어 있으며 기본 실행은 Fixture입니다.
- **영속 저장소는 미구현**입니다. API 프로세스를 재시작하면 메모리에 저장된 상태가 사라집니다.
- **LangGraph, Postgres 체크포인터, 벡터 검색 및 재랭킹은 향후 작업**입니다.
- 평가 데이터의 공수·요율·일정은 검수 전의 참조값입니다. 실제 계약 판단이나 견적의 근거로 사용하지 마세요.
- 공통 계약 스키마는 `schema/schemas.py`에 있습니다. 변경은 해당 공통 기준의 절차를 따르며 임의로 수정하지 않습니다.
