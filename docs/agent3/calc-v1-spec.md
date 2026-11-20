# 계산식 v1 적용 기준 (calc-v1)

작성일: 2026-11-17. Notion "계산식 v1"의 D 파트(13~42번) 중 이번에 적용한 범위와 규칙이다. 도급 변경 견적에만 적용한다.

**금액·일정 공식은 백엔드에서만 실행한다.** `weave-backend`의 `PricingCalculator`·`BusinessDayCalculator`가 유일한 공식이며, AI의 `calc_cost`·`calc_pricing`은 HTTP 호출 어댑터다. Python 금액·일정 공식과 승인 시 재계산을 제거했다. 공식의 골든 벡터 검증도 Backend에서 수행한다. AI에 남아 있는 과거 골든 자료는 참조용이며 Python 공식의 실행에 사용하지 않는다.

공식 계산을 사용할 수 없으면 예시 금액·그룹 금액 합산으로 대체하지 않는다. 계산이 없거나 검증에 실패한 결과는 최종 승인하지 않는다.

## 적용 범위

| 번호 | 항목 | 상태 |
|---|---|---|
| 13 | 작업량 × 단위 작업 시간 | 미적용. 공수는 기존 공수 기준(MD)을 그대로 쓴다 |
| 14·15 | 시간 ↔ MD | 적용. 입력은 0.5 MD 단위, 시간은 `MD × hours_per_md` 파생값 |
| 16~18 | 총·직군별 공수, 공수 증감 | 적용 (`total_md`, `md_by_role`, `md_delta`) |
| 19~22 | 실제 공수 지표 | 범위 밖 (F 파트) |
| 23~36 | 작업별 견적 ~ 마크업률 | 적용 |
| 37 | 소요 영업일 | 적용 |
| 38~41 | 선행 조건, 시작 가능일, 예상 완료일, 일정 영향 | 승인된 일정·공수 엑셀이 있고 모든 작업 행에 담당자가 정해지면 Backend 일정 계산기(schedule-v1)로 계산. 아니면 `plan_detail.plan=null` + 사유. [일정 계산 기준](schedule-calculator.md) |
| 42 | 남은 영업일 | Backend 계산기는 지원. 현재 공식 계산 API에 종료일 연결이 없어 null |

## 계산 단위와 순서

- 작업: 작업별 견적(23), 시간 변환.
- 산정 그룹: 기존 `Totals`(그룹 공수, 인건비 판매가·원가). 값과 의미를 바꾸지 않았다.
- 요청 전체: `pricing`. 경비·별도 견적 항목·할인·예비비·부가세는 여기서 한 번만 적용한다.

순서: 공유 작업 중복 제거(`_combine`) → 그룹별 `Totals` → 요청 전체 인건비 → 경비·별도 견적 항목 → 할인 → 예비비(할인 후 금액 기준) → 공급가액 → 부가세·총액. 부분 재산정·재계산 뒤에도 요청 전체 집계는 다시 만든다. 미산정 그룹이나 단가 누락이 하나라도 있으면 `pricing`은 null이다.

## 용어

| 필드 | 뜻 |
|---|---|
| `labor_price` / `labor_cost` | 인건비 견적(24) / 내부 인건비 원가(25) |
| `expense_cost` / `expense_billable` | 경비 내부 원가 / 청구 가능한 경비 + 마크업(27) |
| `separate_amount` / `separate_cost` | 별도 견적 항목 금액 / 원가 |
| `direct_cost` | 직접 원가(26) = 인건비 원가 + 경비 원가 + 별도 항목 원가 |
| `subtotal` | 견적 소계(28) |
| `discount_amount`, `discounted_amount` | 할인액, 할인 후 견적(29) |
| `contingency_amount` | 예비비(30) |
| `supply_amount` | 공급가액 = 견적액. VAT 별도 |
| `vat_amount`, `total_with_vat` | 부가세(32), 부가세 포함 금액(33) |
| `profit`, `profit_ratio`, `markup_ratio` | 직접 이익(34), 이익률(35), 마크업률(36). 공급가액 기준이며 VAT 포함 총액을 쓰지 않는다 |
| `md_delta`, `supply_delta` | 이전 결과 대비 공수 증감(18), 견적 차이(31) |

