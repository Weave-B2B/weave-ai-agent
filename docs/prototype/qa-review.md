# 변경요청 로컬 UI 독립 QA

검토일: 2026-10-28 / Asia/Seoul. 담당: `ui_qa` / `dev-team-qa`. 판정: **changes_requested**.

## 승인과 검토 대상

매니저가 전달한 사용자 직접 답변 **“QA 검토만 승인, 수정 전에는 다시 확인”**에 따른 검토다. 목표는 혼합안 B의 C02~C06 로컬 UI와 기존 Agent 3 API 경계를 독립 검증하는 것이다. 제품 코드·테스트·설정은 수정하지 않았다. QA 작성 파일은 이 보고서 하나다. 기존 사용자 변경을 되돌리지 않았다. 수정 및 수정 후 재검증 착수는 별도 승인 대상이다.

- 저장소: `/Users/mac/Documents/Project/Practical_Project/development/weave-ai-agent`
- 제품 검토 경로: `prototype/**`, `app/main.py`의 정적 mount 추가
- 기준: `AGENTS.md`, 원본 `.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/validate-conventions.yml`, `.agents/skills/manager-dev-workflow/SKILL.md` 및 protocol, `docs/dev-workflow/project.json`, `docs/prototype/source-map.md`, `readiness.md`, `implementation-plan.md`의 UI-01~13, `docs/agent3/agent3-mvp.md`
- 위 로컬 문서는 실제 읽었다. `source-map.md`에 연결된 Notion 원문은 이번 QA에서 재조회하지 않았다. 기획 문서의 제안/검토 상태를 전체 제품 승인으로 승격하지 않았다.
- C02~C04는 명시적인 fixture, C05/C06은 기존 생성·조회·수정·review 네 API다. 새 업무 API·DB·인프라·실제 인증·고객 발송은 제외했다.
- 사용자 미리보기 `127.0.0.1:8765`를 종료하거나 사용하지 않았다. QA 전용 `127.0.0.1:8766`과 별도 Chrome 탭 두 개를 사용했다.
- Python **3.12.14** 및 `/private/tmp/weave-ui-venv-20261028` 환경 결과다. 저장소 기준 Python 3.11.9의 검증 결과가 아니다.

## QA-01 — 산정에 제출된 일정 조건과 C05 표시가 다름

상태: **open**. 심각도: **medium**. 담당: 화면 구현 담당 / `prototype/app.js:131` (`impact()`의 `baseInput.constraints[0].text` 참조). 관련 기준: UI-03, UI-05, UI-07의 검토 맥락 및 UI-09의 일관성.

실제 브라우저 재현:

1. 기존 Agent 3 API 모드에서 CR-A-001 → 영향 산정으로 이동한다.
2. “테스트 입력 · 상위 합성 데이터와 실행 상태”를 열어 JSON의 `constraints[0].text`만 `QA 검증: 오픈일 2027-01-15 유지`로 바꾼다. case/project ID는 유지한다.
3. “영향 산정 실행”으로 새 산정을 만든다. 실제 fake 모델 API가 HTTP 200과 새 세션 `a3-112c5279cb1d4229895ae566b1328337`, 산정 결과 `agent3-impact-CR-A-001-05485922`를 반환했다.
4. C05 “일정 영향 → 기준 조건 · 합성 입력”을 확인한다. **`변경합의된 오픈일 2026-11-24 유지`가 계속 표시됐다.**
5. 일정 미산정 인지를 체크하고 PM 산정을 승인한다. C06의 같은 결과에 대한 서버 초안은 **`조건: QA 검증: 오픈일 2027-01-15 유지`**를 표시했다.

기대: C05 검토 대상의 기준 조건은 해당 산정에 실제 제출한 입력 snapshot과 일치해야 한다. 재산정하지 않은 편집 중 입력은 현재 결과의 기준 조건과 구분해야 한다.

