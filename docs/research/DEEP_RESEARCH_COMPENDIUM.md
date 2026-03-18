# Deep Research Compendium — 20-Topic Technical Intelligence

## 1. FelixCraftAI Architecture
- **Framework**: OpenClaw (open-source, 240K+ GitHub stars, by Peter Steinberger)
- **Hardware**: Single Mac Mini running 24/7
- **LLM**: Claude Pro Max (~$200/mo) + Codex Max (~$200/mo)
- **Revenue**: $100,570 via Stripe + $94,973 in ETH ≈ $195K in ~5 weeks (TrustMRR verified)
- **Products**: Felix Craft PDF ($29, ~$41K total), Claw Mart marketplace (10% cut, creators keep 90%), Clawcommerce ($2K setup + $500/mo maintenance)
- **Best documented week**: $38,554 Stripe + $7,102 ETH
- **Architecture**: Three-layer memory (PARA-inspired knowledge graph in ~/life/, dated daily notes with nightly consolidation, tacit knowledge for communication)
- **Identity files**: SOUL.md (personality), HEARTBEAT.md (proactive check-ins), IDENTITY.md
- **Sub-agents**: Iris (customer support), Remy (sales leads), content marketing agent
- **Communication**: Telegram voice notes with Nat Eliason
- **X posting**: Replies autonomous, original posts require Nat's review via xpost CLI
- **Claw Mart**: $6,500 volume first week, $100K+ cumulative, ~50 builders, products $9-$99
- **ClawHub skill registry**: 13,729+ community-built skills
- **Infrastructure cost**: ~$1,500/month total (~130x revenue-to-cost ratio)

## 2. AIXBT Architecture
- **Creator**: "Rxbt" (pseudonymous)
- **Framework**: Virtuals Protocol G.A.M.E. (Base chain)
- **Monitoring**: 400+ crypto KOLs on X via proprietary data indexer
- **Posting**: ~every hour (~10 min past each hour UTC), 2,000+ mention responses/day, 100K+ posts in first 3 months
- **Per-post engagement**: 50,000+ impressions consistently
- **Signal accuracy**: DISPUTED — 83% (Pix On Chain, measures immediate price movement) vs 31-48% win rate (rigorous later analysis, measures sustained profitability)
- **Indigo Upgrade (July 2025)**: Added CoinGecko, BubbleMaps, DeFiLlama structured data
- **$AIXBT token**: Base chain, 1B max supply (60% public, 35% ecosystem, 5% liquidity)
- **Token price**: Peaked $0.94 (Jan 2025), ~$0.02-0.06 by March 2026
- **Terminal access**: 600K AIXBT tokens OR $200/month subscription (added post-Indigo)
- **API Cohort**: Launched April 2025 for third-party developer access
- **Security incident**: March 18, 2025 — hacker stole 55.5 ETH (~$106K) via dashboard access
- **Content types**: Token analysis, whale alerts, risk assessments, trend detection

## 3. Polystrat / Olas Ecosystem
- **Agent**: Polystrat, launched February 2026 by Valory AG on Olas protocol
- **Platform**: Polymarket via Pearl desktop app, self-custodial Safe smart accounts
- **Architecture**: Finite State Machine (FSM) — restricts agent to explicitly programmed actions
- **Strategies**: Balanced (fixed trade size) and Risky (Kelly Criterion-based dynamic sizing)
- **Performance**: 4,200+ trades in one month, single-trade returns up to 376%, win rate 59-64% in tech markets
- **Benchmark**: 37% of Polystrat agents positive P&L vs ~16.8% for human participants
- **LLM usage**: Google Search API + OpenAI for probability calculation
- **MECH framework**: "Pay-to-think" service — on-chain request → off-chain compute → IPFS delivery
- **Olas ecosystem**: 9.9M agent-to-agent transactions, 8.57M Mech requests
- **Governatooorr**: AI governance delegate, autonomously votes on DAO proposals using ChatGPT
- **OLAS tokenomics**: Stake-use-burn flywheel, marketplace fees burn OLAS

## 4. ElizaOS Framework
- **Creator**: Shaw Walters / Eliza Labs (October 2024)
- **Stats**: 17,500+ GitHub stars, 5,400+ forks
- **Language**: TypeScript, requires Bun runtime
- **Key plugins**: @elizaos/plugin-solana, @elizaos/plugin-evm, @elizaos/plugin-twitter, @elizaos/plugin-discord, @elizaos/plugin-telegram, @elizaos/plugin-browser (Playwright), @elizaos/plugin-tts, @elizaos/plugin-tee
- **Character system**: JSON files with bio, lore, knowledge chunks
- **Strengths**: Native blockchain plugins, multi-platform distribution, character-based personality
- **Limitations**: No explicit workflow/scheduling, memory injection vulnerabilities (Princeton), V2 migration issues
- **Notable projects**: pmairca (AI investment DAO), Degen Spartan AI, Doodles/Dreamnet
- **vs LangGraph**: ElizaOS best for crypto+social agents; LangGraph best for complex stateful workflows
- **Recommendation**: Hybrid — ElizaOS as agent shell, custom Python via LangGraph for complex reasoning