- 비율은 `0.3` 형태(백분율 아님), 소수 4자리. 기존 `Totals.margin`은 "그룹 인건비 기준 비율"이고 `profit_ratio`와 다르다.
- 공급가액이 0이면 `profit_ratio`는 null. 원가가 양수면 `markup_ratio`는 `-1.0000`, 원가가 0이면 null.
- 0원과 미확정(null)을 구분한다. 모르는 값을 0으로 채우지 않는다.

## 반올림

보고하는 금액마다 원 단위로 1회만 반올림한다(`HALF_UP`, 부가세는 정책의 `rounding`). 인건비는 미반올림 합을 반올림하고, 그 뒤 단계는 확정된 원 단위 금액끼리 더한다. `lines[].amount`는 표시용이며 그 합이 `labor_price`와 같다고 가정하지 않는다.

## 입력 검증 (보정하지 않고 거부)

음수 공수·경비, 0.5 단위가 아닌 MD, 할인액이 소계 초과, 금액 할인과 비율 할인 동시 입력, 0~1 밖의 세율·할인율·예비비율, 청구 불가 경비에 청구액·마크업 지정, `expenses_state`와 경비 목록 불일치.

## 정책 상태와 잠정 표시

- 정책: 백엔드 `app.calc.*`가 공식 원본이다. AI의 `data/kb/calc_policy.json`은 고정 예시 선택에만 쓰며 공식 계산으로 전송하지 않는다. 상태는 `SYNTHETIC / UNCONFIRMED / CONFIRMED`다. 현재 값(과세, 세율 10%, 부가세 반올림, 예비비 미적용, 할인 → 예비비 순서)은 합성 가정이다.
- 경비: `NOT_ENTERED`(미입력) / `CONFIRMED_NONE`(없음 확인) / `ENTERED`.
- 정책이 `CONFIRMED`가 아니거나 경비·원가가 미입력이면 `provisional=true`다. 내부 잠정 초안은 만들 수 있지만 공식 최종 승인은 차단한다. 백엔드 `CALC_REQUIRE_CONFIRMED_POLICY` 기본값은 **true**다. AI 로컬 합성 시연에서만 false를 허용하며, 백엔드 UUID 단가표는 AI에서도 정책 확정을 요구한다.

## 일정

- 소요 영업일(37) = `ceil(작업 시간 / 일별 유효 투입 시간)`. 일별 유효 투입 시간은 기존 업무·휴가를 뺀, 이 변경 작업에 쓸 수 있는 시간이다. 설정이 없으면 1MD 시간(전일 투입 1인)을 가정하고 `assumed_full_time=true`로 표시한다. 0시간 작업은 0일.
- 남은 영업일(42) = 기준일 다음 날부터 종료일까지(종료일 포함). 계획 완료일 데이터가 없어 계약 종료일을 쓰면 `remaining_basis=contract_end`로 표시한다. 종료일이 없으면 그 필드만 null.
- 소요 영업일은 완료 기간이 아니다. 시작일·완료일·일정 영향은 `plan_detail`(schedule-v1)에 따로 계산한다. 엑셀·담당자 배정이 없으면 계산하지 않는다.
- 기존 `Schedule.business_days`는 "지연 영업일(41)" 의미로 고정하며 소요 영업일을 넣지 않는다.
- 공식 API는 현재 Backend 설정의 유효 가용시간을 사용하고 종료일은 전달하지 않는다. 따라서 42번은 null이다. AI가 로컬 계약 종료일로 이를 다시 계산하지 않는다. 종료일·프로젝트 달력의 공식 연결은 별도 후속 작업이다.

