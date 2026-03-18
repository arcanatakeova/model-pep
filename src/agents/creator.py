"""ARCANA AI — Creator Agent (UGC, Products, Content)
Read docs/UGC.md and docs/REVENUE_CHANNELS.md before implementing.

Responsibilities:
- UGC Video Production: script generation, avatar selection, HeyGen/MakeUGC API calls, quality gate, delivery
- Digital Product Management: create/update products on Gumroad, track sales
- Newsletter Content: generate weekly newsletter for Beehiiv
- Podcast Production: script + ElevenLabs voice synthesis + Spreaker distribution
- Programmatic SEO: generate articles, publish to content site
- Prompt Library: package and update prompt collections

UGC Pipeline (highest margin channel — 93-98%):
1. Receive order (client product URL + audience + selling points)
2. Generate 3 script variants via Sonnet
3. Select avatar from HeyGen (500+ options)
4. Generate video via HeyGen API (1-3 min processing)
5. Quality gate: review lip sync, brand alignment, CTA
6. Deliver via Supabase storage + process Stripe payment
7. Log to ugc_orders table

Pricing: Single $50-150, Monthly 10-pack $400-800, Enterprise $1,500-3,000
"""
# TODO: Implement creator agent
