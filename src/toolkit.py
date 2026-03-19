"""ARCANA AI — Unified Toolkit Layer.

Wraps all installed free toolkits into clean, reusable utilities
that every ARCANA module can import. No module needs to know which
underlying library is being used.

Capabilities:
- NLP: sentiment analysis, entity extraction, fuzzy matching
- Web: scraping, RSS feeds, user-agent rotation
- Content: markdown conversion, HTML sanitization, slug generation
- Validation: email, URL, phone number
- Media: image resizing, watermarking, format conversion
- Cache: in-memory (TTL/LRU) and disk-based persistent cache
- Logging: structured logging with loguru
- Metrics: Prometheus counters/histograms
- Templates: Jinja2 email/report templates
- Formatting: human-readable numbers, dates, tables
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════
# NLP & TEXT PROCESSING
# ═══════════════════════════════════════════════

def sentiment_score(text: str) -> dict[str, float]:
    """Analyze sentiment using TextBlob. Returns polarity (-1 to 1) and subjectivity (0 to 1)."""
    from textblob import TextBlob
    blob = TextBlob(text)
    return {
        "polarity": round(blob.sentiment.polarity, 3),
        "subjectivity": round(blob.sentiment.subjectivity, 3),
        "label": "positive" if blob.sentiment.polarity > 0.1
                 else "negative" if blob.sentiment.polarity < -0.1
                 else "neutral",
    }


def fuzzy_match(s1: str, s2: str) -> float:
    """Fuzzy string similarity ratio (0-1). Uses Levenshtein distance."""
    try:
        import Levenshtein
        return Levenshtein.ratio(s1.lower(), s2.lower())
    except ImportError:
        # Fallback: simple character overlap
        set1, set2 = set(s1.lower()), set(s2.lower())
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)


def is_duplicate_text(text1: str, text2: str, threshold: float = 0.85) -> bool:
    """Check if two texts are near-duplicates."""
    return fuzzy_match(text1, text2) >= threshold


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """Extract keywords from text using TextBlob noun phrase extraction."""
    from textblob import TextBlob
    blob = TextBlob(text)
    # Get noun phrases and single nouns
    phrases = [str(p).lower() for p in blob.noun_phrases]
    # Deduplicate and rank by frequency
    freq: dict[str, int] = {}
    for p in phrases:
        freq[p] = freq.get(p, 0) + 1
    return sorted(freq.keys(), key=lambda k: -freq[k])[:top_n]


def detect_language(text: str) -> str:
    """Detect text language using TextBlob."""
    try:
        from textblob import TextBlob
        blob = TextBlob(text[:500])
        return str(blob.detect_language())
    except Exception:
        return "en"  # Default to English


# ═══════════════════════════════════════════════
# WEB SCRAPING
# ═══════════════════════════════════════════════

def scrape_html(html: str, selector: str = "", tag: str = "") -> list[str]:
    """Extract text from HTML using BeautifulSoup.

    Args:
        html: Raw HTML string
        selector: CSS class to filter by (e.g., "post-title")
        tag: HTML tag to find (e.g., "h2", "p", "a")
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    if selector:
        elements = soup.find_all(class_=selector)
    elif tag:
        elements = soup.find_all(tag)
    else:
        elements = [soup]

    return [el.get_text(strip=True) for el in elements if el.get_text(strip=True)]


def extract_links(html: str, base_url: str = "") -> list[dict[str, str]]:
    """Extract all links from HTML."""
    from bs4 import BeautifulSoup
    from furl import furl
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if base_url and href.startswith("/"):
            href = str(furl(base_url).set(path=href))
        links.append({
            "url": href,
            "text": a.get_text(strip=True),
        })
    return links


def parse_rss_feed(xml_content: str) -> list[dict[str, str]]:
    """Parse an RSS/Atom feed and return entries."""
    import atoma
    try:
        feed = atoma.parse_rss_bytes(xml_content.encode())
        return [
            {
                "title": item.title or "",
                "link": item.link or "",
                "description": (item.description or "")[:500],
                "published": str(item.pub_date) if item.pub_date else "",
            }
            for item in feed.items
        ]
    except Exception:
        try:
            feed = atoma.parse_atom_bytes(xml_content.encode())
            return [
                {
                    "title": entry.title.value if entry.title else "",
                    "link": entry.links[0].href if entry.links else "",
                    "description": (entry.summary.value if entry.summary else "")[:500],
                    "published": str(entry.updated) if entry.updated else "",
                }
                for entry in feed.entries
            ]
        except Exception:
            return []