## 결과 계약

- `ProvisionalAgent3Output.pricing`(요청 전체 스냅샷, 입력 원문 `inputs`와 `input_hash` 포함), `schedule_detail`. 계산 시각·주체·정책 해시·스냅샷 ID를 함께 저장한다. `meta.debug.calc`에는 실행 진단도 둔다.
- `agent3-run/2`가 두 필드를 포함한다. `/1` 결과는 읽을 수 있지만 `pricing`이 없으면 최종 승인 전에 재계산해야 한다.
- 고객 문서에는 인건비 견적, 공급가액, 부가세, 합계, 소요 영업일만 쓰고 원가·이익·마크업은 쓰지 않는다.

## 2026-11-18 금액 연결 보완

- `pricing.inputs.request`: 사용한 단가표 전체·계산 정책·경비·별도 항목·할인·이전 값, `inputs.as_of/calendar/timezone/project_id`를 함께 보존한다.
- `input_hash`와 `snapshot_id`는 위 입력의 정규화 SHA-256이다. `policy_hash`는 실제 사용한 정책의 해시이며, 백엔드 모드에서는 로컬 정책을 대신 해시하지 않는다.
- 백엔드 공식 계산 API 응답 `snapshot`의 메타데이터를 AI `pricing`에 병합한다. 정책 조회는 `GET /internal/v1/estimates/policy`다.
- 개별 도구와 통합 Run 모두 계산 누락·입력 변조·정책 변경을 최종 승인 시 다시 검증한다. `/1`은 읽을 수 있지만 최신 스냅샷 없이는 통합 승인할 수 없다.
- 백엔드 `estimate_calculation_snapshot`에 검토 버전별 불변 입력·결과를 저장한다. PM 조정은 `PATCH /api/v1/estimates/reviews/{reviewVersionId}/pricing`로 저장한다. `expectedInputHash`, `adjustments`, `reason`을 받으며 승인 권한이 필요하다. 오래된 입력은 거절한다.
- PM 공수 수정은 저장된 정책·조정값을 유지한 채 재계산한다. 새 Run 결과를 남기고 문서·검토 묶음을 비운다. 총괄은 작업 산정 LLM을 다시 부르지 않고 최신 수치로 문서를 재생성한다.
- E 협의안의 공급가액과 재승인 금액을 통일하고, 협의안 해시에 `calculationSnapshotHash`를 포함한다.
- 화면의 제안가는 그룹 인건비의 합이 아닌 `pricing.supply_amount`다. 모르는 직접원가·이익은 미산정으로 표시한다.
- 금액 합계는 요청 전체에서 미반올림 합을 반올림한다. 그룹별 표시 합계와의 일치를 승인 조건으로 사용하지 않는다.

## 남은 일 / 배포 전 확인

1. PD·CN의 할인·예비비·세율·반올림 정책 확정. AI가 회사 정책을 임의 확정하지 않는다.
2. 계산 스냅샷 테이블은 Backend Flyway `V5__estimate_calculation_snapshot_and_impact_workbook.sql`로 자동 적용된다(수동 DDL 불필요). CN의 ERD 반영은 별도.
3. 경비·할인 PM 편집 전용 프론트 폼은 후속. 저장 API와 기존 결과 표시를 먼저 연결했다.
4. 실제 모델과 실제 HTTP 양방향으로 실행하는 전체 E2E는 별도다. 가상 모델 회귀 테스트·서비스/DB 통합 테스트와 구분한다.
5. 38~41번은 엑셀 기반 일정 계산기(schedule-v1)로 계산한다. 샘플 기대값과 계산 규칙은 [schedule-calculator.md](schedule-calculator.md). 37번 소요 영업일은 참고값으로 유지한다.
6. Figma 패널 예시 수치 중 개발비 400만·예비비 0·공급가액 600만의 근거는 확인이 필요하다.

## 2026-11-18 공식 계산 단일화

