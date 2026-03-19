"""ARCANA AI — Database & Persistent Memory System.

Designed to run on a 10TB NAS with scary-good recall.
This is ARCANA's brain — every interaction, lead, deal, piece of content,
conversation, metric, and lesson learned, stored forever and instantly searchable.

Architecture:
1. SQLite for structured data (contacts, deals, content, metrics, events)
2. Full-text search (FTS5) for instant recall across all text
3. Markdown files (existing memory/) for human-readable knowledge
4. JSON blobs for flexible schema-less data
5. Time-series metrics for trend analysis

The NAS gives us effectively infinite storage. We keep EVERYTHING.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("arcana.database")

DB_DIR = Path("data")
DB_PATH = DB_DIR / "arcana.db"

# ── Column whitelists (derived from CREATE TABLE schemas) ─────────
_CONTACTS_COLUMNS = frozenset({
    "name", "email", "company", "role", "phone", "source", "x_handle",
    "linkedin_url", "website", "industry", "company_size", "location",
    "notes", "tags", "first_seen", "last_contact", "interaction_count",
    "lead_score", "lifetime_value", "status", "metadata",
})

_DEALS_COLUMNS = frozenset({
    "contact_id", "service", "monthly_value", "annual_value", "stage",
    "probability", "source", "source_query", "source_platform",
    "proposal_sent", "proposal_text", "close_date", "lost_reason",
    "notes", "created_at", "updated_at", "metadata",
})

_CONTENT_COLUMNS = frozenset({
    "content_type", "platform", "body", "title", "suit", "hook_strength",
    "x_tweet_id", "x_thread_ids", "impressions", "engagements", "clicks",
    "leads_generated", "revenue_attributed", "posted_at", "created_at",
    "metadata",
})


def _validate_columns(kwargs_keys: set[str], allowed: frozenset[str], table: str) -> None:
    """Raise ValueError if any key is not in the allowed column set."""
    bad = kwargs_keys - allowed
    if bad:
        raise ValueError(f"Invalid column(s) for {table}: {bad}")


class Database:
    """Production-grade persistent storage with full-text search.

    Features:
    - WAL mode for concurrent reads during writes
    - Auto-vacuum to reclaim space
    - Busy timeout for lock contention
    - Periodic integrity checks
    - Backup support
    - Connection health monitoring
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._query_count = 0
        self._error_count = 0
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-64000")     # 64MB cache
            self._conn.execute("PRAGMA synchronous=NORMAL")     # Faster with WAL
            self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        return self._conn

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.execute("PRAGMA optimize")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                logger.warning("Database close optimization failed: %s", exc)
            self._conn.close()
            self._conn = None
            logger.info("Database closed — %d queries, %d errors", self._query_count, self._error_count)

    def backup(self, dest_path: str | Path | None = None) -> Path:
        """Create a backup of the database."""
        dest = Path(dest_path) if dest_path else self.db_path.with_suffix(
            f".backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.db"
        )
        conn = self._get_conn()
        backup_conn = sqlite3.connect(str(dest))
        try:
            conn.backup(backup_conn)
            logger.info("Database backed up to %s", dest)
        finally:
            backup_conn.close()
        return dest

    def vacuum(self) -> None:
        """Run incremental vacuum to reclaim space."""
        conn = self._get_conn()
        with self._lock:
            conn.execute("PRAGMA incremental_vacuum(1000)")
            conn.commit()

    def integrity_check(self) -> bool:
        """Run SQLite integrity check."""
        conn = self._get_conn()
        result = conn.execute("PRAGMA integrity_check").fetchone()
        ok = result[0] == "ok" if result else False
        if not ok:
            logger.error("Database integrity check FAILED: %s", result)
        return ok

    def get_db_stats(self) -> dict[str, Any]:
        """Get database stats for monitoring."""
        conn = self._get_conn()
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return {
            "size_mb": round(size_bytes / 1_000_000, 2),
            "pages": page_count,
            "free_pages": freelist,
            "queries": self._query_count,
            "errors": self._error_count,
        }

    def _init_db(self) -> None:
        """Create all tables and indexes."""
        conn = self._get_conn()

        conn.executescript("""
            -- ══════════════════════════════════════════════════
            -- CONTACTS (every person/company ARCANA has seen)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                company TEXT,
                role TEXT,
                phone TEXT,
                source TEXT,            -- x, reddit, scanner, inbound, referral
                x_handle TEXT,
                linkedin_url TEXT,
                website TEXT,
                industry TEXT,
                company_size TEXT,
                location TEXT,
                notes TEXT,
                tags TEXT,              -- JSON array of tags
                first_seen TEXT NOT NULL DEFAULT (datetime('now')),
                last_contact TEXT,
                interaction_count INTEGER DEFAULT 0,
                lead_score INTEGER DEFAULT 0,
                lifetime_value REAL DEFAULT 0,
                status TEXT DEFAULT 'prospect',  -- prospect, lead, client, churned, inactive
                metadata TEXT           -- JSON blob for anything else
            );

            CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
            CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
            CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);
            CREATE INDEX IF NOT EXISTS idx_contacts_lead_score ON contacts(lead_score DESC);
            CREATE INDEX IF NOT EXISTS idx_contacts_x_handle ON contacts(x_handle);

            -- ══════════════════════════════════════════════════
            -- DEALS (every opportunity in the pipeline)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER REFERENCES contacts(id),
                service TEXT NOT NULL,
                monthly_value REAL DEFAULT 0,
                annual_value REAL DEFAULT 0,
                stage TEXT DEFAULT 'prospect',
                probability INTEGER DEFAULT 10,
                source TEXT,
                source_query TEXT,       -- which scanner query found this
                source_platform TEXT,    -- x, reddit, hn, upwork, etc.
                proposal_sent INTEGER DEFAULT 0,
                proposal_text TEXT,
                close_date TEXT,
                lost_reason TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
            CREATE INDEX IF NOT EXISTS idx_deals_contact ON deals(contact_id);
            CREATE INDEX IF NOT EXISTS idx_deals_service ON deals(service);
            CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at);

            -- ══════════════════════════════════════════════════
            -- INTERACTIONS (every touchpoint with every person)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER REFERENCES contacts(id),
                deal_id INTEGER REFERENCES deals(id),
                channel TEXT NOT NULL,    -- x_reply, x_dm, email, discord, phone, meeting
                direction TEXT NOT NULL,  -- inbound, outbound
                content TEXT,
                sentiment TEXT,          -- positive, neutral, negative
                is_lead_signal INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_channel ON interactions(channel);
            CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at);

            -- ══════════════════════════════════════════════════
            -- CONTENT (every piece of content ARCANA creates)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,  -- tweet, thread, newsletter, blog, ugc, seo_article
                platform TEXT NOT NULL,      -- x, linkedin, reddit, blog, beehiiv
                title TEXT,
                body TEXT NOT NULL,
                suit TEXT,                  -- wands, cups, swords, pentacles
                hook_strength INTEGER,
                x_tweet_id TEXT,
                x_thread_ids TEXT,          -- JSON array
                impressions INTEGER DEFAULT 0,
                engagements INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                leads_generated INTEGER DEFAULT 0,
                revenue_attributed REAL DEFAULT 0,
                posted_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_content_type ON content(content_type);
            CREATE INDEX IF NOT EXISTS idx_content_platform ON content(platform);
            CREATE INDEX IF NOT EXISTS idx_content_posted ON content(posted_at);
            CREATE INDEX IF NOT EXISTS idx_content_leads ON content(leads_generated DESC);

            -- ══════════════════════════════════════════════════
            -- OPPORTUNITIES (everything the scanner finds)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,        -- x_ugc, x_chatbot, reddit, hn, upwork, etc.
                platform TEXT NOT NULL,      -- x, reddit, hackernews, upwork, producthunt
                query_used TEXT,             -- which search query found this
                original_text TEXT NOT NULL,
                author TEXT,
                author_handle TEXT,
                service_match TEXT,
                score INTEGER DEFAULT 0,
                estimated_value REAL DEFAULT 0,
                urgency TEXT,               -- now, soon, exploring
                buyer_type TEXT,            -- business_owner, marketer, founder, agency
                auto_responded INTEGER DEFAULT 0,
                response_text TEXT,
                escalated INTEGER DEFAULT 0,
                converted INTEGER DEFAULT 0,
                deal_id INTEGER REFERENCES deals(id),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_opp_source ON opportunities(source);
            CREATE INDEX IF NOT EXISTS idx_opp_score ON opportunities(score DESC);
            CREATE INDEX IF NOT EXISTS idx_opp_service ON opportunities(service_match);
            CREATE INDEX IF NOT EXISTS idx_opp_converted ON opportunities(converted);
            CREATE INDEX IF NOT EXISTS idx_opp_created ON opportunities(created_at);

            -- ══════════════════════════════════════════════════
            -- REVENUE (every dollar in and out)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS revenue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,        -- stripe, gumroad, affiliate, consulting, ugc, etc.
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                type TEXT NOT NULL,           -- income, expense, refund
                description TEXT,
                contact_id INTEGER REFERENCES contacts(id),
                deal_id INTEGER REFERENCES deals(id),
                stripe_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_revenue_channel ON revenue(channel);
            CREATE INDEX IF NOT EXISTS idx_revenue_type ON revenue(type);
            CREATE INDEX IF NOT EXISTS idx_revenue_created ON revenue(created_at);

            -- ══════════════════════════════════════════════════
            -- METRICS (time-series for trend analysis)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,         -- mrr, leads, conversions, content_posted, etc.
                value REAL NOT NULL,
                dimension TEXT,             -- optional: channel, service, platform
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
            CREATE INDEX IF NOT EXISTS idx_metrics_recorded ON metrics(recorded_at);

            -- ══════════════════════════════════════════════════
            -- EVENTS (everything that happens, ever)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,    -- lead_created, deal_advanced, content_posted, etc.
                category TEXT,              -- scanner, crm, content, revenue, system
                data TEXT,                  -- JSON blob with event details
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

            -- ══════════════════════════════════════════════════
            -- SKILLS (automations ARCANA has built for itself)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                trigger_type TEXT,           -- scheduled, event, manual
                frequency TEXT,              -- hourly, daily, weekly, monthly
                prompt_template TEXT,
                steps TEXT,                  -- JSON array of steps
                times_executed INTEGER DEFAULT 0,
                last_executed TEXT,
                success_rate REAL DEFAULT 0,
                time_saved_hours REAL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT
            );

            -- ══════════════════════════════════════════════════
            -- QUERY PERFORMANCE (which scanner queries work)
            -- ══════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS query_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                platform TEXT NOT NULL,
                category TEXT,
                times_used INTEGER DEFAULT 0,
                opportunities_found INTEGER DEFAULT 0,
                responses_sent INTEGER DEFAULT 0,
                conversions INTEGER DEFAULT 0,
                revenue_attributed REAL DEFAULT 0,
                last_used TEXT,
                score REAL DEFAULT 0,       -- computed: conversions / times_used
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_qp_score ON query_performance(score DESC);
            CREATE INDEX IF NOT EXISTS idx_qp_platform ON query_performance(platform);

            -- ══════════════════════════════════════════════════
            -- FULL-TEXT SEARCH (across all text in the system)
            -- ══════════════════════════════════════════════════
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_memory USING fts5(
                source,         -- which table: contacts, deals, interactions, content, etc.
                source_id,      -- the row ID in the source table
                text,           -- the searchable text
                tokenize='porter unicode61'
            );
        """)

        conn.commit()
        logger.info("Database initialized at %s", self.db_path)

    # ══════════════════════════════════════════════════════════════
    # CONTACTS
    # ══════════════════════════════════════════════════════════════

    def upsert_contact(self, **kwargs: Any) -> int:
        """Create or update a contact. Returns contact ID."""
        _validate_columns(set(kwargs.keys()) - {"id"}, _CONTACTS_COLUMNS, "contacts")
        conn = self._get_conn()
        email = kwargs.get("email", "")
        x_handle = kwargs.get("x_handle", "")

        # Try to find existing
        existing = None
        if email:
            existing = conn.execute("SELECT id FROM contacts WHERE email = ?", (email,)).fetchone()
        if not existing and x_handle:
            existing = conn.execute("SELECT id FROM contacts WHERE x_handle = ?", (x_handle,)).fetchone()

        if existing:
            contact_id = existing["id"]
            updates = {k: v for k, v in kwargs.items() if v is not None and k != "id"}
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                with self._lock:
                    conn.execute(
                        f"UPDATE contacts SET {set_clause}, last_contact = datetime('now') WHERE id = ?",
                        (*updates.values(), contact_id),
                    )
                    conn.commit()
            return contact_id
        else:
            cols = [k for k in kwargs if kwargs[k] is not None]
            vals = [kwargs[k] for k in cols]
            placeholders = ", ".join("?" * len(cols))
            col_names = ", ".join(cols)
            with self._lock:
                cur = conn.execute(
                    f"INSERT INTO contacts ({col_names}) VALUES ({placeholders})", vals,
                )
                conn.commit()
            contact_id = cur.lastrowid

            # Index in FTS
            text = f"{kwargs.get('name', '')} {kwargs.get('company', '')} {kwargs.get('email', '')} {kwargs.get('notes', '')}"
            self._index_fts("contacts", contact_id, text)

            return contact_id

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return dict(row) if row else None

    def find_contacts(self, **filters: Any) -> list[dict[str, Any]]:
        """Query contacts with filters."""
        _validate_columns(set(filters.keys()), _CONTACTS_COLUMNS | {"id"}, "contacts")
        conn = self._get_conn()
        where = []
        values = []
        for k, v in filters.items():
            where.append(f"{k} = ?")
            values.append(v)
        clause = " AND ".join(where) if where else "1=1"
        rows = conn.execute(f"SELECT * FROM contacts WHERE {clause} ORDER BY lead_score DESC", values).fetchall()
        return [dict(r) for r in rows]

    def get_top_leads(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM contacts WHERE status IN ('prospect', 'lead') "
            "ORDER BY lead_score DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # DEALS
    # ══════════════════════════════════════════════════════════════

    def create_deal(self, **kwargs: Any) -> int:
        _validate_columns(set(kwargs.keys()), _DEALS_COLUMNS, "deals")
        conn = self._get_conn()
        cols = [k for k in kwargs if kwargs[k] is not None]
        vals = [kwargs[k] for k in cols]
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(cols)
        with self._lock:
            cur = conn.execute(
                f"INSERT INTO deals ({col_names}) VALUES ({placeholders})", vals,
            )
            conn.commit()
        return cur.lastrowid

    def update_deal(self, deal_id: int, **kwargs: Any) -> None:
        _validate_columns(set(kwargs.keys()), _DEALS_COLUMNS, "deals")
        conn = self._get_conn()
        updates = {k: v for k, v in kwargs.items() if v is not None}
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn.execute(f"UPDATE deals SET {set_clause} WHERE id = ?", (*updates.values(), deal_id))
            conn.commit()

    def get_pipeline(self, stage: str | None = None) -> list[dict[str, Any]]:
        conn = self._get_conn()
        if stage:
            rows = conn.execute(
                "SELECT d.*, c.name, c.email, c.company FROM deals d "
                "LEFT JOIN contacts c ON d.contact_id = c.id "
                "WHERE d.stage = ? ORDER BY d.monthly_value DESC", (stage,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT d.*, c.name, c.email, c.company FROM deals d "
                "LEFT JOIN contacts c ON d.contact_id = c.id "
                "WHERE d.stage NOT IN ('lost', 'churned') "
                "ORDER BY d.probability DESC, d.monthly_value DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pipeline_value(self) -> dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT
                COUNT(*) as total_deals,
                SUM(monthly_value) as total_monthly,
                SUM(monthly_value * probability / 100.0) as weighted_monthly,
                SUM(CASE WHEN stage = 'active' THEN monthly_value ELSE 0 END) as active_mrr
            FROM deals WHERE stage NOT IN ('lost', 'churned')
        """).fetchone()
        return dict(row) if row else {}

    def get_pipeline_by_stage(self) -> dict[str, dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT stage, COUNT(*) as count, SUM(monthly_value) as value
            FROM deals WHERE stage NOT IN ('lost', 'churned')
            GROUP BY stage
        """).fetchall()
        return {r["stage"]: {"count": r["count"], "value": r["value"]} for r in rows}

    # ══════════════════════════════════════════════════════════════
    # INTERACTIONS
    # ══════════════════════════════════════════════════════════════

    def log_interaction(self, contact_id: int, channel: str, direction: str,
                        content: str, **kwargs: Any) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO interactions (contact_id, channel, direction, content, "
                "sentiment, is_lead_signal, deal_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (contact_id, channel, direction, content,
                 kwargs.get("sentiment"), kwargs.get("is_lead_signal", 0),
                 kwargs.get("deal_id"), json.dumps(kwargs.get("metadata", {}))),
            )
            conn.execute(
                "UPDATE contacts SET interaction_count = interaction_count + 1, "
                "last_contact = datetime('now') WHERE id = ?", (contact_id,),
            )
            conn.commit()
        self._index_fts("interactions", cur.lastrowid, content)
        return cur.lastrowid

    def get_interactions(self, contact_id: int, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM interactions WHERE contact_id = ? ORDER BY created_at DESC LIMIT ?",
            (contact_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # CONTENT TRACKING
    # ══════════════════════════════════════════════════════════════

    def log_content(self, content_type: str, platform: str, body: str, **kwargs: Any) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO content (content_type, platform, body, title, suit, "
                "hook_strength, x_tweet_id, posted_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (content_type, platform, body, kwargs.get("title"),
                 kwargs.get("suit"), kwargs.get("hook_strength"),
                 kwargs.get("x_tweet_id"), kwargs.get("posted_at"),
                 json.dumps(kwargs.get("metadata", {}))),
            )
            conn.commit()
        self._index_fts("content", cur.lastrowid, f"{kwargs.get('title', '')} {body}")
        return cur.lastrowid

    def update_content_metrics(self, content_id: int, **metrics: Any) -> None:
        _validate_columns(set(metrics.keys()), _CONTENT_COLUMNS, "content")
        conn = self._get_conn()
        updates = {k: v for k, v in metrics.items() if v is not None}
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._lock:
            conn.execute(f"UPDATE content SET {set_clause} WHERE id = ?", (*updates.values(), content_id))
            conn.commit()

    def get_top_content(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM content ORDER BY leads_generated DESC, engagements DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # OPPORTUNITIES
    # ══════════════════════════════════════════════════════════════

    def log_opportunity(self, source: str, platform: str, original_text: str,
                        score: int, **kwargs: Any) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO opportunities (source, platform, original_text, score, "
                "query_used, author, author_handle, service_match, estimated_value, "
                "urgency, buyer_type, auto_responded, response_text, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, platform, original_text, score,
                 kwargs.get("query_used"), kwargs.get("author"),
                 kwargs.get("author_handle"), kwargs.get("service_match"),
                 kwargs.get("estimated_value", 0), kwargs.get("urgency"),
                 kwargs.get("buyer_type"), kwargs.get("auto_responded", 0),
                 kwargs.get("response_text"), json.dumps(kwargs.get("metadata", {}))),
            )
            conn.commit()
        self._index_fts("opportunities", cur.lastrowid, original_text)
        return cur.lastrowid

    def get_opportunity_stats(self, days: int = 7) -> dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN auto_responded THEN 1 ELSE 0 END) as responded,
                SUM(CASE WHEN escalated THEN 1 ELSE 0 END) as escalated,
                SUM(CASE WHEN converted THEN 1 ELSE 0 END) as converted,
                SUM(estimated_value) as pipeline_value,
                AVG(score) as avg_score
            FROM opportunities
            WHERE created_at >= datetime('now', ?)
        """, (f"-{days} days",)).fetchone()
        return dict(row) if row else {}

    def get_opportunities_by_source(self, days: int = 7) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT source, COUNT(*) as count, AVG(score) as avg_score,
                   SUM(estimated_value) as total_value,
                   SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conversions
            FROM opportunities WHERE created_at >= datetime('now', ?)
            GROUP BY source ORDER BY count DESC
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # REVENUE
    # ══════════════════════════════════════════════════════════════

    def log_revenue(self, channel: str, amount: float, type_: str = "income",
                    description: str = "", **kwargs: Any) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO revenue (channel, amount, type, description, "
                "contact_id, deal_id, stripe_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (channel, amount, type_, description,
                 kwargs.get("contact_id"), kwargs.get("deal_id"),
                 kwargs.get("stripe_id"), json.dumps(kwargs.get("metadata", {}))),
            )
            conn.commit()
        return cur.lastrowid

    def get_revenue_summary(self, days: int = 30) -> dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) as total_income,
                SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) as total_expenses,
                SUM(CASE WHEN type = 'refund' THEN amount ELSE 0 END) as total_refunds,
                COUNT(*) as transaction_count
            FROM revenue WHERE created_at >= datetime('now', ?)
        """, (f"-{days} days",)).fetchone()
        return dict(row) if row else {}

    def get_revenue_by_channel(self, days: int = 30) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT channel, SUM(amount) as total,
                   COUNT(*) as transactions
            FROM revenue WHERE type = 'income'
            AND created_at >= datetime('now', ?)
            GROUP BY channel ORDER BY total DESC
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # METRICS (Time-Series)
    # ══════════════════════════════════════════════════════════════

    def record_metric(self, name: str, value: float, dimension: str = "") -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO metrics (name, value, dimension) VALUES (?, ?, ?)",
                (name, value, dimension or None),
            )
            conn.commit()

    def get_metric_history(self, name: str, days: int = 30, dimension: str = "") -> list[dict[str, Any]]:
        conn = self._get_conn()
        if dimension:
            rows = conn.execute(
                "SELECT value, recorded_at FROM metrics "
                "WHERE name = ? AND dimension = ? "
                "AND recorded_at >= datetime('now', ?) ORDER BY recorded_at",
                (name, dimension, f"-{days} days"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT value, recorded_at FROM metrics "
                "WHERE name = ? AND recorded_at >= datetime('now', ?) ORDER BY recorded_at",
                (name, f"-{days} days"),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_metric_trend(self, name: str, days: int = 7) -> dict[str, Any]:
        """Get trend data for a metric — current, previous period, change."""
        conn = self._get_conn()
        current = conn.execute(
            "SELECT AVG(value) as avg, SUM(value) as total, COUNT(*) as count "
            "FROM metrics WHERE name = ? AND recorded_at >= datetime('now', ?)",
            (name, f"-{days} days"),
        ).fetchone()
        previous = conn.execute(
            "SELECT AVG(value) as avg, SUM(value) as total, COUNT(*) as count "
            "FROM metrics WHERE name = ? "
            "AND recorded_at >= datetime('now', ?) "
            "AND recorded_at < datetime('now', ?)",
            (name, f"-{days * 2} days", f"-{days} days"),
        ).fetchone()
        cur_total = (current["total"] or 0) if current else 0
        prev_total = (previous["total"] or 0) if previous else 0
        change = ((cur_total - prev_total) / prev_total * 100) if prev_total else 0
        return {
            "current": cur_total,
            "previous": prev_total,
            "change_pct": round(change, 1),
            "trend": "up" if change > 0 else "down" if change < 0 else "flat",
        }

    # ══════════════════════════════════════════════════════════════
    # EVENTS (everything that happens)
    # ══════════════════════════════════════════════════════════════

    def log_event(self, event_type: str, category: str = "system", data: dict[str, Any] | None = None) -> int:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO events (event_type, category, data) VALUES (?, ?, ?)",
                (event_type, category, json.dumps(data or {})),
            )
            conn.commit()
        return cur.lastrowid

    def get_events(self, event_type: str | None = None, category: str | None = None,
                   days: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._get_conn()
        where = ["created_at >= datetime('now', ?)"]
        values: list[Any] = [f"-{days} days"]
        if event_type:
            where.append("event_type = ?")
            values.append(event_type)
        if category:
            where.append("category = ?")
            values.append(category)
        clause = " AND ".join(where)
        rows = conn.execute(
            f"SELECT * FROM events WHERE {clause} ORDER BY created_at DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # QUERY PERFORMANCE TRACKING
    # ══════════════════════════════════════════════════════════════

    def track_query_use(self, query_text: str, platform: str, category: str,
                        found: int, responded: int, converted: int = 0) -> None:
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id, times_used, opportunities_found, responses_sent, conversions "
            "FROM query_performance WHERE query_text = ? AND platform = ?",
            (query_text, platform),
        ).fetchone()
        with self._lock:
            if existing:
                conn.execute(
                    "UPDATE query_performance SET "
                    "times_used = times_used + 1, "
                    "opportunities_found = opportunities_found + ?, "
                    "responses_sent = responses_sent + ?, "
                    "conversions = conversions + ?, "
                    "last_used = datetime('now'), "
                    "score = CAST((conversions + ?) AS REAL) / (times_used + 1) "
                    "WHERE id = ?",
                    (found, responded, converted, converted, existing["id"]),
                )
            else:
                score = converted / 1.0 if converted else found / 10.0
                conn.execute(
                    "INSERT INTO query_performance (query_text, platform, category, "
                    "times_used, opportunities_found, responses_sent, conversions, "
                    "last_used, score) VALUES (?, ?, ?, 1, ?, ?, ?, datetime('now'), ?)",
                    (query_text, platform, category, found, responded, converted, score),
                )
            conn.commit()

    def get_top_queries(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM query_performance ORDER BY score DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_dead_queries(self, min_uses: int = 5) -> list[dict[str, Any]]:
        """Queries used many times but never convert."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM query_performance WHERE times_used >= ? AND conversions = 0 "
            "ORDER BY times_used DESC", (min_uses,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # FULL-TEXT SEARCH
    # ══════════════════════════════════════════════════════════════

    def _index_fts(self, source: str, source_id: int, text: str) -> None:
        """Add text to the full-text search index."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO fts_memory (source, source_id, text) VALUES (?, ?, ?)",
                (source, str(source_id), text),
            )
            conn.commit()

    def search(self, query: str, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Full-text search across all stored text."""
        conn = self._get_conn()
        if source:
            rows = conn.execute(
                "SELECT source, source_id, snippet(fts_memory, 2, '[', ']', '...', 32) as snippet, "
                "rank FROM fts_memory WHERE fts_memory MATCH ? AND source = ? "
                "ORDER BY rank LIMIT ?",
                (query, source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source, source_id, snippet(fts_memory, 2, '[', ']', '...', 32) as snippet, "
                "rank FROM fts_memory WHERE fts_memory MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def recall(self, query: str, limit: int = 10) -> str:
        """Search memory and return a formatted string for LLM context injection."""
        results = self.search(query, limit=limit)
        if not results:
            return f"No memories found for: {query}"

        lines = [f"## Memory Recall: '{query}'"]
        for r in results:
            lines.append(f"- [{r['source']}#{r['source_id']}] {r['snippet']}")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════
    # DASHBOARD (for morning reports)
    # ══════════════════════════════════════════════════════════════

    def get_dashboard(self) -> dict[str, Any]:
        """Full dashboard for morning report / status check."""
        conn = self._get_conn()

        contacts = conn.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"]
        active_clients = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE status = 'active'").fetchone()["c"]
        pipeline = self.get_pipeline_value()
        revenue_30d = self.get_revenue_summary(30)
        revenue_7d = self.get_revenue_summary(7)
        opp_stats = self.get_opportunity_stats(7)

        return {
            "contacts_total": contacts,
            "active_clients": active_clients,
            "pipeline": pipeline,
            "revenue_30d": revenue_30d,
            "revenue_7d": revenue_7d,
            "opportunities_7d": opp_stats,
            "mrr_trend": self.get_metric_trend("mrr"),
            "leads_trend": self.get_metric_trend("leads"),
        }

    def format_dashboard(self) -> str:
        """Format dashboard as readable text."""
        d = self.get_dashboard()
        p = d.get("pipeline", {})
        r30 = d.get("revenue_30d", {})
        r7 = d.get("revenue_7d", {})
        opp = d.get("opportunities_7d", {})
        mrr = d.get("mrr_trend", {})

        return (
            f"**ARCANA Dashboard**\n"
            f"Contacts: {d['contacts_total']} | Active clients: {d['active_clients']}\n"
            f"Pipeline: {p.get('total_deals', 0)} deals | "
            f"${p.get('total_monthly', 0) or 0:,.0f}/mo total | "
            f"${p.get('weighted_monthly', 0) or 0:,.0f}/mo weighted\n"
            f"Active MRR: ${p.get('active_mrr', 0) or 0:,.0f}\n"
            f"Revenue (30d): ${r30.get('total_income', 0) or 0:,.0f} income | "
            f"${r30.get('total_expenses', 0) or 0:,.0f} expenses\n"
            f"Revenue (7d): ${r7.get('total_income', 0) or 0:,.0f}\n"
            f"Opportunities (7d): {opp.get('total', 0)} found | "
            f"{opp.get('responded', 0)} responded | "
            f"{opp.get('converted', 0)} converted\n"
            f"MRR trend: {mrr.get('trend', 'N/A')} ({mrr.get('change_pct', 0):+.1f}%)"
        )
