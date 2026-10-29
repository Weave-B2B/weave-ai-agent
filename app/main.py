from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.routes import router
from app.api.agent3_routes import router as agent3_router
from app.llm.factory import build_llm


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Validate selected LLM configuration before starting LLM mode."""
    if settings.agent_mode == "llm":
        build_llm(settings)
    yield


app = FastAPI(
    title="scope-agent",
    description="범위 변경 AI 에이전트 오케스트레이션 (① 구조화 → ② 범위 판단 → ③ 영향 산정)",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(agent3_router)

# Local confirmation UI; business APIs and workflow ownership remain unchanged.
prototype_dir = Path(__file__).resolve().parents[1] / "prototype"
if prototype_dir.is_dir():
    app.mount("/prototype", StaticFiles(directory=prototype_dir, html=True), name="prototype")