| 용도 | 경로 | 동작 |
|---|---|---|
| 항목별 인건비 | `POST /internal/v1/estimates/labor-totals` | 프로젝트·단가표 UUID와 작업 라인을 받고 기존 `Totals` 계약 반환. 단가 누락은 `NEEDS_INPUT` |
| 요청 전체 금액·일정 | `POST /internal/v1/estimates/calculations` | DB 단가표·정책으로 계산하고 `pricing`, `schedule`, `snapshot` 반환 |
| 승인 전 검증 | `POST /internal/v1/estimates/validations` | 저장된 `pricing`, 프로젝트·단가표 ID, `current`, `require_confirmed`를 받아 Backend에서 입력·금액·단가표·정책 재검증 |
| 현재 정책 식별 | `GET /internal/v1/estimates/policy` | 공식 정책 해시 반환 |

기존 내부 서비스 토큰을 그대로 사용하며 새 인증 변수는 없다. AI는 입력 해시·항목 연결을 확인하지만 금액을 재계산하지 않는다. 검증 통신 실패도 승인 허용으로 바꾸지 않는다. Backend는 외부 계산 입력의 `sell_rate`·`cost_rate` 지정도 거절하며 저장된 PM 수정 라인은 내부 경로로 처리한다.

### 로컬 시연과 테스트

- 기본 `CALCULATION_MODE=backend`: 실제 프로젝트·단가표 UUID, Backend URL·내부 토큰이 필요하다. 공식 API와 함께 배포해야 한다.
- `python -m prototype.run_demo --lab --mode fake --port 8772`: 명시적으로 `fixture`를 설정한다. `data/fixtures/backend_calculations.json`의 **고정 응답만 재생**하며 계산 공식은 실행하지 않는다. 미등록 공수·할인·단가 조합은 명확히 거절한다. 임의 단가표 편집으로 새 계산이 되는 완전한 오프라인 계산기는 제공하지 않는다.
- 실제 UUID 단가표는 `fixture` 설정에서도 항상 Backend를 호출한다. Backend 오류 시 예시 응답으로 전환하지 않는다.
- 실제 LLM 시연은 기본적으로 Backend 계산을 사용한다. 합성 입력만 재생하려면 일반 AI 서버를 `CALCULATION_MODE=fixture`로 명시 실행한다. `run_demo`의 실제 모델 모드는 Backend를 선택한다.
- 일반 Python 테스트는 저장된 응답을 사용하므로 Java·Backend 실행이 필요 없다. 공식의 금액·일정·반올림 테스트는 Backend `CalcV1GoldenTest`가 담당한다.
- 예시 갱신 시에만 Backend에서 `./gradlew calculationFixtureClasspath`로 테스트 클래스를 준비하고 AI 테스트를 `WEAVE_RECORD_CALCULATIONS=1`로 실행한다. `JAVA_HOME`과 `WEAVE_CALC_CLASSPATH_FILE`(Backend `build/calculation-fixture-classpath.txt`)도 지정한다. 이 테스트 전용 기록기는 Backend Java 공식을 실행해 응답을 저장하며 운영 앱에서 실행하지 않는다. 갱신 후 기록 변수를 끄고 같은 테스트가 재생만으로 통과하는지 확인한다.

## 계산 단일화 검증 기록

- Python: Agent 3·Run·총괄·프로토타입·계산 API 어댑터·도구·샘플 검사 **349 통과, 22 건너뜀**. 예시 기록을 끈 일반 실행에서도 같은 결과를 확인했다. Streamlit은 실행 대상에서 제외했다.
- Backend: D·E·Run 연결·모듈 경계 **145 통과, 실패·건너뜀 0**. `WEAVE_DB_TEST=true`로 로컬 PostgreSQL 영속화·승인·협의안 연결을 포함했다.
- 화면 회귀: 기존 Node 검사 **53 통과**. 실제 외부 LLM·브라우저 E2E·배포는 실행하지 않았다. 실제 Backend HTTP 환경을 요구하는 선택 검사는 위 건너뜀에 포함된다.
- 금액 변조, 입력 해시 변경, 다른 프로젝트/단가표, 정책 변경, 미확정 승인, 토큰 없는 접근, 잘못된 외부 행 단가, 계산 HTTP 오류·타임아웃·형식 오류를 검증했다.

