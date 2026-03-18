"""Example: LangGraph Orchestrator Pattern

Reference pattern for src/orchestrator.py.
The autonomous brain that runs every 15 minutes.
"""
import asyncio
import os
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel


class Action(BaseModel):
    """An action any agent can propose."""
    agent: str          # scanner, trader, communicator, creator, automator
    action_type: str    # e.g., "trade", "post_tweet", "generate_ugc", "respond_dm"
    description: str
    revenue_estimate: float   # Expected $ value
    probability: float        # 0-1
    time_hours: float         # Hours to execute
    risk_score: float         # 1 = no risk, 10 = high risk
    data: dict = {}           # Action-specific payload


def priority_score(action: Action) -> float:
    """Priority = (Revenue × Probability) / (Time × Risk)
    
    Higher score = execute first.
    Consulting leads ALWAYS score highest.
    """
    if action.risk_score == 0:
        action.risk_score = 1  # Prevent division by zero
    if action.time_hours == 0:
        action.time_hours = 0.01
    
    score = (action.revenue_estimate * action.probability) / (action.time_hours * action.risk_score)
    
    # Consulting lead bonus: 10x multiplier
    if action.action_type in ("respond_dm", "qualify_lead"):
        score *= 10
    
    return score


def check_kill_switch() -> bool:
    """Check if STOP file exists. If so, halt everything."""
    return Path("STOP").exists()


async def orchestrator_loop():
    """Main loop. Runs every ORCHESTRATOR_INTERVAL_MINUTES."""
    interval = int(os.getenv("ORCHESTRATOR_INTERVAL_MINUTES", "15"))
    
    while True:
        # 1. Kill switch check
        if check_kill_switch():
            print("🛑 KILL SWITCH ACTIVE — All operations halted.")
            # Notify Ian/Tan
            # await notify.alert("Kill switch activated. All operations halted.")
            await asyncio.sleep(60)
            continue
        
        try:
            # 2. Collect proposed actions from all agents
            actions: list[Action] = []
            
            # Scanner proposes trading opportunities
            # scanner_actions = await scanner.get_opportunities()
            # actions.extend(scanner_actions)
            
            # Communicator proposes responses to mentions/DMs
            # comm_actions = await communicator.get_pending_responses()
            # actions.extend(comm_actions)
            
            # Creator proposes scheduled content
            # creator_actions = await creator.get_scheduled_content()
            # actions.extend(creator_actions)
            
            # 3. Score all actions
            scored = sorted(actions, key=priority_score, reverse=True)
            
            # 4. Execute highest priority action
            if scored:
                top_action = scored[0]
                score = priority_score(top_action)
                
                print(f"🎯 Executing: {top_action.description} (score: {score:.1f})")
                
                # Route to appropriate agent
                # if top_action.agent == "trader":
                #     result = await trader.execute(top_action)
                # elif top_action.agent == "communicator":
                #     result = await communicator.execute(top_action)
                # ...
                
                # 5. Log to Supabase
                # await log_action(top_action, result)
                
                # 6. Learn from outcome
                # await memory.learn_from_outcome(
                #     prediction=top_action.description,
                #     actual_outcome=str(result),
                #     context={"agent": top_action.agent, "type": top_action.action_type}
                # )
            
            # 7. Health heartbeat
            # await supabase.table("agent_log").insert({
            #     "agent": "orchestrator",
            #     "action": "heartbeat",
            #     "details": {"actions_evaluated": len(actions), "top_score": score if actions else 0},
            # }).execute()
        
        except Exception as e:
            print(f"❌ Orchestrator error: {e}")
            # await notify.alert(f"Orchestrator error: {e}")
        
        # Wait for next cycle
        await asyncio.sleep(interval * 60)


if __name__ == "__main__":
    asyncio.run(orchestrator_loop())
