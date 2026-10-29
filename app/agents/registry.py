from app.agents.base import AgentSet
from app.core.config import settings


def build_agents() -> AgentSet:
    if settings.agent_mode == "fixture":
        from app.agents.fixture import fixture_agents
        agents = fixture_agents()
        if settings.agent2_mode == "llm":               # ②만 실제 LLM으로 (①③은 정답 fixture)
            from app.llm.client import build_llm
            agents.agent2 = build_agent2(build_llm(settings))
        return agents
    from app.agents.agent1.agent import LlmAgent1
    from app.llm.client import build_llm
    from app.agents.agent3.agent import LlmAgent3
    return AgentSet(LlmAgent1(build_llm(settings)), build_agent2(build_llm(settings)), LlmAgent3(build_llm(settings)))


def build_agent2(llm):
    if settings.agent2_style == "workflow":
        from app.agents.agent2.agent import LlmAgent2
        return LlmAgent2(llm)
    from app.agents.agent2.tool_agent import ToolAgent2
    return ToolAgent2(llm)