def random_user_agent() -> str:
    """Get a random browser user-agent string."""
    try:
        from fake_useragent import UserAgent
        return UserAgent().random
    except Exception:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ═══════════════════════════════════════════════
# CONTENT & MARKDOWN
# ═══════════════════════════════════════════════

def markdown_to_html(md_text: str) -> str:
    """Convert Markdown to HTML."""
    import markdown2
    return markdown2.markdown(
        md_text,
        extras=["fenced-code-blocks", "tables", "header-ids", "strike", "task_list"],
    )


def html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown."""
    from markdownify import markdownify
    return markdownify(html, heading_style="ATX", strip=["img", "script", "style"])


def sanitize_html(html: str, allowed_tags: list[str] | None = None) -> str:
    """Sanitize HTML to prevent XSS. Only allows safe tags."""
    import bleach
    tags = allowed_tags or [
        "p", "br", "strong", "em", "a", "ul", "ol", "li",
        "h1", "h2", "h3", "h4", "blockquote", "code", "pre",
        "table", "thead", "tbody", "tr", "th", "td",
    ]
    return bleach.clean(html, tags=tags, attributes={"a": ["href"]}, strip=True)


def slugify(text: str) -> str:
    """Generate a clean URL slug from text."""
    from slugify import slugify as _slugify
    return _slugify(text, max_length=80)


# ═══════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════

def validate_email(email: str) -> dict[str, Any]:
    """Validate an email address. Returns normalized email or error."""
    try:
        from email_validator import validate_email as _validate, EmailNotValidError
        result = _validate(email, check_deliverability=False)
        return {
            "valid": True,
            "normalized": result.normalized,
            "domain": result.domain,
        }
    except Exception as e:
        return {"valid": False, "error": str(e), "normalized": email}


def validate_url(url: str) -> bool:
    """Check if a URL is valid."""
    import validators
    return bool(validators.url(url))


def validate_domain(domain: str) -> bool:
    """Check if a domain name is valid."""
    import validators
    return bool(validators.domain(domain))


# ═══════════════════════════════════════════════
# IMAGE PROCESSING
# ═══════════════════════════════════════════════

def resize_image(
    input_path: str | Path, output_path: str | Path,
    width: int, height: int, quality: int = 85,
) -> bool:
    """Resize an image to specified dimensions."""
    try:
        from PIL import Image
        img = Image.open(str(input_path))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        img.save(str(output_path), quality=quality, optimize=True)
        return True
    except Exception:
        return False


def create_thumbnail(
    input_path: str | Path, output_path: str | Path,
    size: tuple[int, int] = (300, 300),
) -> bool:
    """Create a thumbnail from an image."""
    try:
        from PIL import Image
        img = Image.open(str(input_path))
        img.thumbnail(size, Image.Resampling.LANCZOS)
        img.save(str(output_path), quality=80, optimize=True)
        return True
    except Exception:
        return False


def add_watermark(
    input_path: str | Path, output_path: str | Path,
    text: str = "ARCANA AI", opacity: int = 128,
) -> bool:
    """Add a text watermark to an image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(str(input_path)).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except OSError:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = img.width - text_w - 10
        y = img.height - text_h - 10
        draw.text((x, y), text, font=font, fill=(255, 255, 255, opacity))

        result = Image.alpha_composite(img, overlay).convert("RGB")
        result.save(str(output_path), quality=85)
        return True
    except Exception:
        return False


# Platform-specific image sizes
IMAGE_SIZES = {
    "instagram_square": (1080, 1080),
    "instagram_portrait": (1080, 1350),
    "instagram_story": (1080, 1920),
    "twitter_post": (1200, 675),
    "linkedin_post": (1200, 627),
    "facebook_post": (1200, 630),
    "youtube_thumbnail": (1280, 720),
    "tiktok_cover": (1080, 1920),
    "og_image": (1200, 630),
}


# ═══════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════

_memory_caches: dict[str, Any] = {}


def get_cache(name: str = "default", maxsize: int = 256, ttl: int = 3600) -> Any:
    """Get or create a TTL cache. Items expire after ttl seconds."""
    from cachetools import TTLCache
    if name not in _memory_caches:
        _memory_caches[name] = TTLCache(maxsize=maxsize, ttl=ttl)
    return _memory_caches[name]