실제: 서버 초안에는 새 조건이 반영되지만 C05는 처음 로드한 기본 fixture의 조건을 표시한다. `business_days=null`과 미산정 사유 자체는 올바르게 표시됐다.

영향: PM이 산정 결과와 다른 일정 조건을 보고 미산정 한계를 확인·승인할 수 있다. 모델 응답 정확성이나 서버 계산 오류로 판단한 것이 아니라, 실제 제출 입력과 UI 표시 간 불일치다.

확인된 원인: `impact()`가 해당 산정의 입력 대신 `baseInput.constraints[0].text`를 렌더한다. 단순히 편집 중 `inputText`로 대체하면 저장하지 않은 수정 내용이 기존 결과의 조건으로 보일 수 있으므로 충분한 수정이 아니다.

수정 제안: 산정 성공 시 그 요청에 제출한 입력 snapshot을 결과/세션과 함께 유지하고, C05 조건과 복구 시 표시를 그 snapshot에서 읽는다. 제출 전 편집과 현재 산정의 기준을 구분한다. 이 QA에서는 수정하지 않았다.

## 수용 기준별 실제 근거

| 기준 | 검증·결과 |
|---|---|
| UI-01 | 첫 화면과 상세의 합성 목록/원문/범위, 기존 API, 기본 실행기 가상 응답, 공급자 확인 불가 문구 확인. 읽기 전용 mock 전환 시 “가상 상태”와 API 미호출 안내 확인. |
| UI-02 | 12개 목록에서 1→2페이지 이동(10건/2건), `zzz-QA` 빈 검색, 프로젝트=멤버십 앱+범위=판단 보류 결과 CR-004 1건, 상세 이동 확인. 상태탭은 PM4/내부승인3/고객협의2/실행1/완료0/열린12와 실제 합계가 일치했다. 현재 fixture에 완료 요청이 없어 완료 추가 시 동작은 검증하지 않았다. |
| UI-03 | 기존 API HTTP 200으로 PM/UX/FE/BE/QA 작업, 가정, 10MD/원가4,480,000원/판매6,425,000원 수신. mock 지연 중 DOM에서 “처리 중…”·수정·승인 버튼 disabled 확인. 실제 API의 매우 짧은 pending 구간을 지연 주입해 재현하지는 않았다. 일정 조건 표시는 QA-01 실패. |
| UI-04 | 승인 전 C06 유효 초안 없음 및 복사 비활성 확인. 산정 준비와 PM 승인이 별도 표시됨. |
| UI-05 | null 일정이 “미산정”, 가용 인력·선행 작업 부족 사유로 표시됨. 임의 일수 없음. 기준 조건은 QA-01 참조. |
| UI-06 | FE 3→3.5MD 및 가정 수정, 사유 제출 후 10.5MD/원가4,705,000원/판매6,750,000원. 최초 FE3과 수정3.5를 분리했다. 기존 승인·초안이 history에 보존되고 현재 초안은 차단됐다. dirty 편집 중 승인 disabled, 3.2MD는 0.5단위 오류로 차단. |
| UI-07 | 미산정 인지 체크 전 승인 disabled, 체크 후 실제 review 성공. 검토자 참조는 로컬 테스트 값으로 안내됨. |
| UI-08 | 사유 없는 반려는 화면 입력 오류, 사유 입력 후 실제 review 성공. 초안이 없고 검토 대기 유지. 이후 승인 가능함을 확인. |
| UI-09 | 승인 후 C06 서버 초안·대상 결과·검토자·시각 표시. 상업 승인/고객 협의/발송/청구는 완료되지 않음. 복사 성공 toast와 복사한 초안의 QA 조건 문자열 일치 확인. Markdown 저장은 클릭했으나 도구의 download 이벤트 검출이 시간초과여서 파일 저장 결과는 미검증. |
| UI-10 | 두 번째 Chrome QA 탭이 같은 case를 새로 산정한 뒤 첫 탭 승인 시 실제 HTTP409 `a newer impact exists for this case`. 승인 잠금, 재확인/재산정 안내, GET 후에도 충돌 잠금 유지 확인. 동시에 같은 revision을 경합시키는 edit race는 미실행. |
| UI-11 | 기존 API에서 산정 대상 없음·상위 PM 검토 전·QA 단가 누락을 각각 실행. 상태와 후속 안내가 다르고 승인 불가, 금액 미산정 유지. 실패 phase는 명시적 읽기 전용 mock으로 검증. |
| UI-12 | 실제 request_revision=-1 제출 시 HTTP422 필드 오류. QA 서버 종료 중 조회로 실제 연결 실패. 재시작 뒤 기존 세션 조회로 실제404. 502/503 및 추가404/422/409/연결 오류 표시는 읽기 전용 mock으로 각각 검증했다. 실제 모델/단가 파일 장애를 일으킨 502/503 검증은 아니다. |
| UI-13 | 새로고침 뒤 기존 API GET으로 같은 v4/10.5MD와 history 복구. QA 서버 재시작 후 실제404에서 이전 작업을 비우고 승인 차단, 새 산정으로 정상 복구. 영속 저장으로 표시하지 않음. |

