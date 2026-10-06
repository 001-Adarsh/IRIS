"""
ChatEngine module for IRIS Agent.

This module provides a simple chat interface that wraps the IRISAgent
to maintain conversation history, resolve context, plan, and execute
the plan. It improves the chat experience by preserving context
across turns and providing a clean API for interacting with the agent.
"""

from typing import List, Dict, Any, Optional
from core.agent import IRISAgent
from core.planner import IRISPlanner
from core.executor import IRISExecutor
from core.provider_manager import ProviderManager


class ChatEngine:
    """
    A lightweight chat engine that manages conversation history and
    delegates to IRISAgent for context resolution, planning, and execution.
    """

    def __init__(self, agent: IRISAgent, planner: Optional[IRISPlanner] = None, executor: Optional[IRISExecutor] = None):
        self.agent = agent
        self.planner = planner or IRISPlanner()
        self.executor = executor or IRISExecutor()
        self.history: List[Dict[str, str]] = []

    def send(self, user_message: str) -> str:
        context = self.agent._resolve_context(self.history, user_message)
        plan = self.planner.plan(context)
        result = self.executor.execute_plan(plan)
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": result})
        return result

    def get_history(self) -> List[Dict[str, str]]:
        return list(self.history)

    def clear_history(self) -> None:
        self.history.clear()