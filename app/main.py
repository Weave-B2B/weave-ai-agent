from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="scope-agent", description="범위 변경 AI 에이전트 오케스트레이션 (① 구조화 → ② 범위 판단 → ③ 영향 산정)")
app.include_router(router)