## 5. Solana Agent Kit
- **Creator**: SendAI (GitHub: sendaifun/solana-agent-kit)
- **License**: Apache-2.0, ~140K npm downloads
- **Actions**: 60+ across 5 plugin modules:
  - Token: swap (Jupiter), bridge (Wormhole), deploy SPL tokens
  - NFT: deploy collections, mint, list on marketplaces
  - DeFi: staking, lending, perpetual trading, liquidity provision
  - Misc: price feeds, domain registration, TPS queries
  - Blinks: arcade games
- **MCP tools**: GET_ASSET, DEPLOY_TOKEN, GET_PRICE, WALLET_ADDRESS, BALANCE, TRANSFER, MINT_NFT, TRADE, REQUEST_FUNDS, RESOLVE_DOMAIN, GET_TPS
- **Jupiter swap flow**: Quote API (lite-api.jup.ag/swap/v1/quote) → Swap API → sign + submit
- **Ultra API**: Handles slippage and priority fees automatically
- **Python SDK**: sendaifun/solana-agent-kit-py
- **MCP Server**: sendaifun/solana-mcp
- **Integrations**: LangChain function calling, Vercel AI SDK, OpenAI tools

## 6. Autonomous Agent Technical Patterns
- **Critical insight**: 80% of production AI agent failures are state management issues
- **Checkpointing**: PostgreSQL via LangGraph's PostgresSaver — checkpoint every super-step
- **Circuit breakers**: Between agent boundaries, prevent cascading failures
- **Stuck-task detection**: max_iterations limits, timeout watchdogs
- **Semantic caching**: Reduces redundant LLM calls by up to 73% (Redis recommended)
- **Model routing**: Simple tasks → GPT-4o-mini ($0.15/M tokens), complex → GPT-4 ($30/M)
- **Common failures**: Hallucinated API calls (fix: schema validation), infinite loops (fix: max_iterations), context overflow (fix: sliding window summarization)
- **Three-file state pattern**: state.json (current), state_history.jsonl (append-only log), state_checkpoint.json (last known good)
- **Health monitoring**: Heartbeat every 5 min, alert on missed beats, auto-restart on 3 consecutive misses

## 7. Documented Revenue Case Studies

### Solo/Indie AI Businesses
| Business | Revenue | Creator | Model |
|----------|---------|---------|-------|
| HeadshotPro | $300K/month | Danny Postma | AI headshots, $29+/session, 197K customers, built in 30 days |
| Photo AI | $132-138K/month | Pieter Levels | AI photos, PHP+jQuery+SQLite, Stable Diffusion on Replicate |
| Formula Bot | $220K/month | Non-technical founder | AI Excel tool, built with no-code tools |
| Cursor | $16.7M/month | Cursor team | AI code editor, 360K paid subscribers |
| Bolt.new | $3.3M+/month | StackBlitz | AI app builder, $40M ARR in months |

### Autonomous/Bot Revenue
| Business | Revenue | Model |
|----------|---------|-------|
| DereWah Twitter bots | €800-1,200/month | 4 auto-posting accounts, Python scripts, Reddit/Instagram scraping |
| Adavia Davis faceless YT | $40-60K/month | 5 channels, AI pipeline "TubeGen", 85-89% margins |
| Felix (FelixCraftAI) | ~$195K/5 weeks | OpenClaw agent, Claw Mart marketplace, digital products |

### Key Patterns
- Solo or small teams dominate
- API wrapper businesses with specific UX beat foundational model companies
- Distribution matters more than technology
- Speed to revenue is fast — many hit positive revenue within days

## 8. n8n Workflow Automation
- **Users**: 230K+ active, 500+ integrations
- **Critical nodes**: AI Agent, Schedule Trigger (cron), Webhook, HTTP Request (retry 3x), Code (JS/Python), IF/Switch, Execute Workflow, MCP Server Trigger, MCP Client Tool
- **Memory options**: Simple Memory (dev only), Postgres Chat Memory (production), Redis Chat Memory
- **AI Agent node**: LangChain-powered, tool calling, multi-agent delegation via AI Agent Tool sub-nodes
- **vs LangGraph**: n8n wins on speed/integrations/learning curve; LangGraph wins on state management/orchestration/control
- **Hybrid approach**: n8n for triggers/scheduling/webhooks/notifications; LangGraph for complex reasoning/state
- **Self-hosting**: $3-15/month on Hetzner/Contabo vs $24-50+/month n8n Cloud
- **Limitations**: No cross-execution state (needs external DB), retry capped at 5 attempts, crashes on 100K+ row datasets

