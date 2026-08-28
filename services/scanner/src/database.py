from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Optional


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT NOT NULL,
    item_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT')),
    detected_at_utc TEXT NOT NULL,
    confidence REAL NOT NULL,
    sequence_json TEXT NOT NULL,
    detection_reason TEXT NOT NULL,
    capture_path TEXT,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_results_detected_at ON results(detected_at_utc DESC);
CREATE INDEX IF NOT EXISTS ix_results_category_detected_at
    ON results(category, detected_at_utc DESC);

CREATE TABLE IF NOT EXISTS scanner_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT UNIQUE,
    severity TEXT NOT NULL,
    event_code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at_utc TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_scanner_events_occurred_at
    ON scanner_events(occurred_at_utc DESC);

CREATE TABLE IF NOT EXISTS scanner_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL UNIQUE,
    rule_code TEXT NOT NULL,
    category TEXT NOT NULL,
    streak_length INTEGER NOT NULL,
    result_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    response_status INTEGER,
    response_body TEXT,
    created_at_utc TEXT NOT NULL,
    attempted_at_utc TEXT,
    FOREIGN KEY(result_id) REFERENCES results(id)
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: Optional[datetime] = None) -> str:
    return (value or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ScannerDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def set_state(self, key: str, value: str) -> None:
        now = utc_text()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO scanner_state(state_key, state_value, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value=excluded.state_value,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (key, value, now),
            )

    def get_state(self, key: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state_value FROM scanner_state WHERE state_key=?", (key,)
            ).fetchone()
            return str(row[0]) if row else None

    def insert_result(
        self,
        item_code: str,
        item_name: str,
        category: str,
        confidence: float,
        sequence: list[str],
        detection_reason: str,
        detected_at: Optional[datetime] = None,
        capture_path: Optional[str] = None,
    ) -> int:
        now = utc_text()
        detected = utc_text(detected_at)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO results(
                    item_code, item_name, category, detected_at_utc, confidence,
                    sequence_json, detection_reason, capture_path, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_code,
                    item_name,
                    category,
                    detected,
                    confidence,
                    json.dumps(sequence, ensure_ascii=False),
                    detection_reason,
                    capture_path,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_result_capture(self, result_id: int, capture_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE results SET capture_path=? WHERE id=?", (capture_path, result_id)
            )

    def insert_event(
        self,
        severity: str,
        event_code: str,
        message: str,
        details: dict,
        source_key: Optional[str] = None,
    ) -> int:
        now = utc_text()
        source_key = source_key or f"scanner:{event_code}:{now}"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO scanner_events(
                    source_key, severity, event_code, message, details_json,
                    occurred_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    severity,
                    event_code,
                    message,
                    json.dumps(details, ensure_ascii=False),
                    now,
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id FROM scanner_events WHERE source_key=?", (source_key,)
            ).fetchone()
            return int(row[0])
