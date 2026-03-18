"""Example: X/Twitter Posting Patterns

Reference patterns for src/agents/communicator.py.
Uses tweepy for OAuth 1.0a (recommended for Basic tier posting).
"""
import tweepy
import os
import random
import asyncio
from datetime import datetime


def get_x_client() -> tweepy.Client:
    """Create authenticated X API v2 client."""
    return tweepy.Client(
        consumer_key=os.getenv("X_API_KEY"),
        consumer_secret=os.getenv("X_API_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_SECRET"),
    )


async def post_tweet(text: str) -> dict:
    """Post a single tweet. Returns tweet data including ID."""
    client = get_x_client()
    response = client.create_tweet(text=text)
    return {"id": response.data["id"], "text": text}


async def post_thread(tweets: list[str]) -> list[dict]:
    """Post a thread by chaining replies.
    
    tweets[0] = first tweet (hook)
    tweets[1:] = replies chained to previous
    """
    client = get_x_client()
    results = []
    
    # Post first tweet
    first = client.create_tweet(text=tweets[0])
    results.append({"id": first.data["id"], "text": tweets[0]})
    
    # Chain replies
    previous_id = first.data["id"]
    for tweet_text in tweets[1:]:
        await asyncio.sleep(random.uniform(2, 8))  # Jitter between thread posts
        reply = client.create_tweet(
            text=tweet_text,
            in_reply_to_tweet_id=previous_id,
        )
        results.append({"id": reply.data["id"], "text": tweet_text})
        previous_id = reply.data["id"]
    
    return results


async def self_reply(original_tweet_id: str, reply_text: str) -> dict:
    """Reply to your own tweet (triggers 150x algorithm multiplier).
    
    ALWAYS do this 5-15 minutes after posting.
    """
    await asyncio.sleep(random.uniform(300, 900))  # 5-15 min delay
    client = get_x_client()
    response = client.create_tweet(
        text=reply_text,
        in_reply_to_tweet_id=original_tweet_id,
    )
    return {"id": response.data["id"], "text": reply_text}


async def monitor_mentions(user_id: str, since_id: str | None = None) -> list[dict]:
    """Poll for new mentions. Call every 5 minutes.
    
    Basic tier: 15,000 reads/month = ~347 reads/day = ~24/hour.
    Budget: 12 reads/hour for mentions, 12 for other reads.
    """
    client = get_x_client()
    kwargs = {"id": user_id, "max_results": 10}
    if since_id:
        kwargs["since_id"] = since_id
    
    response = client.get_users_mentions(**kwargs)
    if not response.data:
        return []
    
    return [{"id": t.id, "text": t.text} for t in response.data]


# ============================================================
# CONTENT TEMPLATES (inject data from scanner/trader)
# ============================================================

def morning_briefing(data: dict) -> list[str]:
    """Generate Morning Briefing thread from market data."""
    return [
        f"☀️ ARCANA MORNING BRIEFING — {data['date']}\n\n"
        f"MARKETS OVERNIGHT:\n"
        f"• SOL: ${data['sol_price']} ({data['sol_change']}%)\n"
        f"• BTC: ${data['btc_price']} ({data['btc_change']}%)\n"
        f"• Total crypto mcap: ${data['total_mcap']}",
        
        f"TRENDING SOLANA TOKENS:\n"
        f"• {data['trending'][0]}\n"
        f"• {data['trending'][1]}\n\n"
        f"The signal is always there. Most just aren't looking.",
    ]


def trade_receipt(data: dict) -> str:
    """Generate Trade Receipt from trade outcome."""
    return (
        f"ARCANA TRADE RECEIPT #{data['number']}\n"
        f"══════════════════════════════\n"
        f"Market: {data['pair']} ({data['exchange']})\n"
        f"Direction: {data['direction']}\n"
        f"Entry: ${data['entry']} | Exit: ${data['exit']}\n"
        f"Size: ${data['size']} ({data['pct']}% of portfolio)\n\n"
        f"RESULT: {'+'if data['pnl']>0 else ''}${data['pnl']:.2f} ({data['pnl_pct']:.1f}%)\n"
        f"PORTFOLIO: ${data['total']:.2f}\n"
        f"══════════════════════════════\n"
        f"The pattern is the profit. | arcanaoperations.com"
    )
