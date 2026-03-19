# UGC.md — AI Video Production Pipeline

## Market Context
UGC market: $7.6B (2025), 28% annual growth, $64B by 2034. Only 16% of brands have UGC strategy.
Human creators: $150-500/video (avg $198, dropped 44% YoY).
AI production: MakeUGC $5-10/video, HeyGen $1-2/video = 95-99% margins.
Performance: 400% higher CTR, 29% better conversions, 74% higher product page conversion vs traditional ads.

## HeyGen API (Primary — more capable)
Docs: docs.heygen.com
Auth: X-Api-Key header or OAuth 2.0
50+ endpoints. Full webhook system.

### Create a video:
```python
# POST https://api.heygen.com/v2/video/generate
payload = {
    "video_inputs": [{
        "character": {"type": "avatar", "avatar_id": "avatar_id_here"},
        "voice": {"type": "text", "input_text": "Your UGC script here", "voice_id": "voice_id"},
        "background": {"type": "color", "value": "#FFFFFF"}
    }],
    "dimension": {"width": 1080, "height": 1920}  # Vertical for social
}
response = httpx.post("https://api.heygen.com/v2/video/generate",
    headers={"X-Api-Key": HEYGEN_KEY}, json=payload)
video_id = response.json()["data"]["video_id"]

# Poll for completion or use webhook
status = httpx.get(f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
    headers={"X-Api-Key": HEYGEN_KEY})
```

Pricing: $5 PAYG minimum. 1 credit/min (Avatar III), ~6 credits/min (Avatar IV).
500+ stock avatars. Voice cloning available. 175+ language translation with lip-sync.
Output: MP4/WebM at 1080p. Credits expire every 30 days.

## MakeUGC API (Simpler, Enterprise-only)
Docs: app.makeugc.ai/api/platform/documentation
Auth: X-Api-Key header

### Endpoints:
```
GET  /video/avatars     — List available avatars (300+)
GET  /video/voices      — List available voices
POST /video/generate    — Submit video generation job
GET  /video/status      — Check job status (poll every 5-10 seconds)
POST /custom-avatar/generate — Generate custom avatar
```

1,500-char script limit. 2-10 min processing. No webhooks — polling only.
Enterprise tier required for API access (contact sales).

## Production Pipeline
1. Client submits product URL + target audience + key selling points
2. Claude Sonnet generates 3 script variants (hook, body, CTA format)
3. Agent selects avatar matching brand demographics
4. HeyGen API generates video (1-3 minutes processing)
5. Quality gate: AI reviews lip sync, brand alignment, CTA clarity
6. Delivery via Supabase storage link + Stripe payment

## Pricing Model
- Single video: $50-150 (cost: $1-5, margin: 93-98%)
- Monthly 10-pack: $400-800/mo (cost: $10-50)
- Enterprise 50+: $1,500-3,000/mo (cost: $50-250)
- White-label for agencies: $5-15/video wholesale
