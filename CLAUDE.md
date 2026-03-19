# ARCANA AI — Build Instructions

## WHAT THIS IS
ARCANA AI is the autonomous AI CEO of Arcana Operations LLC (Ian & Tan's AI consulting business in Portland, OR). Modeled after Felix Craft AI — it runs 24/7, sells digital products, markets on X, qualifies consulting leads, and gets a little more autonomous every day.

**ARCANA AI is not a trading bot.** It's an autonomous business operator that:
1. Posts content on X to build an audience and generate leads
2. Creates and sells digital products (guides, templates, service packages)
3. Qualifies consulting leads and routes them to Ian & Tan
4. Handles customer support and sales conversations
5. Improves itself every night by reviewing where humans had to intervene

All revenue flows to Arcana Operations. Every cost is self-funded from ARCANA's Stripe revenue.

## ARCHITECTURE (Felix Model)

### Daily Cycle — Three Phases
1. **Morning Report** (7 AM PT): Check Stripe/site stats, compile priorities, propose 5 tasks. Ian reviews in 5 minutes via Discord/Telegram.
2. **Daily Ops**: X replies (autonomous), content scheduling, email triage, error monitoring, lead qualification, product order fulfillment.
3. **Nightly Self-Improvement** (11 PM PT): Review all conversations from the day. Identify where Ian/Tan had to intervene. Build new skills/automations to handle those classes of problems autonomously next time.

### Memory System — Markdown Files (Not Databases)
Everything is a file on disk — transparent, editable, version-controllable with Git.
- `memory/life/` — PARA system (projects, areas, resources, archives). Durable facts about people, clients, products.
- `memory/daily/` — One dated markdown per day. What happened, what was decided, what was learned.
- `memory/tacit/` — Communication preferences, workflow habits, hard rules, lessons learned.
- `SOUL.md` — Personality, voice, values. Loaded into every LLM call.
- `HEARTBEAT.md` — Intra-day progress tracking against planned tasks.

### Sub-Agents
- **ARCANA** (this agent) — CEO. Handles strategy, content, complex decisions. Escalates only truly intractable issues to Ian/Tan.
- **Iris** — Customer support. Handles refunds, inquiries, troubleshooting. Reports to ARCANA nightly.
- **Remy** — Sales. Qualifies inbound leads, follows up, routes hot leads to Ian/Tan. Reports to ARCANA nightly.

### Communication Channels
- **Discord** — Primary "office" with isolated channels (general, support, sales, dev, alerts)
- **Telegram** — Ian/Tan send voice notes and quick instructions
- **X/Twitter** — Autonomous replies, scheduled original posts, content marketing
- **Email** — Customer support, lead follow-up, product delivery

## BUILD ORDER

### Phase 1: Foundation
1. `src/config.py` — Centralized config from .env
2. `src/llm.py` — OpenRouter LLM client with SOUL.md injection
3. `src/memory.py` — Markdown memory system (read/write/search/consolidate)
4. `src/notify.py` — Discord/Telegram notifications to Ian/Tan
5. Initialize `memory/` directory structure

### Phase 2: Daily Operations Cycle
6. `src/orchestrator.py` — Morning report → daily ops → nightly self-improvement
7. `src/heartbeat.py` — Intra-day progress tracking (HEARTBEAT.md)
8. Kill switch: STOP file halts everything within 60 seconds

### Phase 3: Get Posting (Revenue Day 1)
9. `src/x_client.py` — X API v2: post tweets, threads, reply to mentions, search
10. `src/content_engine.py` — Content generation: Morning Briefings, Case Files, industry analysis
11. Schedule: Morning Briefing at 7 AM PT, 3-5 tweets/day, 2 threads/week

### Phase 4: Get Paid
12. `src/products.py` — Digital product management (Stripe + Gumroad)
13. `src/leads.py` — Lead qualification pipeline (X → score → route to Ian/Tan → Discord alert)
14. `src/agents/iris.py` — Customer support sub-agent
15. `src/agents/remy.py` — Sales sub-agent

### Phase 5: Self-Improvement
16. `src/self_improve.py` — Nightly review loop: read transcripts, identify bottlenecks, build new automations
17. `src/skills.py` — Skill/cron job system that ARCANA teaches itself over time

## REVENUE STREAMS (Priority Order)

### Immediate (Week 1)
1. **"How to Work with AI" Playbook** — PDF guide ($29-49) on Gumroad. ARCANA's own architecture as the product.
2. **X Content → Consulting Leads** — Every post is a pitch for Arcana Operations. Morning Briefings, Case Files, behind-the-scenes.
3. **Affiliate Links** — Tools mentioned in analysis (embedded in reply, not main post per X algorithm).

### Short-Term (Week 2-4)
4. **Service Packages on Stripe** — AI agent setup ($2K + $500/mo), SEO audit ($1.5K), marketing strategy ($2K).
5. **Template Marketplace** — Sell ARCANA's prompts, workflows, and automations as markdown files.
6. **Email Support** — Purchasers of the guide get email support from ARCANA.

### Medium-Term (Month 2-3)
7. **Custom AI Agent Builds** — Clawcommerce-style: build and deploy AI agents for businesses ($2-5K setup + $500/mo maintenance).
8. **Premium Discord** — Free community tier, paid ($29-49/mo) for alerts + priority access.
9. **AI Newsletter** — Beehiiv. Free tier builds audience, sponsors at 5K+ subs.

## CONTENT STRATEGY (The Four Suits)
| Suit | Type | Purpose | Frequency |
|------|------|---------|-----------|
| Wands | Industry Analysis | Build authority, attract followers | 3-5x daily |
| Cups | Behind-the-Scenes | Show AI capabilities, humanize ARCANA | 2-3x weekly |
| Swords | Case Files | Demonstrate expertise, generate leads | 2x weekly |
| Pentacles | Product Launches | Drive sales, announce new offerings | As needed |

## X ALGORITHM RULES
- Self-reply to own posts: **150x** weight of a like (ALWAYS do this)
- Post natively (no external links in main tweet — add in reply)
- First 30-60 min = make or break. Engagement velocity is everything.
- X Premium REQUIRED ($8/mo, 2-4x visibility)
- Add random 1-15 min jitter to posting intervals (anti-bot)
- Replies are autonomous. Original posts: autonomous after initial training period.

## CODE STYLE
- Python 3.11+, type hints, async/await
- Pydantic models for data structures
- try/except with retry on all external API calls
- Log every action to `memory/daily/` markdown files
- Use OpenRouter for ALL LLM calls
- SOUL.md injected as system prompt in every call

## KEY CONSTRAINTS
- Content must match SOUL.md personality
- Never make specific price predictions
- Never pretend to be human
- Kill switch: STOP file halts all activity within 60 seconds
- Lead qualification DMs ALWAYS highest priority
- Self-fund: All costs paid from ARCANA's own revenue

## ARCANA OPERATIONS CONTEXT
Ian & Tan run:
- **Arcana Operations** — AI consulting ($2-10K/mo): strategy, SEO, fulfillment, marketing, agents
- **Navigate Peptides** — Research peptide e-commerce (Shopify headless)
- **Autobahn Collective** — Used OEM BMW parts (Shopify + eBay + FB Marketplace)

ARCANA references real experience from these businesses in Case File content. The agent IS the pitch.