## 디자인·자산 검토

Figma를 읽기 전용 조회했다. `figma-design-to-code` 지침을 읽었으며 외부 디자인 쓰기/자산 생성은 하지 않았다. Figma session `fea5b99b-20d5-4374-92f2-aa62573f1894`, file `Pq2PLWBhkSupb6ThD8oELN`, C02 `280:749` 및 C05 `280:1661`의 실제 screenshot을 조회했다. `get_design_context`는 호출하지 않았다.

Chrome의 C02/C05/C06 실제 화면 screenshot을 보고 좌측 메뉴·상단바·회색 카드·상태 스트립·탭·표 구조를 원본과 비교했다. 로컬 모드 안내, API 미제공 열, PM 검토와 일정 미산정은 승인된 계약 차이에 따른 조정이다. 픽셀 단위 동일성으로 평가하지 않았다.

- `prototype/figma-assets.json`의 5개 화면 노드 매핑이 가리키는 파일은 모두 존재하고 비어 있지 않았다.
- 로컬 SVG 48개를 XML metadata로 검사했으며 모두 root width/height가 있었다.
- 실제 C05 DOM 이미지 29개/고유20종에서 깨짐 0개, 표시 크기와 natural 크기 차이 0개였다. 미표시 상태의 모든 자산 slot을 개별적으로 계측한 검증은 아니다.
- 기본 브라우저 viewport `innerWidth=1374`, page scrollWidth=1361로 전체 페이지 가로 넘침은 없었고 목록 표 내부 스크롤을 확인했다.
- 도구의 viewport `set({width:390,height:844})`를 호출했지만 실제 innerWidth가1374로 유지되어 모바일 크기 검증은 **미실행**이다. override는 reset했다. 개발자의 모바일 성공 기록을 QA 증거로 대체하지 않았다.

## 실행 명령과 회귀 결과

저장소 루트에서 실행했다. 서버는 localhost 바인딩 권한 때문에 sandbox 승인 실행으로 시작했으며 fake 응답과 합성 단가만 사용했다. 실제 AWS/Bedrock/local/OpenAI 추론을 호출하지 않았다.

```bash
/private/tmp/weave-ui-venv-20261028/bin/python --version
/private/tmp/weave-ui-venv-20261028/bin/python prototype/run_demo.py --port 8766
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=fake AGENT_MODE=fixture /private/tmp/weave-ui-venv-20261028/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=fake AGENT_MODE=fixture /private/tmp/weave-ui-venv-20261028/bin/python -m pytest -q -p no:cacheprovider tests/test_agent3_streamlit.py::test_demo_runs_repeatedly_without_exhausting_the_fake_client
node --check prototype/app.js
node --check prototype/api.js
node --check prototype/fixtures.js
git diff --check -- app/main.py prototype docs/prototype
```

