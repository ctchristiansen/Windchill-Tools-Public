"""Show open problem reports, change requests, change notices and change tasks by state and age.

Flags change objects that have not been touched for longer than a threshold,
which usually means they are stuck in review.

Use from another app:
    from windchill_scripts.change_aging import find_open_changes
    changes = find_open_changes(client)

Run on its own:
    uv run change-aging [--stale-days 30] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from windchill_api import EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import age_days, display, first, parse_datetime, value

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

STALE_DAYS = 30
OUTPUT_FILE = "change_aging.txt"

# Life cycle states (internal values) that mean a change object is finished.
CLOSED_STATES = {"RESOLVED", "CANCELLED", "CANCELED", "CLOSED", "COMPLETED", "REJECTED", "IMPLEMENTED"}

log = logging.getLogger(__name__)


@dataclass
class ChangeObject:
    type: str
    number: str
    name: str
    state: str
    created: datetime | None
    modified: datetime | None
    creator: str

    @property
    def age(self) -> float | None:
        return age_days(self.created)

    @property
    def idle(self) -> float | None:
        return age_days(self.modified or self.created)


def is_open(entity: dict) -> bool:
    return str(value(entity.get("State")) or "").upper() not in CLOSED_STATES


def find_open_changes(
    client: WindchillClient, entity_sets: Iterable[EntitySet] = endpoints.CHANGE_SETS
) -> tuple[list[ChangeObject], list[str]]:
    """Open change objects, plus the labels of any types the server did not answer for."""
    changes, skipped = [], []
    for es in entity_sets:
        log.info("Reading %ss", es.label)
        try:
            for e in client.query(es.path):
                if not is_open(e):
                    continue
                changes.append(
                    ChangeObject(
                        type=es.label,
                        number=display(e.get("Number")),
                        name=display(e.get("Name")),
                        state=display(e.get("State")),
                        created=parse_datetime(e.get("CreatedOn")),
                        modified=parse_datetime(first(e, "LastModified", "ModifiedOn")),
                        creator=display(first(e, "CreatedBy", "Creator")),
                    )
                )
        except WindchillError as exc:
            # Not every site uses every change type (e.g. variances).
            log.warning("Skipping %ss: %s", es.label, exc)
            skipped.append(es.label)
    changes.sort(key=lambda c: -(c.idle or 0))
    return changes, skipped


def _bucket(days: float | None) -> str:
    if days is None:
        return "?"
    for limit, label in ((7, "< 1 wk"), (30, "1-4 wk"), (90, "1-3 mo"), (180, "3-6 mo"), (365, "6-12 mo")):
        if days < limit:
            return label
    return "> 1 yr"


BUCKETS = ["< 1 wk", "1-4 wk", "1-3 mo", "3-6 mo", "6-12 mo", "> 1 yr", "?"]


def format_report(changes: list[ChangeObject], skipped: list[str], server: str = "", stale_days: float = STALE_DAYS) -> str:
    stale = [c for c in changes if (c.idle or 0) >= stale_days]
    text = header(
        "Open change objects",
        Server=server,
        Open=len(changes),
        Stale=f"{len(stale)} untouched for {stale_days:g}+ days",
        Not_read=", ".join(skipped) or "(none)",
    )
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(BUCKETS, 0))
    for c in changes:
        counts[(c.type, c.state)][_bucket(c.age)] += 1
    rows = [(t, s, *b.values(), sum(b.values())) for (t, s), b in sorted(counts.items())]
    text += section("Count by type, state and age since created", table(rows, ["Type", "State", *BUCKETS, "Total"]))
    rows = [
        (
            c.type,
            c.number,
            c.state,
            f"{c.idle:.0f}" if c.idle is not None else "?",
            f"{c.age:.0f}" if c.age is not None else "?",
            c.creator,
            c.name,
        )
        for c in stale
    ]
    text += section(
        f"Untouched for {stale_days:g}+ days (most idle first)",
        table(rows, ["Type", "Number", "State", "Idle days", "Age days", "Creator", "Name"]),
    )
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--stale-days", type=float, default=STALE_DAYS)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        changes, skipped = find_open_changes(client_from_args(args))
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(changes, skipped, args.server, args.stale_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