def cached_get(cache_name: str, key: str) -> Any | None:
    """Get a value from a named cache."""
    cache = _memory_caches.get(cache_name)
    if cache is None:
        return None
    return cache.get(key)


def cached_set(cache_name: str, key: str, value: Any, maxsize: int = 256, ttl: int = 3600) -> None:
    """Set a value in a named cache (auto-creates if needed)."""
    cache = get_cache(cache_name, maxsize, ttl)
    cache[key] = value


def get_disk_cache(name: str = "arcana") -> Any:
    """Get a persistent disk-based cache."""
    from diskcache import Cache
    cache_dir = Path("data") / "cache" / name
    cache_dir.mkdir(parents=True, exist_ok=True)
    return Cache(str(cache_dir))


# ═══════════════════════════════════════════════
# FORMATTING
# ═══════════════════════════════════════════════

def human_number(n: float) -> str:
    """Format number for humans: 1234567 → '1.2M'."""
    import humanize
    return humanize.intword(int(n))


def human_money(amount: float) -> str:
    """Format money: 1234.5 → '$1,234.50'."""
    return f"${amount:,.2f}"


def human_time_ago(dt: datetime) -> str:
    """Format datetime as 'X ago': '3 hours ago'."""
    import humanize
    return humanize.naturaltime(dt)


def human_filesize(size_bytes: int) -> str:
    """Format file size: 1048576 → '1.0 MB'."""
    import humanize
    return humanize.naturalsize(size_bytes)


def format_table(data: list[dict[str, Any]], headers: str = "keys") -> str:
    """Format data as a pretty table."""
    from tabulate import tabulate as _tabulate
    return _tabulate(data, headers=headers, tablefmt="pipe")


# ═══════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════

_jinja_env = None


def render_template(template_str: str, **kwargs: Any) -> str:
    """Render a Jinja2 template string with variables."""
    global _jinja_env
    if _jinja_env is None:
        from jinja2 import Environment
        _jinja_env = Environment(autoescape=True)
    template = _jinja_env.from_string(template_str)
    return template.render(**kwargs)


# ═══════════════════════════════════════════════
# JSON (FAST)
# ═══════════════════════════════════════════════

def fast_json_dumps(obj: Any) -> str:
    """Fast JSON serialization using orjson."""
    import orjson
    return orjson.dumps(obj, default=str).decode()


def fast_json_loads(data: str | bytes) -> Any:
    """Fast JSON deserialization using orjson."""
    import orjson
    return orjson.loads(data)


# ═══════════════════════════════════════════════
# DATETIME
# ═══════════════════════════════════════════════

def now_utc() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def parse_date(text: str) -> datetime | None:
    """Parse a date from any format."""
    import arrow
    try:
        return arrow.get(text).datetime
    except Exception:
        return None


def time_until(dt: datetime) -> str:
    """Human-readable time until a datetime."""
    import arrow
    return arrow.get(dt).humanize()


# ═══════════════════════════════════════════════
# METRICS (Prometheus)
# ═══════════════════════════════════════════════

_metrics: dict[str, Any] = {}


def counter(name: str, description: str = "") -> Any:
    """Get or create a Prometheus counter."""
    from prometheus_client import Counter
    if name not in _metrics:
        _metrics[name] = Counter(name, description or name)
    return _metrics[name]


def histogram(name: str, description: str = "", buckets: tuple = ()) -> Any:
    """Get or create a Prometheus histogram."""
    from prometheus_client import Histogram
    if name not in _metrics:
        _metrics[name] = Histogram(name, description or name, buckets=buckets or Histogram.DEFAULT_BUCKETS)
    return _metrics[name]


def gauge(name: str, description: str = "") -> Any:
    """Get or create a Prometheus gauge."""
    from prometheus_client import Gauge
    if name not in _metrics:
        _metrics[name] = Gauge(name, description or name)
    return _metrics[name]


# ═══════════════════════════════════════════════
# TOKEN COUNTING
# ═══════════════════════════════════════════════

def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Count tokens for cost estimation."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding(model)
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4  # Fallback approximation


# ═══════════════════════════════════════════════
# URL PROCESSING
# ═══════════════════════════════════════════════

def build_url(base: str, **params: Any) -> str:
    """Build a URL with query parameters."""
    from furl import furl
    f = furl(base)
    f.args.update(params)
    return str(f)


def extract_domain(url: str) -> str:
    """Extract domain from a URL."""
    from furl import furl
    try:
        return furl(url).host or ""
    except Exception:
        return ""
