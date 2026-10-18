"""The Core API's durable state (SQLite, WAL).

Everything here is linked to one house and its address, so it is **personal data** under the amended
PDPA. Three tables exist for that reason:
- ``consent``: every grant or withdrawal per layer, with the consent text version the owner saw
  (General and Notice & Choice principles).
- ``disclosures``: everything that leaves the house's data space (a ticket to management, a
  booking to a vendor, a call to an LLM provider), with recipient, fields, actor and basis
  (Disclosure principle).
- ``retention``: days to keep each data class, enforced by ``purge`` (Retention principle).

Findings are stored per run (one stream-service episode, keyed by its ``episode_id``) and revision,
idempotently, as detection does. ``case_actions`` records what people did on a case (dismiss, and
later steps' bookings and tickets); cases themselves are derived from findings and these actions.
``outbox`` is the ordered event log behind ``GET /api/v1/events``.
All times are house time (RFC 3339 with offset).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run TEXT NOT NULL,
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    layer TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    doc TEXT NOT NULL,
    UNIQUE (run, id, revision)
);
CREATE INDEX IF NOT EXISTS findings_latest ON findings (run, id, revision DESC);
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    episode_start TEXT NOT NULL,
    seen_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS consent (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    text_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    house_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS disclosures (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    house_time TEXT NOT NULL,
    actor TEXT NOT NULL,
    recipient TEXT NOT NULL,
    purpose TEXT NOT NULL,
    fields TEXT NOT NULL,
    basis TEXT NOT NULL,
    ref TEXT
);
CREATE TABLE IF NOT EXISTS retention (
    data_class TEXT PRIMARY KEY,
    days INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    house_time TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run TEXT NOT NULL,
    case_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    house_time TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS case_actions_run ON case_actions (run, seq);
CREATE TABLE IF NOT EXISTS llm_explanations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created TEXT NOT NULL,
    doc TEXT NOT NULL,
    UNIQUE (case_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    chips TEXT NOT NULL,
    source TEXT NOT NULL,
    model TEXT,
    house_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_messages_conversation ON chat_messages (conversation_id, seq);
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    id TEXT NOT NULL UNIQUE,
    run TEXT NOT NULL,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_kind ON records (kind, run, case_id);
CREATE TABLE IF NOT EXISTS confirmations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,
    created TEXT NOT NULL,
    expires TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Days each data class is kept (house time). Findings and disclosures make up the home's history
# and the record of what was shared; the event log only feeds live screens.
DEFAULT_RETENTION_DAYS = {"findings": 730, "disclosures": 730, "events": 30, "chat": 90}

PERSONAL_TABLES = (
    "findings",
    "episodes",
    "consent",
    "disclosures",
    "case_actions",
    "llm_explanations",
    "chat_messages",
    "records",
    "confirmations",
    "outbox",
    "kv",
)


@dataclass(frozen=True)
class OutboxEvent:
    seq: int
    kind: str
    house_time: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CaseAction:
    seq: int
    case_id: str
    kind: str
    actor: str
    house_time: str
    detail: dict[str, Any]


def _record(row: Any) -> dict[str, Any]:
    return {
        "kind": row[0],
        "id": row[1],
        "run": row[2],
        "case_id": row[3],
        "status": row[4],
        "created": row[5],
        "updated": row[6],
        **json.loads(row[7]),
    }


def _dumps(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


class ApiStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.conflicts = 0
        with self._lock:
            for data_class, days in DEFAULT_RETENTION_DAYS.items():
                self._con.execute(
                    "INSERT OR IGNORE INTO retention (data_class, days) VALUES (?, ?)",
                    (data_class, days),
                )

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ------------------------------------------------------------------ findings

    def add_findings(self, run: str, docs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store finding revisions under ``run``; the ones that were new, in order.

        An identical revision is skipped. A *different* document under an existing
        (run, id, revision) would mean detection is non-deterministic: it is counted and the
        stored one kept, as detection's own store does.
        """
        added: list[dict[str, Any]] = []
        with self._lock:
            self._con.execute("BEGIN")
            try:
                for doc in docs:
                    text = _dumps(doc)
                    cur = self._con.execute(
                        "INSERT OR IGNORE INTO findings "
                        "(run, id, revision, layer, status, updated_at, doc) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            run,
                            doc["id"],
                            doc["revision"],
                            doc["layer"],
                            doc["status"],
                            doc["updated_at"],
                            text,
                        ),
                    )
                    if cur.rowcount == 1:
                        added.append(doc)
                        continue
                    (existing,) = self._con.execute(
                        "SELECT doc FROM findings WHERE run = ? AND id = ? AND revision = ?",
                        (run, doc["id"], doc["revision"]),
                    ).fetchone()
                    if existing != text:
                        self.conflicts += 1
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
        return added

    def latest_findings(self, run: str, *, layer: str | None = None) -> list[dict[str, Any]]:
        """Latest revision of each finding in ``run``, newest update first."""
        sql = """
            SELECT f.doc FROM findings f
            JOIN (SELECT id, MAX(revision) AS revision FROM findings WHERE run = ? GROUP BY id) m
              ON f.id = m.id AND f.revision = m.revision
            WHERE f.run = ?
        """
        params: list[Any] = [run, run]
        if layer is not None:
            sql += " AND f.layer = ?"
            params.append(layer)
        sql += " ORDER BY f.updated_at DESC, f.seq DESC"
        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def all_revisions(self, run: str) -> list[dict[str, Any]]:
        """Every stored revision in ``run``, in arrival order."""
        with self._lock:
            rows = self._con.execute(
                "SELECT doc FROM findings WHERE run = ? ORDER BY seq", (run,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def finding_history(self, run: str, finding_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT doc FROM findings WHERE run = ? AND id = ? ORDER BY revision",
                (run, finding_id),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ------------------------------------------------------------------ episodes and cursor

    def note_episode(self, episode_id: str, episode_start: str) -> bool:
        """Record an episode; True if it was not known before."""
        with self._lock:
            cur = self._con.execute(
                "INSERT OR IGNORE INTO episodes (episode_id, episode_start, seen_seq) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(seen_seq), 0) + 1 FROM episodes))",
                (episode_id, episode_start),
            )
            return cur.rowcount == 1

    def current_episode(self) -> tuple[str, str] | None:
        """(episode_id, episode_start) of the most recently seen episode."""
        with self._lock:
            row = self._con.execute(
                "SELECT episode_id, episode_start FROM episodes ORDER BY seen_seq DESC LIMIT 1"
            ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def get_value(self, key: str) -> str | None:
        with self._lock:
            row = self._con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_value(self, key: str, value: str) -> None:
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))

    # ------------------------------------------------------------------ consent

    def record_consent(
        self, layer: str, enabled: bool, text_version: str, actor: str, house_time: str
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO consent (layer, enabled, text_version, actor, house_time) "
                "VALUES (?, ?, ?, ?, ?)",
                (layer, int(enabled), text_version, actor, house_time),
            )

    def consent_state(self) -> dict[str, dict[str, Any]]:
        """Latest consent decision per layer."""
        with self._lock:
            rows = self._con.execute(
                "SELECT layer, enabled, text_version, actor, house_time FROM consent c "
                "WHERE seq = (SELECT MAX(seq) FROM consent WHERE layer = c.layer)"
            ).fetchall()
        return {
            r[0]: {"enabled": bool(r[1]), "text_version": r[2], "actor": r[3], "decided_at": r[4]}
            for r in rows
        }

    def consent_history(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT layer, enabled, text_version, actor, house_time FROM consent ORDER BY seq"
            ).fetchall()
        return [
            {
                "layer": r[0],
                "enabled": bool(r[1]),
                "text_version": r[2],
                "actor": r[3],
                "decided_at": r[4],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ case actions

    def add_case_action(
        self,
        run: str,
        case_id: str,
        *,
        kind: str,
        actor: str,
        house_time: str,
        detail: dict[str, Any],
    ) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO case_actions (run, case_id, kind, actor, house_time, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run, case_id, kind, actor, house_time, _dumps(detail)),
            )
            return int(cur.lastrowid or 0)

    def case_actions(self, run: str) -> list[CaseAction]:
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, case_id, kind, actor, house_time, detail FROM case_actions "
                "WHERE run = ? ORDER BY seq",
                (run,),
            ).fetchall()
        return [CaseAction(int(r[0]), r[1], r[2], r[3], r[4], json.loads(r[5])) for r in rows]

    # ------------------------------------------------------------------ action records
    # Tickets, bookings and guided checks: one JSON document each, with an ID, the case it
    # belongs to and a status. Their history is on the case timeline (``case_actions``).

    def put_record(
        self,
        kind: str,
        record_id: str,
        *,
        run: str,
        case_id: str,
        status: str,
        house_time: str,
        doc: dict[str, Any],
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO records (kind, id, run, case_id, status, created, updated, doc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status = excluded.status, updated = excluded.updated, doc = excluded.doc",
                (kind, record_id, run, case_id, status, house_time, house_time, _dumps(doc)),
            )

    def record(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._con.execute(
                "SELECT kind, id, run, case_id, status, created, updated, doc FROM records "
                "WHERE id = ?",
                (record_id,),
            ).fetchone()
        return None if row is None else _record(row)

    def records(
        self,
        kind: str,
        *,
        run: str | None = None,
        case_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT kind, id, run, case_id, status, created, updated, doc FROM records "
            "WHERE kind = ?"
        )
        params: list[Any] = [kind]
        for column, value in (("run", run), ("case_id", case_id), ("status", status)):
            if value is not None:
                sql += f" AND {column} = ?"
                params.append(value)
        with self._lock:
            rows = self._con.execute(sql + " ORDER BY seq", params).fetchall()
        return [_record(r) for r in rows]

    # ------------------------------------------------------------------ confirmations

    def add_confirmation(
        self, token: str, *, case_id: str, kind: str, params: str, created: str, expires: str
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO confirmations (token, case_id, kind, params, created, expires) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token, case_id, kind, params, created, expires),
            )

    def use_confirmation(self, token: str) -> dict[str, Any] | None:
        """The confirmation behind ``token``, marked used in the same step; None if unknown
        or already used (single use)."""
        with self._lock:
            row = self._con.execute(
                "SELECT case_id, kind, params, expires FROM confirmations "
                "WHERE token = ? AND used = 0",
                (token,),
            ).fetchone()
            if row is None:
                return None
            self._con.execute("UPDATE confirmations SET used = 1 WHERE token = ?", (token,))
        return {"case_id": row[0], "kind": row[1], "params": row[2], "expires": row[3]}

    # ------------------------------------------------------------------ agent

    def get_explanation(self, case_id: str, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._con.execute(
                "SELECT doc FROM llm_explanations WHERE case_id = ? AND fingerprint = ?",
                (case_id, fingerprint),
            ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def put_explanation(
        self, case_id: str, fingerprint: str, created: str, doc: dict[str, Any]
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO llm_explanations (case_id, fingerprint, created, doc) "
                "VALUES (?, ?, ?, ?)",
                (case_id, fingerprint, created, _dumps(doc)),
            )

    def add_chat_message(
        self,
        conversation_id: str,
        *,
        role: str,
        text: str,
        chips: list[dict[str, Any]],
        source: str,
        model: str | None,
        house_time: str,
    ) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO chat_messages "
                "(conversation_id, role, text, chips, source, model, house_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, role, text, _dumps(chips), source, model, house_time),
            )
            return int(cur.lastrowid or 0)

    def chat_history(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """The last ``limit`` messages of a conversation, oldest first."""
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, role, text, chips, source, model, house_time FROM chat_messages "
                "WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [
            {
                "seq": r[0],
                "role": r[1],
                "text": r[2],
                "chips": json.loads(r[3]),
                "source": r[4],
                "model": r[5],
                "house_time": r[6],
            }
            for r in reversed(rows)
        ]

    # ------------------------------------------------------------------ disclosures

    def log_disclosure(
        self,
        *,
        house_time: str,
        actor: str,
        recipient: str,
        purpose: str,
        fields: list[str],
        basis: str,
        ref: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO disclosures "
                "(house_time, actor, recipient, purpose, fields, basis, ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (house_time, actor, recipient, purpose, _dumps(fields), basis, ref),
            )
            return int(cur.lastrowid or 0)

    def disclosures(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, house_time, actor, recipient, purpose, fields, basis, ref "
                "FROM disclosures ORDER BY seq"
            ).fetchall()
        return [
            {
                "seq": r[0],
                "house_time": r[1],
                "actor": r[2],
                "recipient": r[3],
                "purpose": r[4],
                "fields": json.loads(r[5]),
                "basis": r[6],
                "ref": r[7],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ outbox (live events)

    def publish(self, kind: str, house_time: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO outbox (kind, house_time, payload) VALUES (?, ?, ?)",
                (kind, house_time, _dumps(payload)),
            )
            return int(cur.lastrowid or 0)

    def events_after(self, seq: int, limit: int = 500) -> list[OutboxEvent]:
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, kind, house_time, payload FROM outbox WHERE seq > ? ORDER BY seq "
                "LIMIT ?",
                (seq, limit),
            ).fetchall()
        return [OutboxEvent(int(r[0]), r[1], r[2], json.loads(r[3])) for r in rows]

    def last_event_seq(self) -> int:
        with self._lock:
            (value,) = self._con.execute("SELECT COALESCE(MAX(seq), 0) FROM outbox").fetchone()
        return int(value)

    # ------------------------------------------------------------------ retention

    def retention(self) -> dict[str, int]:
        with self._lock:
            rows = self._con.execute("SELECT data_class, days FROM retention").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def set_retention(self, data_class: str, days: int) -> None:
        if data_class not in DEFAULT_RETENTION_DAYS:
            raise KeyError(data_class)
        if days < 1:
            raise ValueError("retention must be at least one day")
        with self._lock:
            self._con.execute(
                "UPDATE retention SET days = ? WHERE data_class = ?", (days, data_class)
            )

    def purge(self, now: datetime) -> dict[str, int]:
        """Delete rows older than their class's retention at house time ``now``; counts deleted.

        Findings are judged by ``updated_at``, so a finding still being updated is kept whole.
        Case actions and stored explanations are part of the home's history and follow the
        findings class; chat messages have their own class.
        """
        days = self.retention()
        deleted = dict.fromkeys(DEFAULT_RETENTION_DAYS, 0)
        targets = [
            ("findings", "findings", "updated_at"),
            ("findings", "case_actions", "house_time"),
            ("findings", "llm_explanations", "created"),
            ("findings", "records", "updated"),
            ("events", "confirmations", "created"),
            ("chat", "chat_messages", "house_time"),
            ("disclosures", "disclosures", "house_time"),
            ("events", "outbox", "house_time"),
        ]
        with self._lock:
            for data_class, table, column in targets:
                cutoff = now - timedelta(days=days[data_class])
                stale = [
                    row[0]
                    for row in self._con.execute(f"SELECT seq, {column} FROM {table}")
                    if datetime.fromisoformat(row[1]) < cutoff
                ]
                self._con.executemany(
                    f"DELETE FROM {table} WHERE seq = ?",
                    [(s,) for s in stale],
                )
                deleted[data_class] += len(stale)
        return deleted

    def erase_all(self) -> None:
        """Account closure: delete every personal record this service holds."""
        with self._lock:
            for table in PERSONAL_TABLES:
                self._con.execute(f"DELETE FROM {table}")
