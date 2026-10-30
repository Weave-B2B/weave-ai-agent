# 에이전트 3 초기 MVP

Issue: #5 · 작업 기준: dev `ceefd46` · 브랜치: `feat/#5-agent3`

## 이번 구현

에이전트 2 결과와 범위 PM 검토를 입력받아 **영향 산정 → PM 확인·수정·승인 → 내부 협의안 초안**까지 처리한다. 에이전트 1의 구조화 LLM 클라이언트 패턴을 재사용한다. LLM은 작업·직군·0.5 단위 MD·가정·질문만 제안하고, ID·금액·일정 미산정 처리·협의안 조립은 코드가 담당한다.

- 에이전트 1: `LlmAgent1(llm)` → 전용 초안 모델 → 코드 조립·메타데이터.
- 에이전트 3: `LlmAgent3(llm)` → `Agent3ImpactDraft` → 대상/작업 검증·코드 계산.
- 에이전트 3는 `run()`에서 협의안을 만들지 않는다. `Agent3Workflow`에서 PM이 승인한 결과로만 기존 `Draft` 필드를 채운다.
- 범위 PMReview와 영향 산정 승인은 별개다. PM이 산정 결과를 수정하면 재계산·새 결과 ID·재승인을 거친다.
- 초기 구현에서는 일정 정보를 과도하게 추정하지 않는다. 가용 인력·의존성 자료가 없으므로 `business_days=null`과 사유를 보존한다.
- 고객 발송·고객 합의·청구는 하지 않는다. 결과는 내부 검토용이며 `review_required=true`, `billing_authorized=false`를 유지한다.

## 우선 분리한 연결 지점

동료가 개발 중인 A1/A2·공통 오케스트레이션과 충돌을 줄이기 위해 **별도의 `/agent3` API**로 먼저 검증한다. 공유 `schema/schemas.py`와 `/cases/...`의 기존 상태 전이는 변경하지 않았다.

`/cases/...` 전체 LLM 흐름에는 아직 에이전트 2·답변 분석 구현 및 새 영향 산정 승인 게이트 연결이 필요하다. 기존 `lead_review`는 새 산정 승인과 같지 않다. 이 PR이 공유 엔진 전체 연동까지 완료했다는 뜻은 아니다.

| API | 동작 |
|---|---|
| `POST /agent3/impacts` | `Agent3Input` → 산정 결과 저장·PM 검토 대기 |
| `GET /agent3/impacts/{session_id}` | 산정·승인·협의안·이력 조회 |
| `POST /agent3/impacts/{session_id}/edit` | 작업·MD·가정 수정 → 코드 재계산·새 결과 → 재승인 대기 |
| `POST /agent3/impacts/{session_id}/review` | 승인 시 초안 자동 조립, 반려 시 사유 기록 후 대기 |

`phase`는 이 API의 전용 서비스 상태이며 공유 A3Status enum을 개정하지 않는다. `IMPACT_DRAFT_READY`여도 영향 산정만 준비된 단계에서는 `draft=null`이다.

## 로컬 실행

### Streamlit으로 버튼 테스트

```bash
python -m streamlit run app/demo_streamlit.py
# 에이전트 3 화면만 열 때
python -m streamlit run app/demo_agent3.py
```

통합 화면의 **③ Agent3 · 영향 산정** 탭 또는 전용 화면에서 다음 순서로 시험한다. FastAPI 서버를 별도로 실행할 필요는 없으며 API와 같은 `Agent3Workflow`를 직접 사용한다.

1. 정상 산정·QA 단가 누락·범위 PM 검토 전·산정 대상 없음 중 시나리오를 선택한다.
2. **영향 산정 실행**을 누르면 작업·공수·금액·일정 미산정·질문이 표시된다. 아직 협의안은 없다.
3. **공수·가정 수정**에서 MD/가정과 수정 사유를 입력하고 **수정 반영·재계산**한다.
4. 미산정 일정 확인에 체크한 뒤 **승인하고 협의안 작성**을 누른다. **반려**는 사유를 기록하며 협의안을 만들지 않는다.
5. 협의안을 읽고 Markdown으로 내려받는다. 승인 뒤 다시 공수를 수정하면 이전 초안·승인은 이력으로 남고 재승인해야 한다.

입력·모델 설정이 바뀌었는데 다시 산정하지 않은 경우 이전 결과의 승인·수정 버튼을 비활성화한다. 가상 응답은 실행 버튼을 누를 때마다 새 클라이언트를 만들어 반복 실행할 수 있다. 가상 응답 모드는 에이전트 1·2나 실제 모델 추론을 실행하지 않는다. 고급 입력 JSON·가상 응답 편집과 local/bedrock 공급자 선택도 가능하다.

테스트 검토자 문자열은 실제 사용자 인증을 대신하지 않는다. UI의 상태는 브라우저별 테스트 세션의 서버 메모리에만 보관되며 API 서버의 별도 세션과 공유되지 않는다. 실제 단가 파일을 읽는 기준은 동일한 `RATE_CARD_FILE` 설정이다.

### API로 테스트

저장소 Python 기준은 3.11.9다. 의존성 설치와 환경 설정은 루트 README를 따른다.

```bash
LLM_PROVIDER=fake LLM_FAKE_RESPONSES=examples/agent3/fake_responses.json uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000/docs`의 **Agent3 MVP** API를 사용한다. 예시 입력은 `examples/agent3/input.json`이다. CR-A-001의 합성 상위 입력이며 실제 PM 승인·고객 계약이 아니다.

