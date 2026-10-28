from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # fixture: 데이터셋 정답을 에이전트 대신 돌려줌 (오케스트레이션 개발·계약 테스트용)
    # llm: 실제 에이전트 (구현 후)
    agent_mode: str = "fixture"

    data_dir: Path = ROOT / "data"
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
