from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read the scanner heartbeat from SQLite.")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with sqlite3.connect(args.database, timeout=2) as connection:
        rows = dict(
            connection.execute(
                "SELECT state_key, state_value FROM scanner_state "
                "WHERE state_key IN "
                "('scanner_status', 'scanner_heartbeat_utc', 'scanner_source_serial')"
            ).fetchall()
        )

    print(
        json.dumps(
            {
                "status": rows.get("scanner_status"),
                "lastHeartbeatUtc": rows.get("scanner_heartbeat_utc"),
                "sourceSerial": rows.get("scanner_source_serial"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
