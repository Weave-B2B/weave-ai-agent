from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Backend Run 상태 API(/internal/v1/runs). 비어 있으면 Run API는 503으로 응답한다.
    backend_base_url: str = ""
    backend_internal_token: str = ""
    backend_timeout_sec: float = Field(default=10.0, gt=0)
    # Backend가 추가 coordination 계약을 지원할 때만 활성화한다(기존 v2 순차 경로는 유지).
    backend_coordination_enabled: bool = False
    # Loopback prototype only. Explicit local account; no default ADMIN fallback.
    prototype_backend_company_id: str = ""
    prototype_backend_member_id: str = ""
    prototype_backend_member_role: str = ""
    prototype_backend_actor_label: str = "로컬 개발 계정"
    # 색인 결과(A-MQ-02) 전달 경로. 로컬에서 정식 내부 API가 없으면 /dev/document-versions/{document_version_id}/ingest-result
    backend_ingest_result_path: str = "/internal/v1/document-versions/{document_version_id}/ingest-result"
    # 기준선 문서·청크 원문 출처. local: data/kb 샘플 · backend: 백엔드 내부 API(업로드·색인한 실제 문서)
    kb_source: str = "local"
    backend_baseline_chunks_path: str = "/internal/v1/baselines/{baseline_id}/chunks"
    # 범위 분석 결과 전달 (백엔드 ai_analysis). 같은 내부 토큰을 백엔드→AI 분석 요청 확인에도 쓴다
    backend_analysis_result_path: str = "/internal/v1/ai-analyses/{ai_analysis_id}"
    backend_rate_card_path: str = "/internal/v1/rate-cards/{rate_card_id}"
    backend_project_rate_card_path: str = "/internal/v1/projects/{project_id}/rate-card"
    # 계산식 v1 공식 계산기(백엔드). 백엔드 단가표(UUID)로 산정할 때 호출한다.
    backend_calculation_path: str = "/internal/v1/estimates/calculations"
    # 공식 연결이 기본. fixture는 합성 ID의 저장된 예시 응답만 재생한다.
    calculation_mode: Literal["backend", "fixture"] = "backend"
    # true면 계산 정책 미확정·경비 미입력(잠정) 결과의 최종 승인을 막는다. 합성 테스트 흐름은 false.
    calc_require_confirmed_policy: bool = False
    # 총괄 Clarifier: 에이전트 gap을 합쳐 질문·가정으로 정리 (꺼져 있으면 기존 질문 정리 방식)
    supervisor_clarifier_enabled: bool = False
    prototype_run_db: Path = ROOT / ".prototype-runs" / "runs.sqlite3"

    # fixture: 데이터셋 정답을 에이전트 대신 돌려줌 (오케스트레이션 개발·계약 테스트용)
    # llm: 실제 에이전트 (①②③ 모두 LLM 호출)
    agent_mode: str = "fixture"
    agent2_style: str = "agent"      # agent: LLM이 도구를 골라 쓰는 에이전트 · workflow: 고정 흐름 (비교용)
    agent2_use_hints: bool = True    # ①의 feature_candidates를 검색 없이 후보에 넣을지 (평가 시 힌트 유무 비교)

    # ② 검색 임베딩 (LLM provider와 별개). none이면 어휘 검색만
    embed_provider: str = "none"     # none · fake · openai · gemini · bedrock
    embed_model: str = ""            # 예: text-embedding-3-small · gemini-embedding-001 · amazon.titan-embed-text-v2:0
    embed_api_key: str = ""          # 비우면 LLM_API_KEY 사용 (openai)
    embed_base_url: str = "https://api.openai.com/v1"
    embed_dim: int = Field(default=1024, ge=8)   # Titan v2와 같은 1024로 맞춰 두면 전환 시 차원 변경 없음
    embed_index_path: Path = ROOT / "data" / "index" / "chunk_embeddings.json"
    retrieval_mode: str = "hybrid"   # 임베딩이 켜졌을 때: hybrid(어휘+벡터 RRF) · vector · lexical
    vector_store: str = "local"      # local: embed_index_path JSON · pgvector: vector-db(weave_vector)
    vector_db_url: str = ""          # 예: postgresql://weave:weave@localhost:5433/weave_vector (weave-infra 기본값)
    # 문서 색인 (app.indexing). 로컬은 weave-infra LocalStack (S3_ENDPOINT=http://localhost:4566)
    # PDF 파서. r2: docling + 품질 낮은 페이지 이미지 전사 + 구조 청킹(b1-v3, 기본)
    #           legacy: pdftotext + 섹션 코드 규칙 (원래 합성 템플릿 형식만 읽는다. 되돌릴 때 사용)
    pdf_parser: Literal["r2", "legacy"] = "r2"
    # 품질이 낮은 페이지를 이미지로 전사할 모델. bedrock은 AWS 자격 증명(배포는 Pod 역할), openai는 아래 키
    pdf_r2_transcribe_provider: Literal["bedrock", "openai"] = "bedrock"
    pdf_r2_transcribe_model: str = ""   # 비우면 bedrock: Claude Haiku 4.5(global 프로필), openai: gpt-6-luna
    pdf_r2_openai_api_key: str = ""
    s3_endpoint: str = ""
    s3_access_key_id: str = ""      # 비우면 boto3 기본 자격 증명(환경 변수·~/.aws·IAM 역할). LocalStack은 아무 값(test)
    s3_secret_access_key: str = ""
    s3_region: str = "ap-northeast-2"
    s3_source_bucket: str = "weave-dev-files"
    s3_result_bucket: str = "weave-dev-files"

    # 백엔드 ↔ AI 결과 전달 방식. sqs: 결과 큐로 보낸다. 요청은 워커(python -m app.worker)가 요청 큐에서 받는다
    #   (로컬 .env.example·dev 배포 기본. 큐 URL이 필요해 코드 기본값은 http로 둔다)
    # http: 백엔드 내부 API로 바로 보낸다 (큐 설정 없이 테스트·단독 실행)
    result_transport: Literal["http", "sqs"] = "http"
    sqs_endpoint: str = ""           # 로컬 LocalStack: http://localhost:4566. 배포는 비운다 (Pod IAM 역할)
    sqs_region: str = "ap-northeast-2"
    sqs_index_request_url: str = ""
    sqs_index_result_url: str = ""
    sqs_analysis_request_url: str = ""
    sqs_analysis_result_url: str = ""
    # 워커가 받을 요청 큐 (index·analysis). 나중에 색인·분석 워커를 나눠 배포할 때 하나만 고른다
    worker_queues: str = "index,analysis"
    worker_concurrency: int = Field(default=2, ge=1)          # 동시에 처리할 메시지 수
    worker_visibility_sec: int = Field(default=120, ge=30)   # 처리하는 동안 이 시간만큼 계속 연장한다
    worker_max_receive_count: int = Field(default=3, ge=1)   # 큐의 DLQ 최대 수신 횟수와 같게 둔다

    data_dir: Path = ROOT / "data"
    rate_card_file: Path | None = None  # Defaults to data/kb/rate_cards.json; replace with a supplied file later.
    eval_dir: Path = ROOT / "eval" / "dataset"

    llm_model: str = ""
    llm_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "fake"
    llm_max_tokens: int = Field(default=4096, ge=1)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_read_timeout_sec: float = Field(default=50, gt=0)
    llm_connect_timeout_sec: float = Field(default=5, gt=0)
    ollama_read_timeout_sec: float = Field(default=300, gt=0)
    ollama_connect_timeout_sec: float = Field(default=10, gt=0)
    ollama_num_predict: int = Field(default=2048, ge=1)
    ollama_keep_alive: str = "30m"
    llm_base_url: str = ""
    ollama_base_url: str = ""
    ollama_model: str = ""
    llm_local_json_mode: str = "prompt"
    llm_fake_responses: str | None = None
    aws_region: str = "ap-northeast-2"
    database_url: str = ""  # Postgres 체크포인터·상태 저장 (thread_id = case_id)
    prototype_supervisor_db: Path | None = None  # Optional local SQLite file for Supervisor checkpoints.

    stage_timeout_sec: int = 60       # 단계당 시간 제한 (공통기준 D-6)
    tool_max_retries: int = 2         # retryable 도구 실패 자동 재시도 (공통기준 11장)

        # Prometheus HTTP metrics; explicitly enabled per environment.
    metrics_enabled: bool = False


settings = Settings()
