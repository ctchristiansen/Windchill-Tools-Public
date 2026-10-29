"""Report every checked-out part, document and CAD document, who has it and for how long.

Windchill does not allow filtering on checkout state, so this reads every
object of each type and checks locally. On a large system that takes a while.

Use from another app:
    from windchill_scripts.checked_out_report import find_checked_out
    items = find_checked_out(client)

Run on its own:
    uv run checked-out-report [--min-days 30] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from windchill_api import EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import age_days, display, first, parse_datetime
from windchill_api.workable import checked_out_by, is_checked_out

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "checked_out_objects.txt"

log = logging.getLogger(__name__)


@dataclass
class CheckedOutObject:
    type: str
    id: str
    number: str
    name: str
    version: str
    checked_out_by: str
    since: datetime | None
    container: str

    @property
    def days(self) -> float | None:
        return age_days(self.since)


def find_checked_out(
    client: WindchillClient,
    entity_sets: Iterable[EntitySet] = endpoints.WORKABLE_SETS,
    *,
    user: str | None = None,
    min_days: float = 0,
) -> list[CheckedOutObject]:
    """Checked-out objects, oldest first. ``user`` limits it to one user's checkouts."""
    found = []
    for es in entity_sets:
        log.info("Reading %ss", es.label)
        for e in client.query_where(es.path, None, is_checked_out):
            item = CheckedOutObject(
                type=es.label,
                id=e.get("ID", ""),
                number=display(e.get("Number")),
                name=display(e.get("Name")),
                version=display(first(e, "Version", "Revision")),
                checked_out_by=checked_out_by(e),
                # The original's last-modified time is when it was checked out.
                since=parse_datetime(first(e, "LastModified", "ModifiedOn")),
                container=display(first(e, "ContainerName", "Context")),
            )
            if user and item.checked_out_by.lower() != user.lower():
                continue
            if min_days and (item.days or 0) < min_days:
                continue
            found.append(item)
    found.sort(key=lambda i: i.since or datetime.now(timezone.utc))
    return found


def format_report(items: list[CheckedOutObject], server: str = "", min_days: float = 0) -> str:
    by_user = Counter(i.checked_out_by or "(unknown)" for i in items)
    text = header(
        "Checked-out objects",
        Server=server,
        Minimum_age=f"{min_days:g} days" if min_days else "any",
        Found=f"{len(items)} objects",
    )
    text += section("By user", table(by_user.most_common(), ["User", "Objects"]))
    rows = [
        (
            f"{i.days:.0f}" if i.days is not None else "?",
            i.checked_out_by,
            i.type,
            i.number,
            i.version,
            i.name,
            i.container,
        )
        for i in items
    ]
    text += section("Objects (oldest first)", table(rows, ["Days", "User", "Type", "Number", "Version", "Name", "Context"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--min-days", type=float, default=0, help="Only report checkouts at least this old")
    parser.add_argument("--user", help="Only report this user's checkouts")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        items = find_checked_out(client_from_args(args), user=args.user, min_days=args.min_days)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(items, args.server, args.min_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
