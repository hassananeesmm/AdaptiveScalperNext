"""The seven desk agents. Each is deterministic Python over a read-only
snapshot; an optional external model may only add an UNTRUSTED narrative."""

from adaptive_scalper.ai_desk.agents.coordinator import Coordinator
from adaptive_scalper.ai_desk.agents.learning_analyst import LearningAnalyst
from adaptive_scalper.ai_desk.agents.market_analyst import MarketAnalyst
from adaptive_scalper.ai_desk.agents.news_analyst import NewsAnalyst
from adaptive_scalper.ai_desk.agents.risk_analyst import RiskAnalyst
from adaptive_scalper.ai_desk.agents.skeptic import Skeptic
from adaptive_scalper.ai_desk.agents.strategy_analyst import StrategyAnalyst

ALL_AGENT_CLASSES = (MarketAnalyst, StrategyAnalyst, NewsAnalyst, RiskAnalyst, Skeptic, LearningAnalyst, Coordinator)

__all__ = ["ALL_AGENT_CLASSES", "Coordinator", "LearningAnalyst", "MarketAnalyst", "NewsAnalyst", "RiskAnalyst",
           "Skeptic", "StrategyAnalyst"]