- 전체 기존 suite 1회: **147 passed, 1 failed, 2 deselected, 1 warning / 16.83s**.
- 실패: `tests/test_agent3_streamlit.py::test_demo_runs_repeatedly_without_exhausting_the_fake_client`의 첫 `run_impact`에서 Streamlit AppTest 10초 timeout.
- 위 실패 테스트만 변경 없이 재실행: **1 passed / 1.03s**. 환경/순서/일시 부하 중 어느 것이 원인인지는 확정하지 않았다. 변경 소스의 회귀 결함으로 판정하지 않았고, 전체 suite가 모두 통과했다고도 보고하지 않는다.
- 경고: 기존 Starlette TestClient의 httpx 사용 중단 예정 경고.
- JS 문법3건 및 변경 범위 diff whitespace 검사 통과. 새 테스트·설정·제품 수정 없음.
- 직접 httpx→QA서버 호출은 sandbox의 localhost 연결 제한으로 실행되지 않았다. 같은 동작을 두 번째 브라우저 탭으로 실제 실행하여409를 검증했다.
- 실제 OS 한글 IME 조합, 다른 브라우저, Python3.11.9, 실제 모델 품질·실제 장애502/503·운영 인증/DB는 미실행이다.

## 최종 소스 동일성

Git HEAD는 `6d69950f47e4e5ce0c3e191ca6cfb441dc4794f7`이며 제품은 미커밋 변경 포함 상태로 검토했다.

QA 시작/종료에 `prototype`의 pycache 제외57개 파일과 `app/main.py`를 SHA256 manifest로 비교했다. **58개 모두 동일**, diff 출력 없음. manifest 생성 명령:

```bash
find prototype -type f ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 shasum -a 256 > /private/tmp/weave-qa-final.sha256
shasum -a 256 app/main.py >> /private/tmp/weave-qa-final.sha256
diff -u /private/tmp/weave-qa-start.sha256 /private/tmp/weave-qa-final.sha256
shasum -a 256 /private/tmp/weave-qa-final.sha256
```

최종 manifest 자체 SHA256: `dd5965915d3889de5c4c7e1c639800e6974469096a2d7913f8a96dca8c88ea43`.

| 파일 | SHA256 |
|---|---|
| prototype/app.js | `8cb0ac24963246af3a2d6f78514fc6fe30460871edd624e5e356f816e7a6579c` |
| prototype/api.js | `000fc36f2a7d5687ca23f82966867e0280ca795aa22d13e2db2d8e0ea8ed3e27` |
| prototype/styles.css | `7aad5b31c45e41e558c4910a24588eddd74d29c089be4093bae174112e63c085` |
| prototype/fixtures.js | `8d6e019232a322ccaaacb2a5bfa5afafb858e5074ba17caac3d0f801f8f204b5` |
| prototype/run_demo.py | `2ebf6856cb83ea931a96daf092d7889763e330740a1b3ff998c58bb87e405246` |
| prototype/figma-assets.json | `d973364faff99fb46afb0891ca7de29e18e2eba5e477391153dd305bdd1b21a0` |
| app/main.py | `f636325388b0eb7a9b4dfb19359b025bda22a9b97736f46088521bffa057421e` |
| docs/prototype/run.md (읽은 최종본) | `41d087207e175674a4824c523ede8c55398c4f87a13e34f707ee486b35316057` |

## 인수인계

다음 담당자는 매니저와 화면 구현 담당이다. QA-01의 재현·snapshot 기준 수정 제안을 먼저 읽어야 한다. 이번 과업은 검토 완료/changes_requested이며 수정과 수정 후 재검증은 시작하지 않았다. QA 전용8766 서버와 QA 탭은 정리하고 사용자 미리보기8765는 유지한다.