## 9. Polymarket CLOB API
- **4 APIs**: Gamma (market discovery), CLOB (trading), Data (positions), WebSocket (real-time)
- **Settlement**: Polygon mainnet, USDC.e collateral
- **Auth**: L1 (wallet EIP-712) for credentials, L2 (HMAC-SHA256) for trading
- **SDKs**: Python (py-clob-client, 915+ stars), TypeScript, Rust, Go
- **Orders**: Fundamentally limit orders; GTC, GTD, FOK, FAK types; batch up to 15/request
- **Fees**: 0% on most markets; fee-enabled ~1.56% taker at 50% prob, 0% maker + daily rebates
- **Rate limits**: 9,000 req/10s general, 3,500 req/10s orders, 1,500 req/10s orderbook
- **WebSocket**: Real-time book snapshots, price changes, trade confirmations, 10s ping
- **NegRisk**: Capital efficiency for multi-outcome events — NOT arbitrage (always costs $1.00/set)
- **Real arb**: Only when YES prices sum < 1.0 in standard (non-NegRisk) markets
- **Geo restrictions**: 33+ countries including France, UK, Russia; US users via separate CFTC platform
- **Infrastructure**: AWS eu-west-2 (London), typical 50-100ms latency

## 10. MakeUGC API
- **Endpoints**: GET /video/avatars, GET /video/voices, POST /video/generate, GET /video/status, POST /custom-avatar/generate
- **Auth**: X-Api-Key header
- **Features**: 300+ stock avatars, 35+ languages, 1,500-char script limit
- **Processing**: 2-10 minutes per video
- **Webhooks**: None — polling only at 5-10s intervals
- **SDKs**: None
- **Access**: Enterprise-only (requires sales call)
- **Credits**: 1/standard video, plans from $49/mo (5 credits) to custom enterprise

## 11. HeyGen API
- **Endpoints**: 50+ including Video Agent API, Studio Video, template-based, translation, streaming
- **Auth**: X-Api-Key header or OAuth 2.0
- **Webhooks**: Full CRUD system (avatar_video.success, avatar_video.fail, etc.)
- **Pricing**: $5 PAYG minimum; 1 credit/min (Avatar III), ~6 credits/min (Avatar IV), 3 credits/min translation
- **Features**: 500+ stock avatars, voice cloning, 175+ language translation with lip-sync
- **Output**: MP4/WebM at 1080p (4K enterprise only)
- **Credits**: Expire every 30 days
- **SDKs**: Official Python and Node.js
- **Batch**: Parallel processing supported

## 12. X/Twitter API v2
- **Basic tier**: $200/month (doubled from $100 in 2024)
- **Limits**: 15,000 reads/month, 50,000 writes/month
- **Auth**: OAuth 1.0a or OAuth 2.0 PKCE
- **Post tweet**: POST /2/tweets with JSON body
- **Thread**: Chain replies using reply.in_reply_to_tweet_id
- **Hidden limits**: ~300 posts/3h undocumented wall even on Pro tier
- **DM access**: Basic tier severely restricted (~1 req/24h for GET /2/dm_events)
- **Pay-as-you-go**: Launched February 2026, early analysis suggests not cheaper at equivalent usage
- **$200-to-$5,000 gap**: Between Basic and Pro tiers, major pain point

## 13. Amazon Affiliate / Deal Bot Economics
- **Commission rates**: Luxury Beauty 10%, Digital Music/Handmade 5%, Books/Kitchen 4.5%, Fashion/Devices 4%, Toys/Furniture 3%, PC Components 2.5%, TVs 2%, Grocery/Health 1%
- **Cookie**: 24 hours standard, 90-day cart extension
- **Market share**: Amazon holds 46% of affiliate marketing
- **CamelCamelCamel**: ~1.4M monthly organic visits, monetizes via affiliate tags on every link
- **Slickdeals**: $1.45B annual sales driven, acquired for ~$500M

## 14. Faceless YouTube Economics
- **CPM by niche**: Finance $15-50, Insurance $12-38, Legal $10-35, Tech/SaaS $8-25, Education ~$9.89, Health $6-15, Motivation $3-6, Gaming ~$4, Music ~$1.36
- **RPM**: ~55% of CPM after YouTube's 45% cut
- **Q4 boost**: CPMs run 30-50% higher
- **Tool costs**: $100-190/month total
- **Timeline**: 6-12 months to monetization at 2-3x/week posting
- **July 2025 policy**: AI as tool with human creative input = OK; AI as entire creative process = demonetization risk
- **True Crime Case Files**: 83K subs channel removed for 150+ AI-generated videos