1. `POST /agent3/impacts`에 예시 입력을 넣는다.
2. `phase=WAITING_IMPACT_APPROVAL`, `draft=null`을 확인한다. 가상 10MD의 계산은 원가 4,480,000원·판매 산정가 6,425,000원(VAT 별도)이다.
3. 반환한 `session_id`, `state_revision`, `impact.meta.result_id`, `snapshot_hash`를 사용해 아래 승인 요청을 만든다.
4. 승인이면 `DRAFT_READY` 및 협의안을 반환한다. 같은 `request_id`의 동일 승인 재전송은 같은 결과를 반환한다.
5. 공수를 수정하면 금액이 바뀌고 기존 협의안·승인은 이력으로 보존된다. 최신 버전을 다시 승인한다.

승인 요청 형식:

```json
{
  "state_revision": 1,
  "target_result_id": "반환된 impact.meta.result_id",
  "snapshot_hash": "반환된 snapshot_hash",
  "request_id": "승인 요청 고유 ID",
  "reviewer_ref": "백엔드가 확인한 PM 사용자 참조",
  "approved": true,
  "reason": "가정과 공수 확인",
  "acknowledge_unknown_schedule": true
}
```

일정이 미산정이면 그 한계를 확인하는 `acknowledge_unknown_schedule=true`가 있어야 승인한다. 이는 일정이 확정됐다는 뜻이 아니다. 반려 시 `approved=false`와 사유를 넣는다. 단가 누락·blocking 질문·산정 불가 항목은 승인할 수 없다.

공수 수정 요청은 `state_revision`, `target_result_id`, `reason`, `estimates[{item_ids, lines}]`를 사용한다. `lines`는 현재 공유 Line 구조다. 이번 기본 UI 계약에서 금액·일정 숫자를 직접 수정하는 것은 허용하지 않는다. 일정·기준선·추가 답변이 달라지면 최신 Agent3Input으로 새 산정을 요청한다. 같은 요청의 새 산정이 생기면 이전 세션을 승인할 수 없다.

Fake 응답은 파일에 한 건만 들어 있다. 새 모델 산정을 반복하려면 서버를 재시작하거나 응답 배열에 제안 예시를 추가한다. PM 수정·승인·초안 조립은 추가 LLM 호출을 하지 않는다. `local`/`bedrock` 공급자로 바꾸면 같은 구조화 클라이언트를 사용하지만 실제 모델 실행은 별도 설정·검증이 필요하다.

## 단가표를 전달받았을 때

현재 기본값은 `data/kb/rate_cards.json`의 합성 단가다. 나중에는 **저장소 밖의 전달받은 파일**을 지정하면 된다.

```dotenv
RATE_CARD_FILE=/absolute/path/company_rate_cards.json
```

형식은 현재와 동일하게 최상위 `rate_cards`, 그 아래 단가표 ID별 `rates`, 직군별 `cost`·`price`(KRW/MD)를 둔다. `Agent3Input.rate_card_id`로 사용할 표를 선택한다. 모델 프롬프트에는 단가표를 전달하지 않는다. 실제 회사 가격 파일은 Git에 커밋하지 않는다.

- 파일은 호출 시 읽으므로 교체 후 오래된 프로세스 캐시를 사용하지 않는다.
- 산정 시 사용한 단가표의 전체 내용 해시를 세션에 보관한다. 승인 직전 값이 달라졌으면 409로 거부하고 재계산을 요구한다.
- 금액 계산 자체는 기존 `calc_cost`가 담당한다. 추후 CN API/DB로 바꿀 때 `rate_cards()`의 데이터 조회 경계와 계산 책임을 협의한다.
- 이번 구현은 과거 단가표 파일을 영속 보관하지 않는다. CN 연결 시 불변 버전 또는 당시 snapshot 참조를 남겨야 한다.

## 현재 한계와 서버 경계

- 저장은 **프로세스 메모리**다. 서버 재시작·다중 worker에서는 세션을 공유/복구하지 못한다. 지금은 단일 프로세스 로컬 MVP이며 CN 업무 DB 연동은 미구현이다.
- 새 API는 **신뢰된 백엔드 연동용**이다. 인증·인가가 없으므로 외부 사용자에게 직접 노출하지 않는다. `reviewer_ref` 문자열은 사용자 인증을 구현했다는 뜻이 아니다. 실제 PM 신원·회사/프로젝트 접근·최신 상위 결과 확인은 CN 백엔드에서 해야 한다.
- 입력의 상위 결과가 새로 바뀌었다는 사실을 AI 서버가 다른 시스템에서 자동 감지하지는 않는다. 백엔드는 최신 입력으로 재산정을 요청하고 구버전 결과를 비활성화해야 한다.
- 범위 판단을 에이전트 3가 다시 하지 않는다. 인용은 상위 결과에서 검증된 것만 복사한다.
- 공수의 현실성은 미검증이다. 예시 숫자는 API·금액 계산·승인 흐름 시험용이다. 금액 산술의 일치가 공수 추정 정확도를 뜻하지 않는다.
- 내부 초안에는 상세 가정·질문이 포함될 수 있다. 고객용 필드 선택·문서 내보내기·발송은 별도 구현한다.

## 검증

```bash
python -m pytest tests/test_agent3_agent.py tests/test_agent3_workflow.py -q
python -m pytest tests/test_agent3_streamlit.py tests/test_demo_streamlit.py -q
python -m pytest -q
```

검증은 FakeLlm을 통한 구조화 조립, 실제 코드 계산, 승인 전 초안 차단, 수정 후 재승인, 구버전·단가 교체 충돌, 중복 승인, 누락 정보 등을 확인한다. 실제 Bedrock/로컬 모델 추론 품질이나 운영 DB 복구 검증은 포함하지 않는다.
