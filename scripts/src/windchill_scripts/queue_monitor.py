"""Report failed and backed-up Windchill queue entries (including publishing queues).

Queues are not exposed through Windchill REST Services, so this reads the
Windchill database directly with a read-only account. Install the driver for
your database: ``uv sync --extra oracle`` or ``uv sync --extra sqlserver``.

The SQL below follows the standard Windchill schema (QueueEntry joined to
ProcessingQueue, status in codeC5). Run it in a SQL client first to confirm
it matches your release.

Use from another app (pass any DB-API connection):
    from windchill_scripts.queue_monitor import queue_status
    status = queue_status(connection)

Run on its own:
    uv run queue-monitor --dsn "host:1521/wind" --db-user wcreadonly
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from windchill_api.cli import setup_logging, write_report
from windchill_api.report import header, section, table

# TODO: database connection details. The password is read from WINDCHILL_DB_PASSWORD or prompted for.
DB_TYPE = "oracle"  # "oracle" or "sqlserver"
DB_DSN = "dbhost.example.com:1521/wind"  # Oracle: host:port/service. SQL Server: an ODBC connection string.
DB_USER = "wcreadonly"

# Alert thresholds.
MAX_READY = 500  # entries waiting in one queue
OUTPUT_FILE = "queue_status.txt"

PROCESSING_QUEUE_SQL = """
SELECT q.name, e.codeC5, COUNT(*)
FROM QueueEntry e JOIN ProcessingQueue q ON e.idA3A5 = q.idA2A2
GROUP BY q.name, e.codeC5
"""

SCHEDULE_QUEUE_SQL = """
SELECT q.name, e.codeC5, COUNT(*)
FROM ScheduleQueueEntry e JOIN ScheduleQueue q ON e.idA3A5 = q.idA2A2
GROUP BY q.name, e.codeC5
"""

FAILED_STATUSES = {"FAILED", "SEVERE", "NONEVENTFAILED"}

log = logging.getLogger(__name__)


@dataclass
class QueueStatus:
    # queue name -> status -> count
    counts: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    errors: list[str] = field(default_factory=list)

    def failed(self) -> list[tuple[str, int]]:
        return [(q, n) for q, s in self.counts.items() if (n := sum(s.get(x, 0) for x in FAILED_STATUSES))]

    def backlog(self, max_ready: int = MAX_READY) -> list[tuple[str, int]]:
        return [(q, s["READY"]) for q, s in self.counts.items() if s.get("READY", 0) > max_ready]


def queue_status(connection: Any, queries: tuple[str, ...] = (PROCESSING_QUEUE_SQL, SCHEDULE_QUEUE_SQL)) -> QueueStatus:
    """Count entries per queue and status using an open DB-API connection."""
    status = QueueStatus()
    for sql in queries:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            for name, code, count in cursor.fetchall():
                status.counts[str(name)][str(code).upper()] = int(count)
        except Exception as exc:  # driver-specific error classes
            log.warning("Query failed: %s", exc)
            status.errors.append(str(exc))
        finally:
            cursor.close()
    return status


def connect(db_type: str, dsn: str, user: str, password: str) -> Any:
    if db_type == "oracle":
        import oracledb

        return oracledb.connect(user=user, password=password, dsn=dsn)
    if db_type == "sqlserver":
        import pyodbc

        return pyodbc.connect(f"{dsn};UID={user};PWD={password}", readonly=True)
    raise ValueError(f"Unknown database type {db_type!r}")


def format_report(status: QueueStatus, max_ready: int = MAX_READY) -> str:
    failed, backlog = status.failed(), status.backlog(max_ready)
    text = header(
        "Windchill queue status",
        Queues=len(status.counts),
        Alerts=f"{len(failed)} queues with failed entries, {len(backlog)} with more than {max_ready} waiting",
    )
    if status.errors:
        text += section("Query errors", "".join(f"  {e}\n" for e in status.errors))
    text += section("Queues with failed entries", table(sorted(failed, key=lambda r: -r[1]), ["Queue", "Failed/Severe"]))
    text += section(f"Queues with more than {max_ready} waiting", table(backlog, ["Queue", "Ready"]))
    statuses = sorted({s for c in status.counts.values() for s in c})
    rows = [(q, *(c.get(s, 0) for s in statuses)) for q, c in sorted(status.counts.items())]
    text += section("All queues", table(rows, ["Queue", *statuses]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-type", choices=["oracle", "sqlserver"], default=DB_TYPE)
    parser.add_argument("--dsn", default=os.environ.get("WINDCHILL_DB_DSN", DB_DSN))
    parser.add_argument("--db-user", default=os.environ.get("WINDCHILL_DB_USER", DB_USER))
    parser.add_argument("--max-ready", type=int, default=MAX_READY)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    password = os.environ.get("WINDCHILL_DB_PASSWORD") or getpass.getpass("Database password: ")
    try:
        connection = connect(args.db_type, args.dsn, args.db_user, password)
    except ImportError:
        log.error("Database driver not installed. Run: uv sync --extra %s", args.db_type)
        return 1
    try:
        status = queue_status(connection)
    finally:
        connection.close()
    write_report(args.output, format_report(status, args.max_ready))
    # Non-zero exit when something needs attention, so it can drive alerts from a scheduler.
    return 2 if status.failed() or status.backlog(args.max_ready) or status.errors else 0


if __name__ == "__main__":
    sys.exit(main())
