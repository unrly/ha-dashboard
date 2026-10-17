"""SQLite storage for cases, summaries, and scan history."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .cases import Case

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, docs TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS summaries (
  id TEXT PRIMARY KEY, docs_key TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT NOT NULL, finished TEXT, ok INTEGER, message TEXT,
  found INTEGER, kept INTEGER, new INTEGER
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # --- key/value
    def get(self, k: str) -> Optional[str]:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None

    def put(self, k: str, v: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # --- cases
    def upsert_cases(self, cases: list[Case], scan_time: str) -> list[str]:
        """Save cases seen in this scan; returns the ids that are new."""
        new = []
        with self.lock, self.db:
            for c in cases:
                row = self.db.execute("SELECT id FROM cases WHERE id=?", (c.id,)).fetchone()
                if row:
                    self.db.execute("UPDATE cases SET data=?, last_seen=?, active=1 WHERE id=?",
                                    (c.model_dump_json(), scan_time, c.id))
                else:
                    self.db.execute("INSERT INTO cases(id,data,first_seen,last_seen,active) VALUES(?,?,?,?,1)",
                                    (c.id, c.model_dump_json(), scan_time, scan_time))
                    new.append(c.id)
            ids = [c.id for c in cases]
            q = "UPDATE cases SET active=0 WHERE active=1"
            if ids:
                q += f" AND id NOT IN ({','.join('?' * len(ids))})"
            self.db.execute(q, ids)
        return new

    def set_docs(self, case_id: str, docs: list[dict]) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE cases SET docs=? WHERE id=?", (json.dumps(docs), case_id))

    def case(self, case_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return self._row(row) if row else None

    def cases(self, keep_closed_days: int) -> list[dict]:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_closed_days)).isoformat()
        rows = self.db.execute("SELECT * FROM cases WHERE active=1 OR last_seen>=? ORDER BY last_seen DESC",
                               (cutoff,)).fetchall()
        return [self._row(r) for r in rows]

    def _row(self, r: sqlite3.Row) -> dict:
        d = json.loads(r["data"])
        d.update(first_seen=r["first_seen"], last_seen=r["last_seen"], active=bool(r["active"]),
                 docs=json.loads(r["docs"]))
        s = self.summary(r["id"])
        d["summary"] = s
        return d

    # --- summaries
    def summary(self, case_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT data, docs_key, created FROM summaries WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        return {**json.loads(row["data"]), "docs_key": row["docs_key"], "created": row["created"]}

    def save_summary(self, case_id: str, docs_key: str, data: dict) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO summaries(id,docs_key,data,created) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET docs_key=excluded.docs_key, data=excluded.data, created=excluded.created",
                (case_id, docs_key, json.dumps(data), now()))

    # --- scans
    def start_scan(self) -> int:
        with self.lock, self.db:
            return self.db.execute("INSERT INTO scans(started) VALUES(?)", (now(),)).lastrowid

    def finish_scan(self, scan_id: int, ok: bool, message: str, found=0, kept=0, new=0) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE scans SET finished=?, ok=?, message=?, found=?, kept=?, new=? WHERE id=?",
                            (now(), int(ok), message, found, kept, new, scan_id))

    def last_scan(self) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM scans WHERE finished IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
