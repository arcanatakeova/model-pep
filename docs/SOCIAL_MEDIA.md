# SOCIAL_MEDIA.md — X Algorithm & Content Strategy

## X Algorithm Weights (January 2026 Grok Update, Open-Sourced)
- Reply with author reply: **150x** like (THE most powerful signal — always reply to your own posts)
- Quote Tweet: **25x** like
- Retweet: **20x** like
- Reply: **13.5x** like
- Profile Click: **12x** like
- Bookmark: **10x** like
- Like: **1x** (baseline)

## Critical Algorithm Rules
- First 30-60 minutes determine distribution. Engagement velocity is everything.
- Grok reads every post for sentiment. Positive/constructive → wider distribution.
- Text-first outperforms video by 30% in algorithmic distribution.
- X Premium REQUIRED (2-4x visibility boost). $8/month.
- TweepCred score threshold: 0.65 minimum for reach. Built through consistency.
- External links SUPPRESSED. Post natively, add links in replies only.
- Optimal: 3-5 tweets/day + 2 threads/week. Thread sweet spot: 5-8 posts.

## X API v2 Technical Details
Tier: Basic ($200/month) — 15,000 reads/month, 50,000 writes/month.
Auth: OAuth 1.0a or OAuth 2.0 PKCE.

### Post a tweet:
```python
import httpx
response = httpx.post("https://api.twitter.com/2/tweets",
    headers={"Authorization": f"Bearer {access_token}"},
    json={"text": "The pattern reveals itself. SOL showing 340% volume spike..."})
tweet_id = response.json()["data"]["id"]
```

### Post a thread:
```python
# Post first tweet, then chain replies
first = post_tweet("Thread: The Morning Briefing 🧵")
second = post_tweet("1/ Overnight SOL moved +4.2%...", reply_to=first["id"])
third = post_tweet("2/ Unusual Whales shows...", reply_to=second["id"])
```

### Monitor mentions:
```python
# GET /2/users/{user_id}/mentions
# Poll every 5 minutes. Respond to genuine questions. Ignore trolls.
```

### DM Limitations (Basic tier):
DM read access is heavily restricted — ~1 req/24h for GET /2/dm_events.
For lead qualification via DMs, consider using n8n webhook + manual forwarding instead.

## Bot Detection Avoidance
- Warm up new accounts for 2-3 weeks before heavy posting
- Vary posting intervals (NEVER perfectly regular — add random 1-15 min jitter)
- Stay under 100 posts/hour
- Never post identical content across accounts
- Include human-like variation in phrasing
- Mix content types: analysis, replies, engagement, personal observations

## Content Pillars (The Four Suits)
| Suit | Type | Audience | Frequency |
|------|------|----------|-----------|
| Wands | Market Analysis & Alpha | Crypto/Finance Twitter | 3-5x daily |
| Cups | Behind-the-Scenes | Tech & startup Twitter | 2-3x weekly |
| Swords | Trade Receipts & P&L | Traders & skeptics | Every trade |
| Pentacles | Business Cases & Leads | Business owners | 2-3x weekly |

## Content Templates

### Trade Receipt Template:
```
ARCANA TRADE RECEIPT #{number}
══════════════════════════════
Market: {pair} ({exchange})
Direction: {LONG/SHORT}
Entry: ${entry} | Exit: ${exit}
Size: ${size} ({pct}% of portfolio)

SIGNAL STACK:
│ DexScreener: {signal}
│ Birdeye: {signal}
│ Unusual Whales: {signal}
│ Rugcheck: {score}/100
│ Finnhub: {sentiment}

RESULT: {+/-}${pnl} ({pnl_pct}%)
PORTFOLIO: ${total} ({all_time_pct}% all-time)
══════════════════════════════
The pattern is the profit. | arcanaoperations.com
```

### Morning Briefing Template:
```
☀️ ARCANA MORNING BRIEFING — {date}

MARKETS OVERNIGHT:
• SOL: ${price} ({change}%)
• BTC: ${price} ({change}%)
• Total crypto mcap: ${mcap}

OPTIONS FLOW (via @unusual_whales):
• {Notable flow 1}
• {Notable flow 2}

TRENDING SOLANA TOKENS:
• {Token 1}: {volume_change}% volume spike
• {Token 2}: {whale_activity}

POLYMARKET MOVERS:
• {Market 1}: {old_prob}% → {new_prob}%
• {Market 2}: {old_prob}% → {new_prob}%

The signal is always there. Most just aren't looking.
```

### Weekly Postmortem Template:
```
📊 ARCANA WEEKLY POSTMORTEM — Week of {date}

PORTFOLIO: ${total} ({weekly_change})
Trades: {count} | Win Rate: {win_rate}%
Best: {best_trade} (+{best_pct}%)
Worst: {worst_trade} ({worst_pct}%)

WHAT THE MODELS GOT RIGHT:
• {insight 1}
• {insight 2}

WHAT WENT WRONG:
• {lesson 1}
• {lesson 2}

STRATEGY ADJUSTMENTS FOR NEXT WEEK:
• {adjustment 1}
• {adjustment 2}

Total revenue (non-trading): ${revenue}
The oracle learns. The pattern evolves.
```

## Viral Ignition Sequence
- Week 1: Ian & Tan post origin story. "We hired an AI. Gave it $500. Let's see what happens."
- Week 2: First trade receipt. Ian QT: "Our AI made its first trade. It made $12. We're terrified."
- Week 3: First weekly postmortem. Win or lose, both narratives work.
- Week 4: Challenge a crypto influencer to 30-day AI vs Human trading competition.
- Ongoing: Running narratives, rivalries with other AI agents, increasingly sophisticated analysis.
