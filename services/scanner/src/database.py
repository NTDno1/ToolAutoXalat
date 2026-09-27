from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    round_number INTEGER,
    round_local_date TEXT,
    source_serial TEXT NOT NULL DEFAULT '',
    item_code TEXT NOT NULL,
    item_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT', 'SPECIAL')),
    detected_at_utc TEXT NOT NULL,
    confidence REAL NOT NULL,
    sequence_json TEXT NOT NULL,
    detection_reason TEXT NOT NULL,
    capture_path TEXT,
    created_at_utc TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS scanner_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS betting_signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_serial TEXT NOT NULL,
    round_local_date TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    observed_at_utc TEXT NOT NULL,
    hot_item_code TEXT,
    items_json TEXT NOT NULL,
    UNIQUE(source_serial, round_local_date, round_number)
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

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_results_detected_at ON results(detected_at_utc DESC);
CREATE INDEX IF NOT EXISTS ix_results_category_detected_at
    ON results(category, detected_at_utc DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_results_source_round
    ON results(source_serial, round_local_date, round_number)
    WHERE round_number IS NOT NULL AND round_local_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_scanner_events_occurred_at
    ON scanner_events(occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS ix_betting_signal_snapshots_round
    ON betting_signal_snapshots(round_local_date DESC, round_number DESC);
CREATE INDEX IF NOT EXISTS ix_betting_signal_snapshots_observed
    ON betting_signal_snapshots(observed_at_utc DESC, id DESC);
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
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='results'"
            ).fetchone()
            table_sql = str(row[0]) if row else ""
            if (
                "'SPECIAL'" not in table_sql.upper()
                or "ROUND_NUMBER" not in table_sql.upper()
                or "SOURCE_SERIAL" not in table_sql.upper()
            ):
                connection.executescript(
                    """
                    PRAGMA foreign_keys=OFF;
                    BEGIN IMMEDIATE;
                    DROP TABLE IF EXISTS results_v2;
                    CREATE TABLE results_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        round_number INTEGER,
                        round_local_date TEXT,
                        source_serial TEXT NOT NULL DEFAULT '',
                        item_code TEXT NOT NULL,
                        item_name TEXT NOT NULL,
                        category TEXT NOT NULL CHECK(category IN ('VEGETABLE', 'MEAT', 'SPECIAL')),
                        detected_at_utc TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        sequence_json TEXT NOT NULL,
                        detection_reason TEXT NOT NULL,
                        capture_path TEXT,
                        created_at_utc TEXT NOT NULL
                    );
                    INSERT INTO results_v2(
                        id, round_number, round_local_date, source_serial,
                        item_code, item_name, category, detected_at_utc, confidence,
                        sequence_json, detection_reason, capture_path, created_at_utc
                    )
                    SELECT id, NULL, NULL, '127.0.0.1:5575',
                           item_code, item_name, category, detected_at_utc, confidence,
                           sequence_json, detection_reason, capture_path, created_at_utc
                    FROM results;
                    DROP TABLE results;
                    ALTER TABLE results_v2 RENAME TO results;
                    COMMIT;
                    PRAGMA foreign_keys=ON;
                    """
                )
            connection.executescript(INDEXES)

    def set_state(self, key: str, value: str) -> None:
        self.set_states({key: value})

    def set_states(self, values: dict[str, str]) -> None:
        if not values:
            return
        now = utc_text()
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO scanner_state(state_key, state_value, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value=excluded.state_value,
                    updated_at_utc=excluded.updated_at_utc
                """,
                [(key, value, now) for key, value in values.items()],
            )

    def get_state(self, key: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state_value FROM scanner_state WHERE state_key=?", (key,)
            ).fetchone()
            return str(row[0]) if row else None

    def upsert_betting_signal_snapshot(
        self,
        source_serial: str,
        round_local_date: str,
        round_number: int,
        observed_at_utc: str,
        hot_item_code: Optional[str],
        items: list[dict[str, object]],
    ) -> None:
        """Persist the strongest live HOT/coin observation for one betting round."""
        if round_number <= 0:
            return
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT hot_item_code, items_json
                FROM betting_signal_snapshots
                WHERE source_serial=? AND round_local_date=? AND round_number=?
                """,
                (source_serial, round_local_date, round_number),
            ).fetchone()
            strongest_by_code: dict[str, dict[str, object]] = {}
            if existing:
                try:
                    for item in json.loads(str(existing["items_json"])):
                        code = str(item.get("itemCode", ""))
                        if code:
                            strongest_by_code[code] = dict(item)
                except (TypeError, ValueError, json.JSONDecodeError):
                    strongest_by_code = {}
            for item in items:
                code = str(item.get("itemCode", ""))
                if not code:
                    continue
                previous = strongest_by_code.get(code, {})
                strongest_by_code[code] = {
                    **previous,
                    **item,
                    "itemCode": code,
                    "coinCount": max(
                        int(previous.get("coinCount", 0)),
                        int(item.get("coinCount", 0)),
                    ),
                    "activityPercent": max(
                        int(previous.get("activityPercent", 0)),
                        int(item.get("activityPercent", 0)),
                    ),
                }
            strongest_items = list(strongest_by_code.values())
            strongest_hot = hot_item_code or (
                str(existing["hot_item_code"])
                if existing and existing["hot_item_code"] is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO betting_signal_snapshots(
                    source_serial, round_local_date, round_number,
                    observed_at_utc, hot_item_code, items_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_serial, round_local_date, round_number) DO UPDATE SET
                    observed_at_utc=excluded.observed_at_utc,
                    hot_item_code=excluded.hot_item_code,
                    items_json=excluded.items_json
                """,
                (
                    source_serial,
                    round_local_date,
                    round_number,
                    observed_at_utc,
                    strongest_hot,
                    json.dumps(strongest_items, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def insert_result(
        self,
        item_code: str,
        item_name: str,
        category: str,
        confidence: float,
        sequence: list[str],
        detection_reason: str,
        source_serial: str,
        round_number: Optional[int],
        round_local_date: Optional[str],
        detected_at: Optional[datetime] = None,
        capture_path: Optional[str] = None,
    ) -> int:
        now = utc_text()
        detected = utc_text(detected_at)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO results(
                    round_number, round_local_date, source_serial,
                    item_code, item_name, category, detected_at_utc, confidence,
                    sequence_json, detection_reason, capture_path, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_number,
                    round_local_date,
                    source_serial,
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
            if cursor.rowcount > 0:
                return int(cursor.lastrowid)
            if round_number is not None and round_local_date is not None:
                row = connection.execute(
                    """
                    SELECT id FROM results
                    WHERE source_serial=? AND round_local_date=? AND round_number=?
                    """,
                    (source_serial, round_local_date, round_number),
                ).fetchone()
                if row:
                    return int(row[0])
            raise RuntimeError("Result insert was ignored without an existing round")

    def update_result_capture(self, result_id: int, capture_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE results SET capture_path=? WHERE id=?", (capture_path, result_id)
            )

    def reconcile_result(
        self,
        result_id: int,
        item_code: str,
        item_name: str,
        category: str,
        confidence: float,
        sequence: list[str],
        detection_reason: str,
    ) -> None:
        """Replace a provisional popup classification with verified history data."""
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE results SET
                    item_code=?, item_name=?, category=?, confidence=?,
                    sequence_json=?, detection_reason=?
                WHERE id=?
                """,
                (
                    item_code,
                    item_name,
                    category,
                    confidence,
                    json.dumps(sequence, ensure_ascii=False),
                    detection_reason,
                    result_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Could not reconcile provisional result id={result_id}"
                )

    def synchronize_verified_sequence(
        self,
        source_serial: str,
        round_local_date: str,
        latest_round: int,
        outcomes: list[tuple[str, str, str, float]],
        sequence: list[str],
        anchor_result_id: int,
        round_interval_seconds: float,
    ) -> list[dict[str, object]]:
        """Backfill/correct the eight round rows from a verified app history strip."""
        if latest_round <= 0 or not outcomes:
            return []

        actions: list[dict[str, object]] = []
        now = utc_text()
        serialized_sequence = json.dumps(sequence, ensure_ascii=False)
        with self.connect() as connection:
            anchor_row = connection.execute(
                "SELECT detected_at_utc FROM results WHERE id=?",
                (anchor_result_id,),
            ).fetchone()
            try:
                anchor_detected_at = datetime.fromisoformat(
                    str(anchor_row[0]).replace("Z", "+00:00")
                ) if anchor_row else utc_now()
            except ValueError:
                anchor_detected_at = utc_now()
            newer_detected_at: Optional[datetime] = None

            for offset, (code, name, category, confidence) in enumerate(outcomes[:8]):
                round_number = latest_round - offset
                if round_number <= 0:
                    break
                existing = connection.execute(
                    """
                    SELECT id, item_code, detected_at_utc FROM results
                    WHERE source_serial=? AND round_local_date=? AND round_number=?
                    """,
                    (source_serial, round_local_date, round_number),
                ).fetchone()
                if existing:
                    result_id = int(existing[0])
                    previous_code = str(existing[1])
                    try:
                        existing_detected_at = datetime.fromisoformat(
                            str(existing[2]).replace("Z", "+00:00")
                        )
                    except ValueError:
                        existing_detected_at = anchor_detected_at - timedelta(
                            seconds=max(1.0, round_interval_seconds) * offset
                        )
                    retimed = (
                        newer_detected_at is not None
                        and existing_detected_at >= newer_detected_at
                    )
                    if retimed:
                        existing_detected_at = newer_detected_at - timedelta(
                            milliseconds=1
                        )
                    newer_detected_at = existing_detected_at
                    if previous_code == code:
                        if retimed:
                            connection.execute(
                                "UPDATE results SET detected_at_utc=? WHERE id=?",
                                (utc_text(existing_detected_at), result_id),
                            )
                            actions.append(
                                {
                                    "action": "retimed",
                                    "id": result_id,
                                    "round": round_number,
                                    "code": code,
                                }
                            )
                        continue
                    connection.execute(
                        """
                        UPDATE results SET
                            item_code=?, item_name=?, category=?, confidence=?,
                            sequence_json=?, detection_reason=?, detected_at_utc=?
                        WHERE id=?
                        """,
                        (
                            code,
                            name,
                            category,
                            confidence,
                            serialized_sequence,
                            "history_corrected_verified_8",
                            utc_text(existing_detected_at),
                            result_id,
                        ),
                    )
                    actions.append(
                        {
                            "action": "corrected",
                            "id": result_id,
                            "round": round_number,
                            "previousCode": previous_code,
                            "code": code,
                        }
                    )
                    continue

                estimated_at = anchor_detected_at - timedelta(
                    seconds=max(1.0, round_interval_seconds) * offset
                )
                if newer_detected_at is not None and estimated_at >= newer_detected_at:
                    estimated_at = newer_detected_at - timedelta(milliseconds=1)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO results(
                        round_number, round_local_date, source_serial,
                        item_code, item_name, category, detected_at_utc, confidence,
                        sequence_json, detection_reason, capture_path, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        round_number,
                        round_local_date,
                        source_serial,
                        code,
                        name,
                        category,
                        utc_text(estimated_at),
                        confidence,
                        serialized_sequence,
                        "history_backfilled_verified_8",
                        now,
                    ),
                )
                if cursor.rowcount > 0:
                    newer_detected_at = estimated_at
                    actions.append(
                        {
                            "action": "inserted",
                            "id": int(cursor.lastrowid),
                            "round": round_number,
                            "code": code,
                        }
                    )

        return actions

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
