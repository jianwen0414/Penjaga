"""Durable finding revisions (SQLite, WAL), keyed by the M4 run they belong to.

A run is one M4 episode: ``"<episode>@<episode_start_us>"``. The start keeps the key unique
when M4 restarts and numbers episodes from 0 again.

**Idempotent writes.** Detectors are deterministic and finding IDs derive from their content, so
a restarted service that replays an episode produces the same revisions.
- An identical revision is skipped.
- A *different* document under an existing (run, id, revision) means non-determinism. It is
  counted and logged, and the stored one is kept.

``seq`` orders all revisions ever stored. It is the cursor for the SSE stream (Last-Event-ID).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run TEXT NOT NULL,
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    layer TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at_us INTEGER NOT NULL,
    doc TEXT NOT NULL,
    UNIQUE (run, id, revision)
);
CREATE INDEX IF NOT EXISTS revisions_latest ON revisions (run, id, revision DESC);
CREATE TABLE IF NOT EXISTS checks (
    run TEXT NOT NULL,
    check_id TEXT NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (run, check_id)
);
"""


@dataclass(frozen=True)
class StoredRevision:
    seq: int
    run: str
    doc: dict[str, Any]


class FindingStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.conflicts = 0

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ------------------------------------------------------------------ findings

    def add(self, run: str, docs: Iterable[tuple[dict[str, Any], int]]) -> list[StoredRevision]:
        """Store (finding document, updated_at_us) pairs; the revisions that were new."""
        added: list[StoredRevision] = []
        with self._lock:
            self._con.execute("BEGIN")
            try:
                for doc, updated_us in docs:
                    text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
                    cur = self._con.execute(
                        "INSERT OR IGNORE INTO revisions "
                        "(run, id, revision, layer, status, updated_at_us, doc) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            run,
                            doc["id"],
                            doc["revision"],
                            doc["layer"],
                            doc["status"],
                            updated_us,
                            text,
                        ),
                    )
                    if cur.rowcount == 1:
                        added.append(StoredRevision(int(cur.lastrowid or 0), run, doc))
                        continue
                    (existing,) = self._con.execute(
                        "SELECT doc FROM revisions WHERE run = ? AND id = ? AND revision = ?",
                        (run, doc["id"], doc["revision"]),
                    ).fetchone()
                    if existing != text:
                        self.conflicts += 1
                        log.error(
                            "non-deterministic revision %s r%s in run %s; kept the stored one",
                            doc["id"],
                            doc["revision"],
                            run,
                        )
                self._con.execute("COMMIT")
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
        return added

    def latest(
        self,
        run: str,
        *,
        status: str | None = None,
        layer: str | None = None,
        since_us: int | None = None,
    ) -> list[dict[str, Any]]:
        """The latest revision of each finding in ``run``, newest update first."""
        sql = """
            SELECT r.doc FROM revisions r
            JOIN (SELECT id, MAX(revision) AS revision FROM revisions WHERE run = ? GROUP BY id) m
              ON r.id = m.id AND r.revision = m.revision
            WHERE r.run = ?
        """
        params: list[Any] = [run, run]
        if status is not None:
            sql += " AND r.status = ?"
            params.append(status)
        if layer is not None:
            sql += " AND r.layer = ?"
            params.append(layer)
        if since_us is not None:
            sql += " AND r.updated_at_us >= ?"
            params.append(since_us)
        sql += " ORDER BY r.updated_at_us DESC, r.seq DESC"
        with self._lock:
            rows = self._con.execute(sql, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def history(self, run: str, finding_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT doc FROM revisions WHERE run = ? AND id = ? ORDER BY revision",
                (run, finding_id),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def after(self, seq: int, limit: int = 1000) -> list[StoredRevision]:
        """Revisions stored after cursor ``seq`` (any run), oldest first."""
        with self._lock:
            rows = self._con.execute(
                "SELECT seq, run, doc FROM revisions WHERE seq > ? ORDER BY seq LIMIT ?",
                (seq, limit),
            ).fetchall()
        return [StoredRevision(int(s), r, json.loads(d)) for s, r, d in rows]

    def last_seq(self) -> int:
        with self._lock:
            (value,) = self._con.execute("SELECT COALESCE(MAX(seq), 0) FROM revisions").fetchone()
        return int(value)

    # ------------------------------------------------------------------ guided checks

    def put_check(self, run: str, doc: dict[str, Any]) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO checks (run, check_id, doc) VALUES (?, ?, ?)",
                (run, doc["check_id"], json.dumps(doc, sort_keys=True)),
            )

    def get_check(self, check_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            row = self._con.execute(
                "SELECT run, doc FROM checks WHERE check_id = ?", (check_id,)
            ).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def pending_checks(self, run: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute("SELECT doc FROM checks WHERE run = ?", (run,)).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        return [d for d in docs if d["status"] == "pending"]
