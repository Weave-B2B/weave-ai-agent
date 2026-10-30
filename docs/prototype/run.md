# 변경요청 확인 화면 실행

현재 범위는 [구현 계획](implementation-plan.md)의 혼합안(B)이다.
Figma C02~C06의 기본 구조를 기존 FastAPI 서버의 `/prototype/`에서 확인한다.

## 화면에서 직접 모델 테스트하기

1. 저장소 루트에서 `python prototype/run_demo.py --lab --port 8770`을 실행한다.
   `--lab`은 로컬 주소에서만 사용할 수 있는 개발자 테스트 기능을 켠다.
2. `http://127.0.0.1:8770/prototype/`을 열고 `CR-A-001` → **영향 산정**을 선택한다.
3. **테스트 실행 설정**에서 `가상 응답`, `Gemini API`, `AWS Bedrock`을 선택한다.
   가상 응답은 JSON을 직접 편집할 수 있다. Bedrock은 서버의 AWS 자격 증명과 모델 ID·리전이 필요하다.
4. Gemini를 선택하면 서버가 `.env`의 `GEMINI_API_KEY`를 읽는다. 키는 브라우저에 입력하지 않는다.
   기본 모델은 `gemini-2.5-flash-lite`이며 화면에서 모델 ID를 바꿀 수 있다.
5. 아래 **테스트 입력**에서 샘플 입력을 확인하고 위쪽 **영향 산정 실행**을 누른다.
   공수·가정을 확인하고 필요하면 수정한 뒤 일정 미산정을 체크하여 승인한다.
   **승인·협의**에서 내부 초안을 볼 수 있으며 **결과 JSON · 버전**을 펼쳐 응답을 확인한다.

Gemini·Bedrock 실행은 외부 API 호출이며 비용이 발생할 수 있다. 입력은 실제 모델로 전달되므로
테스트에 사용할 데이터인지 확인한다. 기본 입력·단가는 합성 예시다.
서버를 다시 시작하면 메모리의 테스트 세션이 초기화된다. 기본 실행 명령과 --lab은 별개의 모드다.

## 실행

저장소 루트에서 기존 `requirements.txt`가 설치된 Python을 사용한다.
저장소 기준은 Python 3.11.9다. 이번 구현 중 로컬 실행 확인에 사용한 임시 환경은
Python 3.12.14이며, 3.11.9에서 검증했다는 뜻은 아니다.

```bash
python prototype/run_demo.py
```

