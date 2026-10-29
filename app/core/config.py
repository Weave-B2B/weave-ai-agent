from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # fixture: 데이터셋 정답을 에이전트 대신 돌려줌 (오케스트레이션 개발·계약 테스트용)
    # llm: 실제 에이전트 (구현 후)
    agent_mode: str = "fixture"
    agent2_mode: str = "fixture"     # fixture 모드에서 ②만 llm으로 돌릴 때 llm
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

    data_dir: Path = ROOT / "data"
    rate_card_file: Path | None = None  # Defaults to data/kb/rate_cards.json; replace with a supplied file later.
    eval_dir: Path = ROOT / "eval" / "dataset"

    llm_model: str = ""
    llm_api_key: str = ""
    llm_provider: str = "fake"
    llm_max_tokens: int = Field(default=4096, ge=1)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_read_timeout_sec: float = Field(default=50, gt=0)
    llm_connect_timeout_sec: float = Field(default=5, gt=0)
    llm_base_url: str = ""
    llm_local_json_mode: str = "prompt"
    llm_fake_responses: str | None = None
    aws_region: str = "ap-northeast-2"
    database_url: str = ""  # Postgres 체크포인터·상태 저장 (thread_id = case_id)

    stage_timeout_sec: int = 60       # 단계당 시간 제한 (공통기준 D-6)
    tool_max_retries: int = 2         # retryable 도구 실패 자동 재시도 (공통기준 11장)


settings = Settings()
