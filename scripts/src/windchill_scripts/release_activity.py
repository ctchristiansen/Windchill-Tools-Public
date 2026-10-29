"""Summarize what was created, changed and released over the last N days, per product or library.

Good as a weekly status email. Counts parts, documents and CAD documents
modified in the period, plus change notices.

Use from another app:
    from windchill_scripts.release_activity import summarize_activity
    summary = summarize_activity(client, days=7)

Run on its own:
    uv run release-activity [--days 7] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from windchill_api import EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display, first, odata_datetime, parse_datetime, value

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

DAYS = 7
OUTPUT_FILE = "release_activity.txt"

# Life cycle states (internal values) that count as released.
RELEASED_STATES = {"RELEASED", "PRODUCTION"}

log = logging.getLogger(__name__)


@dataclass
class Counts:
    created: int = 0
    changed: int = 0
    released: int = 0


@dataclass
class ActivitySummary:
    start: datetime
    end: datetime
    # (context, type) -> counts
    counts: dict[tuple[str, str], Counts] = field(default_factory=lambda: defaultdict(Counts))
    released: list[tuple[str, str, str, str, str]] = field(default_factory=list)  # context, type, number, version, name
    skipped: list[str] = field(default_factory=list)


def _context(e: dict) -> str:
    return display(first(e, "Context", "ContainerName", default="")) or "(unknown context)"


def summarize_activity(
    client: WindchillClient,
    days: int = DAYS,
    entity_sets: Iterable[EntitySet] = (*endpoints.WORKABLE_SETS, endpoints.CHANGE_NOTICES),
) -> ActivitySummary:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    summary = ActivitySummary(start, end)

    def in_period(e: dict) -> bool:
        modified = parse_datetime(first(e, "LastModified", "ModifiedOn"))
        return bool(modified and modified >= start)

    for es in entity_sets:
        log.info("Reading %ss changed since %s", es.label, f"{start:%Y-%m-%d}")
        try:
            items = list(client.query_where(es.path, f"LastModified ge {odata_datetime(start)}", in_period, expand="Context"))
        except WindchillError:
            # Some releases cannot expand Context on every type; retry without it.
            try:
                items = list(client.query_where(es.path, f"LastModified ge {odata_datetime(start)}", in_period))
            except WindchillError as exc:
                log.warning("Skipping %ss: %s", es.label, exc)
                summary.skipped.append(es.label)
                continue
        for e in items:
            c = summary.counts[(_context(e), es.label)]
            created = parse_datetime(e.get("CreatedOn"))
            if created and created >= start:
                c.created += 1
            else:
                c.changed += 1
            if str(value(e.get("State")) or "").upper() in RELEASED_STATES:
                c.released += 1
                summary.released.append(
                    (
                        _context(e),
                        es.label,
                        display(e.get("Number")),
                        display(first(e, "Version", "Revision")),
                        display(e.get("Name")),
                    )
                )
    summary.released.sort()
    return summary


def format_report(s: ActivitySummary, server: str = "") -> str:
    totals = Counts()
    for c in s.counts.values():
        totals.created += c.created
        totals.changed += c.changed
        totals.released += c.released
    text = header(
        "Release activity summary",
        Server=server,
        Period=f"{s.start:%Y-%m-%d} to {s.end:%Y-%m-%d}",
        Totals=f"{totals.created} created, {totals.changed} changed, {totals.released} released or in a released state",
        Not_read=", ".join(s.skipped) or "(none)",
    )
    rows = [(ctx, t, c.created, c.changed, c.released) for (ctx, t), c in sorted(s.counts.items())]
    text += section("By context", table(rows, ["Context", "Type", "Created", "Changed", "Released"]))
    text += section("Released in this period", table(s.released, ["Context", "Type", "Number", "Version", "Name"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        summary = summarize_activity(client_from_args(args), args.days)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(summary, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
