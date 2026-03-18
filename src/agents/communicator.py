"""ARCANA AI — Communicator Agent (X/Twitter)
Read docs/SOCIAL_MEDIA.md before implementing.

Responsibilities:
- Post scheduled content: Morning Briefing (7 AM PT daily), Weekly Postmortem (Sunday 10 AM PT)
- Post event-driven content: Trade Receipts (after every trade), Market Alerts (signal thresholds)
- Monitor @mentions every 5 minutes, respond to genuine questions
- Monitor DMs for consulting leads (Basic tier DM access is limited — see notes)
- Engage with relevant crypto/AI/business conversations
- Track engagement metrics in Supabase content_posts table

Critical X API constraints (Basic $200/mo tier):
- 15,000 reads/month, 50,000 writes/month
- DM read access severely limited (~1 req/24h) — use n8n webhook as backup
- Add random 1-15 min jitter to all posting intervals (avoid bot detection)
- Warm up account for 2-3 weeks before heavy posting
- NEVER post identical content twice
- Keep links in replies, not main tweets (suppressed by algorithm)

Content quality gate:
- Every post must match SOUL.md personality
- No hype language, no price predictions, no token shilling
- Data-backed claims only
- Use the Four Suits system to balance content mix
"""
# TODO: Implement communicator agent
