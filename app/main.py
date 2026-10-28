from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import settings
from app.api.routes import router
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
