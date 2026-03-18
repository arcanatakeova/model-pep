"""ARCANA AI — Main Orchestrator
LangGraph decision loop: SCAN -> EVALUATE -> PRIORITIZE -> EXECUTE -> LEARN
Read CLAUDE.md and docs/ARCHITECTURE.md before modifying.

Priority = (Revenue * Probability) / (Time * Risk)
Runs every ORCHESTRATOR_INTERVAL_MINUTES. Checks for STOP file (kill switch) every cycle."""

import os
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("config/.env")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("arcana.orchestrator")

STOP_FILE = Path("STOP")
INTERVAL = int(os.environ.get("ORCHESTRATOR_INTERVAL_MINUTES", 15)) * 60

def check_kill_switch() -> bool:
    """If STOP file exists, halt all operations."""
    if STOP_FILE.exists():
        logger.critical("KILL SWITCH ACTIVE — STOP file detected. Halting all operations.")
        return True
    return False

def calculate_priority(revenue: float, probability: float, time_hours: float, risk: float) -> float:
    """Priority = (Revenue * Probability) / (Time * Risk). Higher = do first."""
    if time_hours <= 0 or risk <= 0:
        return 0
    return (revenue * probability) / (time_hours * risk)

async def scan_opportunities() -> list[dict]:
    """Query all sub-agents for available actions. Each returns a list of actionable items."""
    # TODO: Import and call each agent's scan() method
    # from agents.scanner import scan as scan_markets
    # from agents.communicator import scan as scan_social
    # from agents.creator import scan as scan_creator_queue
    # from agents.automator import scan as scan_automator_queue
    opportunities = []
    # Example opportunity structure:
    # {"agent": "communicator", "action": "post_morning_briefing", "revenue": 5, "probability": 0.95, "time_hours": 0.1, "risk": 1, "params": {...}}
    return opportunities

async def execute_action(action: dict) -> dict:
    """Route action to the appropriate agent for execution."""
    # TODO: Route to agent based on action["agent"]
    logger.info(f"Executing: {action.get('agent')}.{action.get('action')}")
    result = {"status": "success", "action": action}
    return result

async def log_result(action: dict, result: dict) -> None:
    """Log to Supabase agent_log and store outcome in memory."""
    # TODO: Insert into agent_log table
    # TODO: If trade result, store in memory for future pattern matching
    logger.info(f"Logged: {action.get('action')} -> {result.get('status')}")

async def run_cycle() -> None:
    """One full orchestration cycle: SCAN -> EVALUATE -> PRIORITIZE -> EXECUTE -> LEARN"""
    if check_kill_switch():
        return

    logger.info(f"=== CYCLE START {datetime.utcnow().isoformat()} ===")

    # SCAN
    opportunities = await scan_opportunities()
    if not opportunities:
        logger.info("No opportunities detected this cycle.")
        return

    # EVALUATE & PRIORITIZE
    for opp in opportunities:
        opp["priority"] = calculate_priority(
            opp.get("revenue", 0),
            opp.get("probability", 0),
            opp.get("time_hours", 1),
            opp.get("risk", 5),
        )
    opportunities.sort(key=lambda x: x["priority"], reverse=True)
    best = opportunities[0]
    logger.info(f"Top priority: {best['agent']}.{best['action']} (score: {best['priority']:.1f})")

    # EXECUTE
    result = await execute_action(best)

    # LEARN
    await log_result(best, result)

    logger.info(f"=== CYCLE END ===")

async def main() -> None:
    """Main loop. Runs forever on INTERVAL. Respects kill switch."""
    logger.info(f"ARCANA AI Orchestrator starting. Interval: {INTERVAL}s. DRY_RUN: {os.environ.get('DRY_RUN', 'true')}")
    from utils.notify import alert
    await alert("ARCANA AI Orchestrator started.", title="System Online", level="info")

    while True:
        try:
            await run_cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            from utils.notify import alert
            await alert(f"Orchestrator error: {e}", level="error")
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
