from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
from pathlib import Path
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.routes import router
from app.api.agent1_routes import router as agent1_router
from app.api.agent2_routes import router as agent2_router
from app.api.internal_analysis_routes import router as internal_analysis_router
from app.api.internal_ingest_routes import router as internal_ingest_router
from app.api.agent3_routes import router as agent3_router
from app.api.analysis_routes import router as analysis_router
from app.api.supervisor_routes import router as supervisor_router
from app.api.execution_routes import router as execution_router
from app.api.run_routes import router as run_router, register_error_handlers
from app.api.run_supervisor_routes import router as run_supervisor_router
from app.api.local_run_routes import router as local_run_router
from app.llm.factory import build_llm
from prototype.rate_routes import router as prototype_rate_router
from prometheus_fastapi_instrumentator import Instrumentator


_log = logging.getLogger("app.startup")


def _parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def _warn_local_env_drift() -> None:
    """Warn only when local .env exists and is missing active keys from .env.example or runs in fixture mode."""
    env_path = Path(".env")
    if not env_path.is_file():
        return
    example_path = Path(".env.example")
    if example_path.is_file():
        missing = sorted(_parse_env_keys(example_path) - _parse_env_keys(env_path))
        if missing:
            _log.warning("[ENV WARNING] .env.example 대비 누락된 키가 있습니다: %s", ", ".join(missing))
    fixture_modes = [
        name for name, val in (
            ("AGENT_MODE", settings.agent_mode),
            ("AGENT1_MODE", settings.agent1_mode),
        )
        if val == "fixture"
    ]
    if fixture_modes:
        _log.warning("[ENV WARNING] fixture 모드로 실행 중입니다 (%s). 실제 LLM 테스트 시 llm으로 변경하세요.", ", ".join(fixture_modes))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate selected LLM configuration before starting LLM mode."""
    _warn_local_env_drift()
    if settings.agent_mode == "llm":
        build_llm(settings)
    yield


app = FastAPI(
    title="scope-agent",
    description="범위 변경 AI 에이전트 오케스트레이션 (① 구조화 → ② 범위 판단 → ③ 영향 산정)",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(agent1_router)
app.include_router(agent2_router)
app.include_router(internal_analysis_router)
app.include_router(internal_ingest_router)
app.include_router(agent3_router)
app.include_router(analysis_router)
app.include_router(supervisor_router)
app.include_router(execution_router)
app.include_router(run_router)
app.include_router(run_supervisor_router)
app.include_router(local_run_router)
register_error_handlers(app)
app.include_router(prototype_rate_router)

# The developer-only API is enabled only by the explicit local prototype runner.
if os.environ.get("PROTOTYPE_LAB") == "1":
    from prototype.lab import router as lab_router
    app.include_router(lab_router)

# Local confirmation UI; business APIs and workflow ownership remain unchanged.
prototype_dir = Path(__file__).resolve().parents[1] / "prototype"
if prototype_dir.is_dir():
    app.mount("/prototype", StaticFiles(directory=prototype_dir, html=True), name="prototype")

if settings.metrics_enabled:
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=[r"^/metrics$"],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )