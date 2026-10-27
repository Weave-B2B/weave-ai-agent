from app.agents.base import AgentSet
from app.core.config import settings


def build_agents() -> AgentSet:
    if settings.agent_mode == "fixture":
        from app.agents.fixture import fixture_agents
        return fixture_agents()
    from app.agents.agent1.agent import LlmAgent1
    from app.agents.agent2.agent import LlmAgent2
    from app.agents.agent3.agent import LlmAgent3
    return AgentSet(LlmAgent1(), LlmAgent2(), LlmAgent3())
