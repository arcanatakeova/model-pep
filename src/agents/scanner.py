"""ARCANA AI — Opportunity Scanner Agent
Read docs/TRADING.md and docs/API_REFERENCE.md before implementing.

Responsibilities:
- Run every 15 minutes (triggered by orchestrator)
- Aggregate signals from: DexScreener, Birdeye, Unusual Whales, Finnhub, Rugcheck
- Store raw signals in Supabase `signals` table
- Detect anomalies (volume spikes, whale movements, unusual options flow)
- When anomaly detected: escalate to Sonnet for deeper analysis
- Output: list of scored opportunities for the Trader agent

Key patterns to detect:
- Volume spike >200% above 1h average on DexScreener
- Whale wallet accumulation >$500K in 4h on Birdeye
- Unusual options flow >3x normal volume on UW
- Congress trade filings on UW (front-run potential)
- Positive sentiment shift on Finnhub crypto news
- Polymarket probability swings >10% in 24h

Implementation:
- Use httpx async client for all API calls
- Haiku ($0.001/call) for routine pattern matching
- Sonnet ($0.015/call) for complex signal interpretation
- Store every signal in Supabase with confidence score
- Use memory.recall() to check: "Have I seen this pattern before? What happened?"
"""
# TODO: Implement scanner agent
