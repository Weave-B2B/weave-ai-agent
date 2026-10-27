from pathlib import Path

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
    database_url: str = ""  # Postgres 체크포인터·상태 저장 (thread_id = case_id)

    stage_timeout_sec: int = 60       # 단계당 시간 제한 (공통기준 D-6)
    tool_max_retries: int = 2         # retryable 도구 실패 자동 재시도 (공통기준 11장)


settings = Settings()