브라우저에서 [http://127.0.0.1:8000/prototype/](http://127.0.0.1:8000/prototype/)를 연다.
포트가 사용 중이면 `--port 8765` 등으로 지정한다.

실행기는 기존 `app.main:app`을 단일 프로세스로 실행한다. 기본값은 `--mode fake`다.

- `AGENT_MODE=fixture`: 공유 A1/A2는 합성 입력을 유지한다. `/agent3`는 별도 공급자를 사용한다.
- fake: `LLM_PROVIDER=fake`, `LLM_MODEL=fake-ui-demo`, 기존 합성 응답을 임시 파일에 200회 반복한다.
- gemini (`api` 별칭): 기존 `local` OpenAI 호환 클라이언트, 고정 Google endpoint,
  `gemini-2.5-flash-lite`, JSON schema 모드를 명시적으로 선택한다.
- 수정·승인·협의안 템플릿은 추가 LLM 호출 없이 기존 코드로 처리한다.

### 무호출 사전 확인

```bash
python prototype/run_demo.py --check
python prototype/run_demo.py --mode gemini --check
```

`--check`는 서버·HTTP client를 만들지 않고 환경 설정과 단가 JSON만 확인한다.
키는 존재 여부만 표시하며 인증 유효성, 실제 API 접근 권한·모델 품질은 확인하지 않는다.
키 누락/잘못된 숫자 설정/잘못된 단가표는 종료 코드 1이다. 원본 `.env`를 읽거나 바꾸지 않는다.

### Gemini 실행 (실제 호출은 별도 실행 시에만)

서버 셸에서 `GEMINI_API_KEY` 또는 `LLM_API_KEY`를 설정한 후 실행한다.
실제 키를 명령 기록, 문서, 화면 코드에 붙여 넣지 않는다. 비어 있는 설정 예제는
[.env.gemini.example](../../.env.gemini.example)에 있다. 실행기는 이 예제를 자동 로드하지 않는다.

```bash
python prototype/run_demo.py --mode gemini --check
python prototype/run_demo.py --mode gemini --port 8000
# 다른 모델은 사용자가 명시적으로 선택한다.
python prototype/run_demo.py --mode gemini --model gemini-2.5-flash-lite --check
```

`GEMINI_API_KEY`가 우선하고, 비어 있으면 `LLM_API_KEY`를 사용한다.
모델은 `--model` > 고정 preset 순서다. 이전 제공업체의 `LLM_MODEL`·`LLM_BASE_URL`·
`LLM_PROVIDER`·`LLM_LOCAL_JSON_MODE`는 실행기가 덮어쓰므로 Gemini 선택을 바꾸지 않는다.
endpoint는 `https://generativelanguage.googleapis.com/v1beta/openai/`로 고정한다.
실제 모델 호출은 **영향 산정 실행** 시 발생하며, 오류가 나도 fake로 전환하지 않는다.
max tokens·temperature·read/connect timeout은 셸 값 > 문서화된 기본값을 사용하고 검증한 값을 서버 환경에 고정한다.
실행기의 무호출 확인과 달리 실제 서버는 나머지 Settings의 기존 `.env` 로딩 동작을 유지한다.
키는 서버의 Bearer 인증에만 사용하며 브라우저에 내려주지 않는다.

### 외부 단가표

파일 선택 우선순위는 `--rate-card-file` > 서버 환경 `RATE_CARD_FILE` > 저장소 합성 표다.
fake/Gemini 모두 동일하며 외부 파일을 조용히 합성 표로 대체하지 않는다.

```bash
python prototype/run_demo.py --check --rate-card-file /absolute/path/to/company-rates.json
python prototype/run_demo.py --mode gemini --rate-card-file /absolute/path/to/company-rates.json
```

`rate_cards` 아래 표 ID·`unit=KRW/MD`·`vat_included=false`·직군별 `rates.cost/price`를 둔다.
금액은 유한한 0 이상의 JSON 숫자이며 문자열·boolean·음수·NaN·무한대와 단위/VAT 불일치는 거부한다.
누락/null 단가는 양수 MD가 있을 때 `NEEDS_INPUT`이 된다. 0 MD에는 단가가 필요 없다.
외부 표 ID를 사용할 때 **영향 산정 요청 JSON의 `rate_card_id`도 일치시켜야 한다**.
화면의 기존 CR-A-001 입력은 합성 ID를 사용하므로 외부 표를 자동 선택하지 않는다.
파일 교체 후 fingerprint가 달라지면 이전 결과 승인이 차단되고 재계산·재승인이 필요하다.
회사 단가표가 아직 없으면 Gemini 모드도 기본 합성 표를 사용한다. 이는 실제 견적 검증이 아니다.

공식 모델·스키마 근거와 검증 한계는 [Agent 3 API 준비](../agent3/agent3-api-plan.md)를 참고한다.
초기 준비에서는 HTTP mock으로 연결 계약을 검증했다. 이후 --lab에서 합성 입력으로 Gemini 2.5 Flash-Lite 실제 산정·승인·초안을 확인했다. Google은 2.5 모델 접근을 과거 사용 이력이 있는 사용자로 제한한다고 안내하므로 실제 키의 접근 가능 여부는 별도 확인해야 한다.

## 확인할 흐름

1. 목록에서 **CR-A-001 / API 연결 샘플**을 연다. CR-001은 Figma 상세 흐름을 위한 조회용 예시이며, 나머지는 목록·필터 표시용으로 상세 내용은 준비하지 않았다.
2. **요청 원문 → 범위 검토**에서 합성 상위 입력과 근거를 확인한다. 실제 A1/A2 재분석·PM 범위 확정은 연결하지 않았다.
3. **영향 산정 → 영향 산정 실행**으로 기존 `/agent3/impacts`를 호출한다.
4. 작업·직군·MD·서버 계산 합계, 일정 **미산정** 사유를 확인한다. 승인 전 협의안은 없다.
5. MD/가정을 고치고 사유를 입력해 **수정 반영·재계산**한다. MD는 0 이상·0.5 단위다.
6. 일정 미산정 인지 체크와 검토자 참조를 입력하고 승인한다. **승인·협의** 탭에서 서버가 반환한 내부 초안을 복사하거나 Markdown으로 저장한다.
7. 승인 후 다시 공수를 수정하면 기존 초안은 이전 이력으로 남고 최신 결과는 재승인이 필요하다.

상단의 **연결**에서 읽기 전용 **상태 미리보기**를 선택하면 저장된 합성 응답으로
추가 정보 필요·상위 검토 대기·대상 없음·실패·HTTP 오류 등의 화면을 확인할 수 있다.
이 모드는 실제 API를 호출하거나 수정·승인·금액 계산을 하지 않는다.

## 데이터와 상태 경계

- C02 목록·C03 원문·C04 범위 판단은 명시적인 합성 예시다.
- C05 산정·수정·조회·승인/반려와 C06 초안은 기존 API 응답을 사용한다.
- 산정 전에는 브라우저가 서버 공급자 설정을 조회할 수 없다. 산정 후에는 반환된
  `impact.meta.model`을 그대로 표시한다. 이 값이 실제 인증·접속 성공이나 모델 품질을 보증하지 않는다.
  실행 모드는 서버 실행 로그로 확인한다. 기본 fake와 명시적 Gemini 모드는 실행기가 결정하며,
  별도로 실행한 서버의 모델 설정은 해당 서버가 결정한다.
- `reviewer_ref`는 로컬 테스트 참조이며 실제 PM 인증이 아니다. PM 산정 승인과 상업 승인·고객 합의는 별개다.
- 현재 API가 제공하지 않는 직군 단가·행별 금액은 `API 미제공`으로 표시한다.
- 현재 세션의 메모리 결과와 합성 요청 목록의 상태를 섞어 전체 업무가 완료됐다고 표시하지 않는다.
- 요청 JSON이 바뀌거나 MD 편집 중이면 이전 결과 승인을 막는다. 409 응답 후에는 최신 입력으로 재산정한다.
- 브라우저 새로고침 시 현재 탭의 sessionStorage에 남긴 세션 ID로 기존 API를 조회한다.
  서버 재시작으로 세션이 없어지면 404 안내 후 다시 산정해야 한다. 영속 저장을 구현한 것이 아니다.

등록·첨부·상위 재분석·실제 범위 확정·상업 승인·고객 전송·PDF·청구·운영 DB·인프라는 이번 범위 밖이다.
그 기능의 버튼은 비활성화하거나 안내만 표시한다.

## 구현과 검사

- `prototype/index.html`, `styles.css`, `app.js`: 레이아웃·상호작용
- `prototype/api.js`: 기존 API adapter / 별도 읽기 전용 mock adapter
- `prototype/fixtures.js`, `data/`: 합성 요청·기존 합성 입력·상태 미리보기
- `prototype/assets/`, `figma-assets.json`: Figma 도구가 제공한 원본 SVG와 노드별 매핑
- `app/main.py`: `/prototype` 정적 화면 mount만 추가

화면 담당의 실행 확인과 독립 QA 결과는 구분한다. 이번 실행법 문서 자체가 QA 통과를 의미하지 않는다.


## 화면 담당 자체 확인 기록 (2026-10-28)

독립 QA 착수 전 개발자가 확인한 결과다. Python 3.12.14와 Chrome을 사용했다.

- C02~C06 이동과 회색 와이어프레임 구조, 로컬 SVG 표시를 확인했다.
- 연속 검색 입력의 포커스 유지, 빈 검색 결과, 프로젝트·상태 필터와 2페이지 이동을 확인했다.
- 기존 API + fake 응답으로 10 MD / 판매가 6,425,000원을 받았다.
  FE 3 → 3.5 MD 수정 시 10.5 MD / 6,750,000원과 새 revision을 받았다.
- 일정 미산정 사유와 인지 체크 전 승인 차단, 승인 전 초안 없음,
  승인 후 내부 초안 표시, 승인 뒤 다시 수정할 때 승인·초안 무효화 및 history 분리를 확인했다.
- 반려 사유가 없으면 화면에서 차단했고, 사유를 입력한 반려는 API에 반영됐다.
  입력 JSON이 바뀌면 이전 결과의 승인이 차단됐다.
- 실제 API의 대상 없음·상위 PM 검토 대기·QA 단가 누락 상태를 확인했다.
- 404/409/422/502/503 및 연결 실패 표시는 **읽기 전용 mock**으로 점검했다.
  서버 재시작으로 실제 세션이 소실되는 HTTP 404와 이후 새 산정 복구도 확인했다.
- 새로고침으로 현재 탭의 API 세션이 복구됐다. 작은 화면에서는 메뉴를 축소하고 표 내부 스크롤을 사용하며 전체 페이지 가로 넘침이 없었다.
- `node --check` 3개 JS, Python `py_compile`, 이번 변경 범위의 `git diff --check`가 통과했다.
- 기존 `tests/test_agent3_workflow.py`, `tests/test_api.py`: **15 passed**.
  기존 Starlette/httpx 사용 중단 예정 경고 1건이 있었다.

실제 외부 모델 품질, 운영 PM 인증·업무 DB, 실제 네트워크/모델 장애의 전체 재현,
실제 동시 수정에 의한 409 경쟁, 모든 브라우저·Python 3.11.9 검증은 수행하지 않았다.
한글 IME 조합 중 결과 갱신을 유예하도록 구현했으며, 실제 OS IME 조합 검사는 별도 확인 대상이다.


## 이번 화면 보완 자체 확인 기록 (2026-10-28)

아래는 화면 담당자가 전달한 개발자 자체 검사이며 독립 QA 결과와 구분한다.

- `node --experimental-vm-modules --test prototype/tests/submitted-input.test.mjs`: **5 passed**,
  실험 VM 경고 1건. `node --check`와 해당 수정 범위 `git diff --check` 통과.
- 별도 fake 서버 8767에서 제출 조건 `2026-12-10` → C05 → 새로고침 → PM 승인 → C06까지
  동일한 제출 snapshot 표시를 확인했다.
- 미제출 `2027-01-20` 편집 및 없는 단가표 422 실패 후에도 이전 snapshot을 보존하고,
  새로고침 이후 승인을 차단함을 확인했다. 브라우저 error/warn 0건. 테스트 서버는 종료했다.
- Agent 3 Gemini 계약과 단가표 검증은 HTTP mock 기반 focused Python 검사이며 실제 모델 품질 검증은 아니다.
