"""ARCANA AI — Perfect Conversational Memory.

Every conversation ARCANA has — X DMs, email threads, support tickets, Discord —
is stored, threaded, cross-referenced, and retrievable. Before generating any reply,
the full history with that person across ALL channels is injected into LLM context.

Storage: SQLite via Database (conversations + messages tables).
Summaries: LLM-generated for long histories to fit context windows.
Lead detection: Automatic signal scoring on every message.
Follow-ups: Flagged and surfaced in morning reports.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.database import Database
from src.llm import LLM, Tier

logger = logging.getLogger("arcana.conversation_memory")


# ══════════════════════════════════════════════════════════════
# Constants & Enums
# ══════════════════════════════════════════════════════════════

class Channel(str, Enum):
    X_DM = "x_dm"
    X_REPLY = "x_reply"
    EMAIL = "email"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    SUPPORT_TICKET = "support_ticket"
    WEBSITE_CHAT = "website_chat"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    WAITING_REPLY = "waiting_reply"
    FOLLOW_UP_NEEDED = "follow_up_needed"
    RESOLVED = "resolved"
    CONVERTED = "converted"
    ARCHIVED = "archived"


class MessageRole(str, Enum):
    CONTACT = "contact"      # The other person
    ARCANA = "arcana"        # ARCANA's reply
    AGENT = "agent"          # Sub-agent (Iris, Remy)
    HUMAN = "human"          # Ian or Tan intervened


LEAD_SIGNAL_KEYWORDS = [
    "pricing", "price", "cost", "how much", "rates", "budget",
    "hire", "hiring", "looking for", "need help", "need someone",
    "consulting", "consultant", "agency", "service", "services",
    "proposal", "quote", "estimate", "project", "build",
    "contract", "retainer", "engagement", "timeline", "deadline",
    "roi", "revenue", "scale", "growth", "strategy",
    "ai agent", "automation", "seo", "marketing", "shopify",
    "can you", "do you offer", "available", "interested in",
]

# Maximum messages to include in raw history before summarizing
MAX_RAW_MESSAGES = 40
# Maximum characters for context injection
MAX_CONTEXT_CHARS = 12_000


# ══════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════

@dataclass
class Message:
    id: int
    conversation_id: str
    role: str
    content: str
    channel: str
    sentiment: str | None = None
    lead_signals: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    id: str
    contact_id: int
    channel: str
    subject: str | None = None
    status: str = "active"
    sentiment_trend: list[str] = field(default_factory=list)
    lead_score: int = 0
    follow_up_reason: str | None = None
    follow_up_due: str | None = None
    summary: str | None = None
    message_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# ConversationMemory — Main Class
# ══════════════════════════════════════════════════════════════

class ConversationMemory:
    """Perfect conversational memory across all channels.

    Every message is stored. Every person's full history is retrievable.
    Before any reply, the complete cross-channel context is built and
    injected into the LLM prompt.
    """

    def __init__(self, db: Database, llm: LLM) -> None:
        self.db = db
        self.llm = llm
        self._init_tables()

    # ──────────────────────────────────────────────────────────
    # Schema
    # ──────────────────────────────────────────────────────────

    def _init_tables(self) -> None:
        """Create conversations and messages tables if they don't exist."""
        conn = self.db._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                contact_id INTEGER NOT NULL REFERENCES contacts(id),
                channel TEXT NOT NULL,
                subject TEXT,
                status TEXT DEFAULT 'active',
                sentiment_trend TEXT DEFAULT '[]',
                lead_score INTEGER DEFAULT 0,
                follow_up_reason TEXT,
                follow_up_due TEXT,
                summary TEXT,
                message_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_conv_contact ON conversations(contact_id);
            CREATE INDEX IF NOT EXISTS idx_conv_channel ON conversations(channel);
            CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);
            CREATE INDEX IF NOT EXISTS idx_conv_follow_up ON conversations(follow_up_due);
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                channel TEXT NOT NULL,
                sentiment TEXT,
                lead_signals TEXT DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
            CREATE INDEX IF NOT EXISTS idx_msg_role ON messages(role);
        """)
        conn.commit()
        logger.info("Conversation memory tables initialized")

    # ──────────────────────────────────────────────────────────
    # Conversation Lifecycle
    # ──────────────────────────────────────────────────────────

    def start_conversation(
        self,
        contact_id: int,
        channel: str,
        initial_message: str,
        role: str = MessageRole.CONTACT,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start a new conversation thread. Returns conversation ID."""
        conv_id = f"conv_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn = self.db._get_conn()

        conn.execute(
            "INSERT INTO conversations (id, contact_id, channel, subject, status, "
            "message_count, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, 'active', 0, ?, ?, ?)",
            (conv_id, contact_id, channel, subject, now, now,
             json.dumps(metadata or {})),
        )
        conn.commit()

        # Add the initial message
        self.add_message(conv_id, role, initial_message, channel=channel)

        # Log as interaction in the main DB too
        self.db.log_interaction(
            contact_id, channel, "inbound" if role == MessageRole.CONTACT else "outbound",
            initial_message, metadata={"conversation_id": conv_id},
        )

        # Index for FTS
        self.db._index_fts("conversations", 0, f"{conv_id} {subject or ''} {initial_message}")

        logger.info("Started conversation %s with contact %d on %s", conv_id, contact_id, channel)
        return conv_id

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add a message to an existing conversation. Returns message ID."""
        conn = self.db._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Get conversation for channel fallback
        conv_row = conn.execute(
            "SELECT channel, contact_id FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if not conv_row:
            raise ValueError(f"Conversation {conv_id} not found")

        msg_channel = channel or conv_row["channel"]

        # Analyze sentiment
        sentiment = self._quick_sentiment(content)

        # Detect lead signals
        signals = self._detect_signals(content)

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, channel, "
            "sentiment, lead_signals, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, role, content, msg_channel, sentiment,
             json.dumps(signals), now, json.dumps(metadata or {})),
        )
        msg_id = cur.lastrowid

        # Update conversation
        conn.execute(
            "UPDATE conversations SET message_count = message_count + 1, "
            "updated_at = ?, status = ? WHERE id = ?",
            (now, "waiting_reply" if role == MessageRole.CONTACT else "active", conv_id),
        )

        # Update sentiment trend
        trend_row = conn.execute(
            "SELECT sentiment_trend FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        trend = json.loads(trend_row["sentiment_trend"]) if trend_row else []
        trend.append(sentiment)
        # Keep last 20 sentiments
        trend = trend[-20:]
        conn.execute(
            "UPDATE conversations SET sentiment_trend = ? WHERE id = ?",
            (json.dumps(trend), conv_id),
        )

        # Update lead score if signals found
        if signals:
            score_bump = len(signals) * 5
            conn.execute(
                "UPDATE conversations SET lead_score = lead_score + ? WHERE id = ?",
                (score_bump, conv_id),
            )
            # Also bump contact lead score
            conn.execute(
                "UPDATE contacts SET lead_score = lead_score + ? WHERE id = ?",
                (score_bump, conv_row["contact_id"]),
            )

        conn.commit()

        # Index message in FTS
        self.db._index_fts("messages", msg_id, content)

        # Log as interaction
        direction = "inbound" if role == MessageRole.CONTACT else "outbound"
        self.db.log_interaction(
            conv_row["contact_id"], msg_channel, direction, content,
            sentiment=sentiment,
            is_lead_signal=1 if signals else 0,
            metadata={"conversation_id": conv_id, "lead_signals": signals},
        )

        logger.info(
            "Message added to %s | role=%s sentiment=%s signals=%d",
            conv_id, role, sentiment, len(signals),
        )
        return msg_id

    # ──────────────────────────────────────────────────────────
    # Context Retrieval (the key feature)
    # ──────────────────────────────────────────────────────────

    def get_context_for_reply(self, contact_id: int, current_conv_id: str | None = None) -> str:
        """Build full conversational context for LLM injection.

        Returns a formatted string containing:
        1. Contact profile summary
        2. Current conversation history
        3. Cross-channel conversation summaries
        4. CRM data (deals, lead score, status)
        5. Detected lead signals and sentiment trends

        This is THE method to call before generating any reply.
        """
        sections: list[str] = []

        # 1. Contact profile
        contact = self.db.get_contact(contact_id)
        if contact:
            sections.append(self._format_contact_profile(contact))

        # 2. Current conversation (full recent messages)
        if current_conv_id:
            messages = self._get_messages(current_conv_id)
            if messages:
                sections.append(self._format_current_conversation(messages, current_conv_id))

        # 3. Cross-channel history (summaries of other conversations)
        all_convs = self._get_conversations_for_contact(contact_id)
        other_convs = [c for c in all_convs if c["id"] != current_conv_id]
        if other_convs:
            sections.append(self._format_cross_channel_history(other_convs))

        # 4. CRM context (deals, revenue)
        crm_context = self._get_crm_context(contact_id)
        if crm_context:
            sections.append(crm_context)

        # 5. Lead signals and sentiment
        signal_context = self._get_signal_context(contact_id, current_conv_id)
        if signal_context:
            sections.append(signal_context)

        full_context = "\n\n".join(sections)

        # Truncate if too long
        if len(full_context) > MAX_CONTEXT_CHARS:
            full_context = full_context[:MAX_CONTEXT_CHARS] + "\n\n[Context truncated for length]"

        return full_context

    def get_all_conversations(self, contact_id: int) -> list[Conversation]:
        """Get all conversations for a contact across all channels."""
        rows = self._get_conversations_for_contact(contact_id)
        return [self._row_to_conversation(r) for r in rows]

    # ──────────────────────────────────────────────────────────
    # Follow-ups
    # ──────────────────────────────────────────────────────────

    def flag_for_followup(
        self, conv_id: str, reason: str, due: str | None = None,
    ) -> None:
        """Flag a conversation as needing follow-up."""
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE conversations SET status = 'follow_up_needed', "
            "follow_up_reason = ?, follow_up_due = ? WHERE id = ?",
            (reason, due, conv_id),
        )
        conn.commit()

        # Log event
        self.db.log_event("follow_up_flagged", "conversation", {
            "conversation_id": conv_id,
            "reason": reason,
            "due": due,
        })
        logger.info("Flagged %s for follow-up: %s", conv_id, reason)

    def get_pending_followups(self) -> list[dict[str, Any]]:
        """Get all conversations needing follow-up, ordered by due date."""
        conn = self.db._get_conn()
        rows = conn.execute(
            "SELECT c.*, ct.name, ct.email, ct.x_handle, ct.company "
            "FROM conversations c "
            "LEFT JOIN contacts ct ON c.contact_id = ct.id "
            "WHERE c.status = 'follow_up_needed' "
            "ORDER BY c.follow_up_due ASC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stale_conversations(self, hours: int = 48) -> list[dict[str, Any]]:
        """Find conversations waiting for ARCANA's reply for too long."""
        conn = self.db._get_conn()
        rows = conn.execute(
            "SELECT c.*, ct.name, ct.email, ct.x_handle "
            "FROM conversations c "
            "LEFT JOIN contacts ct ON c.contact_id = ct.id "
            "WHERE c.status = 'waiting_reply' "
            "AND c.updated_at <= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────
    # Summarization
    # ──────────────────────────────────────────────────────────

    async def summarize_conversation(self, conv_id: str) -> str:
        """Generate an LLM summary of a conversation for context compression."""
        messages = self._get_messages(conv_id, limit=200)
        if not messages:
            return "No messages in this conversation."

        transcript = "\n".join(
            f"[{m['role']}] ({m['created_at'][:10]}): {m['content']}"
            for m in messages
        )

        prompt = (
            "Summarize this conversation in 3-5 bullet points. Include:\n"
            "- What the person wants/needs\n"
            "- Key decisions or commitments made\n"
            "- Current status and next steps\n"
            "- Any buying signals or objections\n"
            "- Tone/sentiment of the interaction\n\n"
            f"Conversation transcript:\n{transcript}"
        )

        summary = await self.llm.ask(prompt, tier=Tier.HAIKU, temperature=0.3, max_tokens=500)

        # Store the summary
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE conversations SET summary = ? WHERE id = ?",
            (summary, conv_id),
        )
        conn.commit()

        logger.info("Summarized conversation %s (%d messages)", conv_id, len(messages))
        return summary

    async def summarize_contact_history(self, contact_id: int) -> str:
        """Summarize ALL conversations with a contact into a single brief."""
        convs = self._get_conversations_for_contact(contact_id)
        if not convs:
            return "No conversation history with this contact."

        # Get or generate summaries for each conversation
        summaries: list[str] = []
        for conv in convs:
            if conv["summary"]:
                summaries.append(
                    f"[{conv['channel']}] ({conv['created_at'][:10]}): {conv['summary']}"
                )
            else:
                # Summarize on the fly
                summary = await self.summarize_conversation(conv["id"])
                summaries.append(
                    f"[{conv['channel']}] ({conv['created_at'][:10]}): {summary}"
                )

        all_summaries = "\n\n".join(summaries)
        prompt = (
            "Create a concise relationship brief from these conversation summaries. "
            "Focus on: who this person is, what they need, where we stand, "
            "and what ARCANA should know before the next interaction.\n\n"
            f"{all_summaries}"
        )

        brief = await self.llm.ask(prompt, tier=Tier.HAIKU, temperature=0.3, max_tokens=600)
        return brief

    # ──────────────────────────────────────────────────────────
    # Lead Signal Detection
    # ──────────────────────────────────────────────────────────

    def detect_lead_signals(self, conv_id: str) -> dict[str, Any]:
        """Analyze a conversation for lead signals and buying intent."""
        messages = self._get_messages(conv_id)
        if not messages:
            return {"score": 0, "signals": [], "assessment": "no_data"}

        all_signals: list[str] = []
        sentiments: list[str] = []

        for msg in messages:
            if msg["role"] == MessageRole.CONTACT:
                signals = json.loads(msg["lead_signals"]) if msg["lead_signals"] else []
                all_signals.extend(signals)
            if msg["sentiment"]:
                sentiments.append(msg["sentiment"])

        # Score calculation
        unique_signals = list(set(all_signals))
        signal_score = min(len(unique_signals) * 10, 100)

        # Sentiment bonus — positive sentiment adds points
        positive_count = sentiments.count("positive")
        negative_count = sentiments.count("negative")
        sentiment_modifier = (positive_count - negative_count) * 3

        total_score = max(0, min(100, signal_score + sentiment_modifier))

        # Assessment
        if total_score >= 70:
            assessment = "hot_lead"
        elif total_score >= 40:
            assessment = "warm_lead"
        elif total_score >= 15:
            assessment = "mild_interest"
        else:
            assessment = "no_intent"

        # Update conversation lead score
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE conversations SET lead_score = ? WHERE id = ?",
            (total_score, conv_id),
        )
        conn.commit()

        result = {
            "score": total_score,
            "signals": unique_signals,
            "signal_count": len(all_signals),
            "sentiment_breakdown": {
                "positive": positive_count,
                "neutral": sentiments.count("neutral"),
                "negative": negative_count,
            },
            "assessment": assessment,
        }

        if assessment in ("hot_lead", "warm_lead"):
            self.db.log_event("lead_signal_detected", "conversation", {
                "conversation_id": conv_id,
                **result,
            })

        return result

    def get_hot_conversations(self, min_score: int = 40) -> list[dict[str, Any]]:
        """Get conversations with strong lead signals."""
        conn = self.db._get_conn()
        rows = conn.execute(
            "SELECT c.*, ct.name, ct.email, ct.x_handle, ct.company "
            "FROM conversations c "
            "LEFT JOIN contacts ct ON c.contact_id = ct.id "
            "WHERE c.lead_score >= ? AND c.status NOT IN ('resolved', 'archived') "
            "ORDER BY c.lead_score DESC",
            (min_score,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────
    # Conversation Lookup & Search
    # ──────────────────────────────────────────────────────────

    def find_conversation(
        self, contact_id: int, channel: str | None = None,
    ) -> str | None:
        """Find the most recent active conversation for a contact on a channel.

        Useful for continuing an existing thread instead of starting a new one.
        """
        conn = self.db._get_conn()
        where = "contact_id = ? AND status IN ('active', 'waiting_reply')"
        params: list[Any] = [contact_id]
        if channel:
            where += " AND channel = ?"
            params.append(channel)
        row = conn.execute(
            f"SELECT id FROM conversations WHERE {where} "
            "ORDER BY updated_at DESC LIMIT 1",
            params,
        ).fetchone()
        return row["id"] if row else None

    def search_conversations(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search across all conversation messages."""
        return self.db.search(query, source="messages", limit=limit)

    def get_conversation(self, conv_id: str) -> Conversation | None:
        """Get a single conversation by ID."""
        conn = self.db._get_conn()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        return self._row_to_conversation(dict(row)) if row else None

    def get_messages(self, conv_id: str, limit: int = 100) -> list[Message]:
        """Get messages for a conversation as Message objects."""
        rows = self._get_messages(conv_id, limit=limit)
        return [
            Message(
                id=r["id"],
                conversation_id=r["conversation_id"],
                role=r["role"],
                content=r["content"],
                channel=r["channel"],
                sentiment=r["sentiment"],
                lead_signals=json.loads(r["lead_signals"]) if r["lead_signals"] else [],
                created_at=r["created_at"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
            )
            for r in rows
        ]

    def update_status(self, conv_id: str, status: str) -> None:
        """Update conversation status."""
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE conversations SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, conv_id),
        )
        conn.commit()

    # ──────────────────────────────────────────────────────────
    # Reporting (for morning reports / nightly review)
    # ──────────────────────────────────────────────────────────

    def get_daily_summary(self, days: int = 1) -> dict[str, Any]:
        """Get conversation activity summary for the last N days."""
        conn = self.db._get_conn()
        cutoff = f"-{days} days"

        new_convs = conn.execute(
            "SELECT COUNT(*) as c FROM conversations WHERE created_at >= datetime('now', ?)",
            (cutoff,),
        ).fetchone()["c"]

        new_msgs = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE created_at >= datetime('now', ?)",
            (cutoff,),
        ).fetchone()["c"]

        waiting = conn.execute(
            "SELECT COUNT(*) as c FROM conversations WHERE status = 'waiting_reply'"
        ).fetchone()["c"]

        followups = conn.execute(
            "SELECT COUNT(*) as c FROM conversations WHERE status = 'follow_up_needed'"
        ).fetchone()["c"]

        hot_leads = conn.execute(
            "SELECT COUNT(*) as c FROM conversations WHERE lead_score >= 40 "
            "AND status NOT IN ('resolved', 'archived')"
        ).fetchone()["c"]

        by_channel = conn.execute(
            "SELECT channel, COUNT(*) as count FROM messages "
            "WHERE created_at >= datetime('now', ?) GROUP BY channel",
            (cutoff,),
        ).fetchall()

        return {
            "new_conversations": new_convs,
            "new_messages": new_msgs,
            "waiting_reply": waiting,
            "follow_ups_pending": followups,
            "hot_leads": hot_leads,
            "messages_by_channel": {r["channel"]: r["count"] for r in by_channel},
        }

    def format_daily_summary(self, days: int = 1) -> str:
        """Format daily summary as readable text for morning report."""
        s = self.get_daily_summary(days)
        channels = ", ".join(f"{ch}: {cnt}" for ch, cnt in s["messages_by_channel"].items())
        return (
            f"**Conversation Activity ({days}d)**\n"
            f"New conversations: {s['new_conversations']} | "
            f"Messages: {s['new_messages']}\n"
            f"Waiting reply: {s['waiting_reply']} | "
            f"Follow-ups: {s['follow_ups_pending']} | "
            f"Hot leads: {s['hot_leads']}\n"
            f"By channel: {channels or 'none'}"
        )

    # ══════════════════════════════════════════════════════════════
    # Private Helpers
    # ══════════════════════════════════════════════════════════════

    def _get_messages(self, conv_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get raw message rows for a conversation, oldest first."""
        conn = self.db._get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_conversations_for_contact(self, contact_id: int) -> list[dict[str, Any]]:
        """Get all conversation rows for a contact."""
        conn = self.db._get_conn()
        rows = conn.execute(
            "SELECT * FROM conversations WHERE contact_id = ? ORDER BY updated_at DESC",
            (contact_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _quick_sentiment(self, text: str) -> str:
        """Sentiment analysis using TextBlob NLP (upgraded from rule-based)."""
        try:
            from src.toolkit import sentiment_score
            return sentiment_score(text).get("label", "neutral")
        except Exception:
            logger.debug("TextBlob sentiment analysis unavailable, using rule-based fallback")
            # Fallback to simple rule-based if TextBlob unavailable
            lower = text.lower()
            pos_words = {"thanks", "great", "awesome", "love", "perfect", "excellent", "amazing", "appreciate"}
            neg_words = {"frustrated", "disappointed", "terrible", "hate", "worst", "angry", "broken", "refund"}
            pos = sum(1 for w in pos_words if w in lower)
            neg = sum(1 for w in neg_words if w in lower)
            return "positive" if pos > neg else "negative" if neg > pos else "neutral"

    def _detect_signals(self, text: str) -> list[str]:
        """Detect lead signals in a message. Returns list of matched keywords."""
        lower = text.lower()
        return [kw for kw in LEAD_SIGNAL_KEYWORDS if kw in lower]

    def _format_contact_profile(self, contact: dict[str, Any]) -> str:
        """Format contact info for context injection."""
        parts = [f"## Contact: {contact.get('name', 'Unknown')}"]
        if contact.get("company"):
            parts.append(f"Company: {contact['company']}")
        if contact.get("role"):
            parts.append(f"Role: {contact['role']}")
        if contact.get("email"):
            parts.append(f"Email: {contact['email']}")
        if contact.get("x_handle"):
            parts.append(f"X: @{contact['x_handle']}")
        if contact.get("industry"):
            parts.append(f"Industry: {contact['industry']}")
        parts.append(f"Status: {contact.get('status', 'unknown')}")
        parts.append(f"Lead score: {contact.get('lead_score', 0)}")
        parts.append(f"Interactions: {contact.get('interaction_count', 0)}")
        if contact.get("notes"):
            parts.append(f"Notes: {contact['notes']}")
        return "\n".join(parts)

    def _format_current_conversation(
        self, messages: list[dict[str, Any]], conv_id: str,
    ) -> str:
        """Format current conversation messages for context."""
        lines = [f"## Current Conversation ({conv_id})"]

        # If too many messages, show summary + recent
        if len(messages) > MAX_RAW_MESSAGES:
            lines.append(f"[{len(messages)} total messages — showing last {MAX_RAW_MESSAGES}]")
            messages = messages[-MAX_RAW_MESSAGES:]

        for msg in messages:
            role_label = msg["role"].upper()
            ts = msg["created_at"][:16] if msg["created_at"] else ""
            lines.append(f"[{role_label}] ({ts}): {msg['content']}")

        return "\n".join(lines)

    def _format_cross_channel_history(self, convs: list[dict[str, Any]]) -> str:
        """Format other conversations as summaries for context."""
        lines = ["## Previous Conversations"]
        for conv in convs[:10]:  # Cap at 10 other conversations
            channel = conv.get("channel", "?")
            date = (conv.get("created_at") or "")[:10]
            status = conv.get("status", "?")
            msg_count = conv.get("message_count", 0)
            summary = conv.get("summary") or "(no summary yet)"

            lines.append(
                f"- [{channel}] {date} | {msg_count} msgs | {status}\n  {summary}"
            )
        return "\n".join(lines)

    def _get_crm_context(self, contact_id: int) -> str:
        """Pull CRM data (deals, revenue) for context."""
        conn = self.db._get_conn()
        deals = conn.execute(
            "SELECT service, stage, monthly_value, probability "
            "FROM deals WHERE contact_id = ? AND stage NOT IN ('lost', 'churned') "
            "ORDER BY probability DESC",
            (contact_id,),
        ).fetchall()

        revenue = conn.execute(
            "SELECT SUM(amount) as total FROM revenue "
            "WHERE contact_id = ? AND type = 'income'",
            (contact_id,),
        ).fetchone()

        if not deals and not (revenue and revenue["total"]):
            return ""

        lines = ["## CRM Context"]
        if deals:
            lines.append("Active deals:")
            for d in deals:
                lines.append(
                    f"  - {d['service']} | {d['stage']} | "
                    f"${d['monthly_value'] or 0:,.0f}/mo | {d['probability']}% prob"
                )
        if revenue and revenue["total"]:
            lines.append(f"Lifetime revenue: ${revenue['total']:,.2f}")
        return "\n".join(lines)

    def _get_signal_context(self, contact_id: int, conv_id: str | None) -> str:
        """Build lead signal and sentiment context."""
        parts: list[str] = []

        if conv_id:
            signals = self.detect_lead_signals(conv_id)
            if signals["signals"]:
                parts.append(
                    f"Lead signals detected: {', '.join(signals['signals'])} "
                    f"(score: {signals['score']}, assessment: {signals['assessment']})"
                )

            # Sentiment trend
            conn = self.db._get_conn()
            row = conn.execute(
                "SELECT sentiment_trend FROM conversations WHERE id = ?", (conv_id,),
            ).fetchone()
            if row and row["sentiment_trend"]:
                trend = json.loads(row["sentiment_trend"])
                if trend:
                    recent = trend[-5:]
                    parts.append(f"Recent sentiment: {' -> '.join(recent)}")

        if not parts:
            return ""
        return "## Signals & Sentiment\n" + "\n".join(parts)

    def _row_to_conversation(self, row: dict[str, Any]) -> Conversation:
        """Convert a DB row dict to a Conversation dataclass."""
        return Conversation(
            id=row["id"],
            contact_id=row["contact_id"],
            channel=row["channel"],
            subject=row.get("subject"),
            status=row.get("status", "active"),
            sentiment_trend=json.loads(row.get("sentiment_trend") or "[]"),
            lead_score=row.get("lead_score", 0),
            follow_up_reason=row.get("follow_up_reason"),
            follow_up_due=row.get("follow_up_due"),
            summary=row.get("summary"),
            message_count=row.get("message_count", 0),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            metadata=json.loads(row.get("metadata") or "{}"),
        )