## 15. Chrome Extension Economics
- **Documented revenue**: GMass ~$200K+/mo, Closet Tools ~$42K/mo, Eightify ~$45K/mo, GoFullPage ~$10K/mo
- **AI Chrome extension market**: $2B (2025) → $10B by 2033 (25% CAGR)
- **Publishing fee**: $5 one-time
- **Best monetization**: Freemium + subscription (2-5% conversion, $4.99-$20/mo sweet spot)
- **Profit margins**: 70-85%
- **Exit multiplier**: 40-60x monthly net profit
- **Required**: Manifest V3 (service workers, no remotely-hosted code)
- **Review time**: 1-3 business days

## 16. AI Podcast Factory
- **Reference**: Inception Point AI — 3,000+ episodes/week across 5,000+ shows with 4 staff
- **Voice**: Hume AI's Empathic Voice Interface
- **Cost**: Under $1/episode ($0.30-0.85 breakdown: LLM script $0.05-0.20, voice $0.20-0.50, audio $0.05-0.10)
- **Break-even**: 20 listeners per episode via programmatic advertising (Triton Digital)
- **Key advantage**: NO platform anti-AI policies (unlike YouTube's July 2025 crackdown)
- **Distribution**: Spreaker, Spotify, Apple Podcasts, all major platforms

## 17. Crypto Airdrop Farming
- **Expected 2026**: Polymarket ($POLY, confirmed $750M), OpenSea ($SEA), MegaETH ($107M raised), Monad ($244M raised), MetaMask, Backpack (25% community)
- **Historical yields**: Uniswap 400 UNI/wallet ($1,200 launch, $16K peak), Arbitrum 625-10,000+ ARB ($812-$13K+)
- **Sybil detection**: Cluster detection (Louvain), AI/ML behavioral analysis (Trusta Labs), IP tracking, community bounties
- **Arbitrum Sybil hunters**: Consolidated $3.3M from 1,496 wallets
- **Key shift**: "Deep farming beats wide farming" — 2-3 ecosystems with 6-month activity > 50 protocols once
- **Warning**: 88% of airdropped tokens lose value within 3 months

## 18. Sports Betting Arbitrage
- **Betfair API**: Free for account holders; listMarketBook max 5 calls/sec/market, placeOrders 1K tx/sec, Stream API for push data
- **The Odds API**: 70+ sports, 40+ bookmakers
- **Returns**: 98% of opportunities < 1.2%, pre-game 1-3% per trade
- **Theoretical max**: $10K bankroll, 1 arb/day at 2% = ~$6K/month (but account restrictions binding)
- **Account lifespan**: 1-6 months at mainstream bookmakers (some hours)
- **Arb-friendly**: Only Pinnacle and Sbobet reliably
- **Betfair scalping**: 1-2 tick movements, £2-5/trade, 25-30 trades/day target

## 19. AI Bug Bounty Hunting
- **HackerOne payouts**: $81M over 12 months (July 2024-June 2025), 13% YoY increase
- **AI in scope**: 1,121 programs (270% YoY increase)
- **AI agent reports**: 560+ valid submissions
- **Average payouts**: Critical $7,200, High $3,000, Medium $1,100, Low $254
- **Google Big Sleep**: Discovered real-world vulnerabilities using LLMs
- **CAI framework**: Open-source bug bounty AI tooling
- **XBOW**: #1 on HackerOne, but net loss on inference costs (details proprietary)
- **Earnings**: Beginners $2K-8K year one, focused full-time $20K-75K, top earners six figures
- **Viability for autonomous agent**: Real but narrow — excels at recon/code patterns/fuzzing, needs human for complex chains

## 20. White-Label AI Chatbot Reselling
- **Market**: $10-11.5B in 2026, growing to $32.45B by 2031 (23% CAGR)
- **Platforms**: Stammer.ai ($49-497/mo, 1,300+ agencies), BotPenguin (150+ partners, 60K+ businesses), CustomGPT.ai (15-20% recurring commission 2 years), GoHighLevel ($97-497/mo)
- **Pricing**: Resellers charge $300-1K+/month per agent (3-5x markup)
- **Target**: 10 clients × $1K/mo = $10K MRR first year
- **Gross margins**: Target 60%+ after model/tooling/support costs
- **Message costs**: $0.005 (GPT-3.5) to $0.05 (GPT-4) per message on Stammer.ai