## 이전 단계 검증 기록

- Agent 3·Run·계산식·샘플 데이터 및 데이터 무결성 관련 Python 검사: 283 통과, 20 건너뜀, 기존 공통 스키마 해시 불일치 1건 실패.
- 실패한 스키마와 manifest는 작업 전 HEAD의 파일과 동일하며, HEAD에서도 두 해시가 불일치함을 확인했다. 이번 작업은 공통 스키마·manifest를 수정하지 않았다.
- 레거시 Streamlit 검사는 이번 실행 대상에서 제외했다. 해당 UI를 정상 동작한다고 보고하지 않는다.
- 화면: `node --experimental-vm-modules --test prototype/tests/*.test.mjs` 53건 통과. `--experimental-vm-modules`는 기존 화면 테스트가 사용하는 VM 모듈에 필요하다.
- 백엔드 D·E·연결 관련 134건 통과(로컬 PostgreSQL 통합 검사 포함), 이후 최종 Run 연결 20건 재확인 통과.
- 실제 외부 모델 호출·브라우저 E2E·배포는 수행하지 않았다. 신규 경비·할인 입력 폼은 후속이며 저장 API를 통해 검증했다.

## 2026-11-20 PR 전 재검증

- 최신 `dev`의 OpenTelemetry 실행 설정을 보존했다. SQS 전환 대상인 `app/api/internal_analysis_routes.py`와 `run_impact_analysis()`는 변경하지 않았다.
- Python 3.13.9 전체 실행: **868 통과, 기존 실패 2, 건너뜀 26, deselected 2**. 기존 실패는 Agent 1 알 수 없는 fixture 요청의 HTTP 상태와 공통 스키마 manifest 해시다. 변경 전 HEAD를 별도 임시 폴더에 풀어 동일 실패를 재현했다. 두 항목은 이번 변경에서 수정하지 않으며 AI PR은 Draft로 공유한다. 저장소·이미지 기준 Python은 3.11.9이고, 로컬 전체 테스트의 버전과 구분한다.
- Streamlit 기존 단가 기대값을 실제 합성 단가표에 맞췄다. Backend Java 계산기로 고정 예시를 보완한 뒤 기록 모드를 끄고 일반 재생 테스트에서도 확인했다. 미등록 예시·Backend 실패는 화면 오류로 안내한다.
- 비수치·NaN 공수는 요청 전 입력 오류로 거절한다. 금액·일정 공식은 Python에 추가하지 않았다.
- Node 화면 검사: `node --experimental-vm-modules --test prototype/tests/*.test.mjs` **53 통과**.
- Backend: 로컬 PostgreSQL을 포함한 `./gradlew build` **542개 테스트, 실패 0, 건너뜀 1**. 전체 컨텍스트의 DB 연결 수 제한을 피하기 위해 검증 실행에서만 Hikari 최대 풀 2·최소 유휴 0을 사용했다.
- 모노마켓 예시 엑셀을 로컬 브라우저로 실제 업로드했다. `STORED/APPROVED`, 색인 요청 ID 없음, 계약 청크·기준선 편입 0건을 확인했다. 실제 Backend 내부 조회·LocalStack 원본 읽기로 작업 8건·88시간·담당자 5명을 확인했다.
- 실제 외부 LLM을 사용한 전체 산정·고객 문서·최종 승인 E2E와 배포 검증은 여전히 후속이다. 계산 정책 상태는 합성/미확정 여부를 보존하며 업무 승인으로 임의 확정하지 않는다.
