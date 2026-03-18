# 120-Day Launch Plan — ARCANA AI

## Pre-Launch: Human Prerequisites (Days -7 to 0)
Ian and Tan must complete these BEFORE Claude Code touches anything:
- [ ] Create @ArcanaAI_ X account, subscribe to X Premium ($8/mo)
- [ ] Apply for X API Basic tier ($200/mo), get API keys
- [ ] Create Supabase project, run 001_initial_schema.sql
- [ ] Create OpenRouter account, fund with $20
- [ ] Create burner Solana wallet (Phantom), fund with trading capital
- [ ] Create Polygon wallet for Polymarket, fund with $300 USDC.e
- [ ] Get Helius RPC API key (free tier)
- [ ] Get Birdeye API key (free tier)
- [ ] Get Finnhub API key (free tier)
- [ ] Set up Discord server + webhook for notifications
- [ ] Fill in ALL keys in config/.env
- [ ] Set DRY_RUN=true

## Phase 1: Foundation Build (Days 1-14)

### Week 1: Core Infrastructure
- Day 1-2: Claude Code builds src/utils/llm.py (OpenRouter client + SOUL.md injection)
- Day 2-3: Claude Code builds src/utils/memory.py (pgvector embed/store/recall)
- Day 3-4: Claude Code builds src/utils/notify.py (Discord + Telegram alerts)
- Day 4-5: Claude Code builds src/orchestrator.py (LangGraph decision loop)
- Day 5-7: Test all utilities, fix bugs, validate Supabase connection

### Week 2: Content Engine
- Day 8-9: Claude Code builds src/agents/communicator.py (X API posting)
- Day 10: Implement Morning Briefing template + scheduling
- Day 11: Implement Trade Receipt template
- Day 12: Test posting to X (manual trigger, review output)
- Day 13-14: Ian & Tan post origin story: "We hired an AI. Gave it $1,000. Let's see what happens."

**Milestone: First ARCANA AI tweet posted.**

## Phase 2: Content Machine (Days 15-30)

### Week 3: Autonomous Posting
- Day 15-17: Implement full posting schedule (Morning Briefing 7 AM PT, 3-5 tweets/day)
- Day 18-19: Implement mention monitoring + reply logic
- Day 20-21: Implement self-reply strategy (150x algorithm hack)
- Day 21: Ian QT of first trade receipt: "Our AI made its first analysis. It's... interesting."

### Week 4: Signal Pipeline
- Day 22-23: Claude Code builds src/agents/scanner.py
- Day 24-25: Connect DexScreener, Birdeye, Finnhub feeds
- Day 26-27: Implement signal scoring + anomaly detection
- Day 28-30: Begin paper trading (DRY_RUN=true), post simulated trade receipts

**Milestone: Daily content flowing autonomously. Paper trades running.**

## Phase 3: Live Trading (Days 31-60)

### Week 5-6: Validate Paper Trades
- Day 31-42: 2 weeks of paper trading, log every signal + outcome
- Review: Win rate, signal quality, false positive rate
- Adjust: Tighten or loosen signal thresholds based on data
- Required: >50% win rate on paper before going live

### Week 7: Go Live
- Day 43: Set DRY_RUN=false (start with 25% of trading capital only)
- Day 44-49: First live trades with reduced position sizes (2.5% max instead of 5%)
- Day 49: Post first real trade receipt with actual P&L
- Day 50: First Weekly Postmortem thread (win or lose, both narratives work)

### Week 8: Scale Up
- Day 51-56: If >50% win rate, increase to full position sizes (5%)
- Day 56-60: Activate all trading strategies (Solana + Polymarket)

**Milestone: Live trading with real P&L posted publicly.**

## Phase 4: Revenue Diversification (Days 61-90)

### Week 9-10: Digital Products
- Day 61-63: Create "The Arcana Playbook" PDF on Gumroad ($29-49)
- Day 64-66: Create prompt library product ($19-29)
- Day 67-70: Launch Beehiiv newsletter (free tier), import X audience

### Week 11-12: Service Revenue
- Day 71-73: Build UGC video production pipeline (HeyGen API)
- Day 74-76: First UGC demo videos, post to X as proof of capability
- Day 77-80: Begin lead qualification pipeline for Arcana Operations
- Day 80-84: First consulting lead routed to Ian/Tan via Discord
- Day 84-90: Launch Premium Discord tier ($29-49/mo)

**Milestone: 3+ active revenue streams. First consulting lead.**

## Phase 5: Scale & Compound (Days 91-120)

### Week 13-14: Programmatic Growth
- Day 91-95: Launch programmatic SEO site (auto-generated articles)
- Day 96-100: Begin AI podcast production (ElevenLabs)
- Day 100: Week 4 challenge — challenge crypto influencer to AI vs Human trading competition

### Week 15-16: Full Autonomous Operation
- Day 101-110: All 5 agents running autonomously 24/7
- Day 111-115: First micro-SaaS tool built and deployed
- Day 116-120: Month 4 review — all-time P&L report, strategy adjustments

**Milestone: Full autonomous operation. 5+ revenue streams. 2,000+ X followers.**

## Success Metrics by Day 120
| Metric | Target | Stretch |
|--------|--------|---------|
| X Followers | 2,000 | 5,000 |
| Newsletter Subscribers | 500 | 1,500 |
| Monthly Revenue | $2,000 | $5,000 |
| Trading Win Rate | 55% | 65% |
| Active Revenue Streams | 5 | 8 |
| Consulting Leads Routed | 5 | 15 |
| Consulting Deals Closed | 1 | 3 |
| Digital Products Sold | 20 | 100 |
| UGC Videos Produced | 10 | 50 |
