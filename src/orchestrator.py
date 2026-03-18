"""ARCANA AI — Main Orchestrator
LangGraph decision loop: SCAN → EVALUATE → PRIORITIZE → EXECUTE → LEARN
Priority = (Revenue × Probability) / (Time × Risk)
Runs every 15 minutes. Checks for STOP file (kill switch) every cycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from src.agents.communicator import Communicator
from src.agents.creator import Creator
from src.agents.automator import Automator
from src.agents.scanner import Scanner
from src.agents.trader import Trader
from src.config import ArcanaConfig, get_config
from src.utils.db import create_supabase_client, get_daily_stats, log_action
from src.utils.llm import LLMClient, ModelTier
from src.utils.memory import MemorySystem
from src.utils.notify import AlertLevel, Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/arcana.log", mode="a"),
    ],
)
logger = logging.getLogger("arcana.orchestrator")

STOP_FILE = Path(__file__).resolve().parent.parent / "STOP"


class Action(BaseModel):
    """A candidate action from a sub-agent."""
    agent: str
    name: str
    description: str
    expected_revenue: float = 0.0
    probability: float = 0.5
    time_hours: float = 0.25
    risk: float = 1.0
    priority_score: float = 0.0
    params: dict[str, Any] = {}

    def calculate_priority(self) -> float:
        """Priority = (Revenue × Probability) / (Time × Risk)"""
        denominator = max(self.time_hours, 0.01) * max(self.risk, 0.1)
        self.priority_score = (self.expected_revenue * self.probability) / denominator
        return self.priority_score


class OrchestratorState(TypedDict):
    """State flowing through the LangGraph decision loop."""
    actions: list[dict[str, Any]]
    selected_action: dict[str, Any] | None
    execution_result: dict[str, Any] | None
    cycle_count: int
    error: str | None


class Orchestrator:
    """The autonomous brain of ARCANA AI."""

    def __init__(self) -> None:
        self.config: ArcanaConfig | None = None
        self.llm: LLMClient | None = None
        self.memory: MemorySystem | None = None
        self.notifier: Notifier | None = None
        self.db = None
        self.agents: dict[str, Any] = {}
        self._running = True
        self._graph = None

    async def initialize(self) -> None:
        """Initialize all components and sub-agents."""
        self.config = get_config()
        self.llm = LLMClient(self.config)
        self.db = await create_supabase_client(self.config)
        self.memory = MemorySystem(self.db, self.llm)
        self.notifier = Notifier(self.config)

        # Initialize sub-agents
        self.agents = {
            "scanner": Scanner(self.config, self.llm, self.db, self.memory),
            "trader": Trader(self.config, self.llm, self.db, self.memory, self.notifier),
            "communicator": Communicator(self.config, self.llm, self.db, self.memory),
            "creator": Creator(self.config, self.llm, self.db, self.memory),
            "automator": Automator(self.config, self.llm, self.db, self.memory),
        }

        # Build the LangGraph
        self._graph = self._build_graph()

        await self.notifier.send("ARCANA AI initialized. Beginning operations.", AlertLevel.INFO)
        logger.info("Orchestrator initialized with %d agents", len(self.agents))

    def _build_graph(self) -> Any:
        """Build the LangGraph decision loop."""
        graph = StateGraph(OrchestratorState)

        graph.add_node("scan", self._scan_node)
        graph.add_node("evaluate", self._evaluate_node)
        graph.add_node("prioritize", self._prioritize_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("learn", self._learn_node)

        graph.set_entry_point("scan")
        graph.add_edge("scan", "evaluate")
        graph.add_edge("evaluate", "prioritize")
        graph.add_edge("prioritize", "execute")
        graph.add_edge("execute", "learn")
        graph.add_edge("learn", END)

        return graph.compile()

    async def _scan_node(self, state: OrchestratorState) -> OrchestratorState:
        """SCAN: Query all sub-agents for available actions."""
        logger.info("SCAN — Gathering available actions from all agents")
        all_actions: list[dict[str, Any]] = []

        for name, agent in self.agents.items():
            try:
                actions = await agent.get_available_actions()
                all_actions.extend([a.model_dump() for a in actions])
                logger.info("  %s: %d actions available", name, len(actions))
            except Exception as exc:
                logger.error("  %s: failed to get actions: %s", name, exc)
                await self.notifier.error_alert(name, "get_available_actions", str(exc))

        state["actions"] = all_actions
        return state

    async def _evaluate_node(self, state: OrchestratorState) -> OrchestratorState:
        """EVALUATE: Enrich actions with memory context."""
        logger.info("EVALUATE — Enriching %d actions with memory", len(state["actions"]))

        for action_data in state["actions"]:
            action = Action(**action_data)
            try:
                context = await self.memory.recall_context(
                    f"{action.agent}: {action.name} - {action.description}"
                )
                # Adjust probability based on past experience
                if "No relevant memories" not in context:
                    prompt = (
                        f"Based on this past experience:\n{context}\n\n"
                        f"What is the adjusted probability of success (0.0-1.0) for this action?\n"
                        f"Action: {action.description}\n"
                        f"Current probability estimate: {action.probability}\n"
                        f"Respond with ONLY a number."
                    )
                    raw = await self.llm.complete(prompt, tier=ModelTier.HAIKU, temperature=0.1, max_tokens=10)
                    try:
                        action.probability = max(0.01, min(1.0, float(raw.strip())))
                    except ValueError:
                        pass
                action_data.update(action.model_dump())
            except Exception as exc:
                logger.warning("Failed to evaluate action %s: %s", action.name, exc)

        return state

    async def _prioritize_node(self, state: OrchestratorState) -> OrchestratorState:
        """PRIORITIZE: Score and rank all actions."""
        logger.info("PRIORITIZE — Scoring actions")

        scored_actions = []
        for action_data in state["actions"]:
            action = Action(**action_data)
            action.calculate_priority()
            scored_actions.append(action.model_dump())
            logger.info(
                "  [%.1f] %s.%s — $%.0f × %.0f%% / (%.2fh × %.1f risk)",
                action.priority_score,
                action.agent,
                action.name,
                action.expected_revenue,
                action.probability * 100,
                action.time_hours,
                action.risk,
            )

        scored_actions.sort(key=lambda a: a["priority_score"], reverse=True)
        state["actions"] = scored_actions

        if scored_actions:
            state["selected_action"] = scored_actions[0]
            logger.info(
                "Selected: %s.%s (priority: %.1f)",
                scored_actions[0]["agent"],
                scored_actions[0]["name"],
                scored_actions[0]["priority_score"],
            )
        else:
            state["selected_action"] = None
            logger.info("No actions available this cycle")

        return state

    async def _execute_node(self, state: OrchestratorState) -> OrchestratorState:
        """EXECUTE: Run the highest-priority action."""
        selected = state.get("selected_action")
        if not selected:
            state["execution_result"] = {"status": "idle", "message": "No actions to execute"}
            return state

        action = Action(**selected)
        agent = self.agents.get(action.agent)
        if not agent:
            state["execution_result"] = {"status": "error", "message": f"Unknown agent: {action.agent}"}
            return state

        logger.info("EXECUTE — Running %s.%s", action.agent, action.name)

        try:
            result = await agent.execute_action(action)
            state["execution_result"] = {
                "status": "success",
                "action": action.name,
                "agent": action.agent,
                "result": result,
            }
            await log_action(
                self.db,
                action.agent,
                action.name,
                details=result,
                revenue_usd=result.get("revenue_usd", 0),
                cost_usd=result.get("cost_usd", 0),
            )
        except Exception as exc:
            logger.error("Execution failed: %s", exc)
            state["execution_result"] = {"status": "error", "message": str(exc)}
            await log_action(
                self.db, action.agent, action.name, status="error", error=str(exc)
            )
            await self.notifier.error_alert(action.agent, action.name, str(exc))

        return state

    async def _learn_node(self, state: OrchestratorState) -> OrchestratorState:
        """LEARN: Store the outcome in memory for future recall."""
        result = state.get("execution_result", {})
        selected = state.get("selected_action")

        if not selected or result.get("status") == "idle":
            return state

        action = Action(**selected)
        outcome_text = (
            f"Action: {action.agent}.{action.name}\n"
            f"Description: {action.description}\n"
            f"Expected revenue: ${action.expected_revenue:.2f}\n"
            f"Result: {result.get('status')}\n"
            f"Details: {result.get('result', result.get('message', 'N/A'))}"
        )

        importance = await self.memory.calculate_importance(
            outcome_text,
            predicted_outcome=f"Expected ${action.expected_revenue:.2f} with {action.probability:.0%} probability",
        )

        category = "strategy_adjustment"
        if action.agent == "trader":
            category = "trade_outcome"
        elif action.agent == "communicator":
            category = "content_performance"
        elif action.agent == "scanner":
            category = "market_pattern"

        await self.memory.store(
            outcome_text,
            category=category,
            importance_score=importance,
            metadata={"action": action.model_dump(), "result": result},
        )

        logger.info("LEARN — Stored outcome (importance: %.2f, category: %s)", importance, category)
        return state

    def _check_kill_switch(self) -> bool:
        """Check if the STOP file exists."""
        if STOP_FILE.exists():
            logger.warning("KILL SWITCH ACTIVE — STOP file detected. Halting all operations.")
            return True
        return False

    async def run_cycle(self) -> None:
        """Run one complete decision cycle."""
        if self._check_kill_switch():
            return

        logger.info("=" * 60)
        logger.info("CYCLE START — %s", datetime.now(timezone.utc).isoformat())
        logger.info("=" * 60)

        initial_state: OrchestratorState = {
            "actions": [],
            "selected_action": None,
            "execution_result": None,
            "cycle_count": 0,
            "error": None,
        }

        try:
            result = await self._graph.ainvoke(initial_state)
            logger.info("Cycle complete. Result: %s", result.get("execution_result", {}).get("status", "unknown"))
        except Exception as exc:
            logger.error("Cycle failed: %s", exc)
            await self.notifier.error_alert("orchestrator", "run_cycle", str(exc))

        # Heartbeat
        try:
            await log_action(self.db, "orchestrator", "heartbeat")
        except Exception:
            pass

    async def run_daily_summary(self) -> None:
        """Send the daily summary to Ian & Tan."""
        try:
            stats = await get_daily_stats(self.db)
            await self.notifier.daily_summary(
                total_revenue=stats["total_revenue"],
                total_cost=stats["total_cost"],
                trades=stats["trades"],
                posts=stats["posts"],
                leads=stats["leads"],
            )
        except Exception as exc:
            logger.error("Failed to send daily summary: %s", exc)

    async def run_forever(self) -> None:
        """Main loop — run cycles every N minutes."""
        await self.initialize()
        interval = self.config.orchestrator_interval_minutes * 60
        last_summary = datetime.now(timezone.utc).date()

        logger.info("Starting main loop — interval: %d minutes", self.config.orchestrator_interval_minutes)

        while self._running:
            if self._check_kill_switch():
                logger.info("Kill switch active. Sleeping 60s before recheck...")
                await asyncio.sleep(60)
                continue

            await self.run_cycle()

            # Daily summary at end of day
            now = datetime.now(timezone.utc)
            if now.date() > last_summary and now.hour >= 23:
                await self.run_daily_summary()
                last_summary = now.date()

            # Sleep with jitter (anti-bot detection)
            import random
            jitter = random.randint(0, 60)
            await asyncio.sleep(interval + jitter)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down ARCANA AI...")
        self._running = False
        if self.llm:
            await self.llm.close()
        if self.notifier:
            await self.notifier.send("ARCANA AI shutting down.", AlertLevel.INFO)
            await self.notifier.close()


def main() -> None:
    """Entry point."""
    os.makedirs("logs", exist_ok=True)

    orchestrator = Orchestrator()

    def handle_signal(signum, frame):
        asyncio.get_event_loop().create_task(orchestrator.shutdown())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(orchestrator.run_forever())


if __name__ == "__main__":
    main()
