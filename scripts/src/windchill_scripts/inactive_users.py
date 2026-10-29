"""List Windchill users who have not used the system recently.

Windchill does not expose a last-login date through REST, so last activity is
read from the web server access logs (Apache/PTC HTTP Server ``access_log*``
files, plain or .gz), which record the login name of every authenticated
request. Users are listed from the REST API, so people who never appear in
the logs are reported too. Run it on the web server, or copy the logs over.

Use from another app:
    from windchill_scripts.inactive_users import last_seen_from_logs, find_inactive_users
    seen = last_seen_from_logs(["/opt/ptc/HTTPServer/logs/access_log*"])
    result = find_inactive_users(client, seen, days=90)

Run on its own:
    uv run inactive-users --log "/opt/ptc/HTTPServer/logs/access_log*" [--days 90]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from windchill_api import WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display, first

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

# TODO: where the web server writes its access logs.
ACCESS_LOGS = ["/opt/ptc/HTTPServer/logs/access_log*"]

INACTIVE_DAYS = 90
OUTPUT_FILE = "inactive_users.txt"

log = logging.getLogger(__name__)

# Common/combined log format: host ident user [28/Oct/2026:08:16:39 -0500] "GET ..."
LOG_LINE = re.compile(r"^\S+ \S+ (?P<user>\S+) \[(?P<time>[^\]]+)\]")


@dataclass
class LogActivity:
    last_seen: dict[str, datetime] = field(default_factory=dict)  # lower-case login -> time
    earliest: datetime | None = None
    latest: datetime | None = None
    files: int = 0


@dataclass
class UserActivity:
    name: str
    full_name: str
    email: str
    disabled: bool
    last_seen: datetime | None


@dataclass
class InactiveUsersResult:
    days: int
    activity: LogActivity
    inactive: list[UserActivity]
    total_users: int


def _open(path: str):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def last_seen_from_logs(patterns: Iterable[str]) -> LogActivity:
    """Latest request time per login name across the given access log files."""
    activity = LogActivity()
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            activity.files += 1
            with _open(path) as f:
                for line in f:
                    m = LOG_LINE.match(line)
                    if not m or m["user"] == "-":
                        continue
                    try:
                        when = datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z")
                    except ValueError:
                        continue
                    user = m["user"].lower()
                    if user not in activity.last_seen or when > activity.last_seen[user]:
                        activity.last_seen[user] = when
                    if activity.earliest is None or when < activity.earliest:
                        activity.earliest = when
                    if activity.latest is None or when > activity.latest:
                        activity.latest = when
    return activity


def find_inactive_users(
    client: WindchillClient, activity: LogActivity, days: int = INACTIVE_DAYS, include_disabled: bool = False
) -> InactiveUsersResult:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    users = client.get_users()
    inactive = []
    for u in users:
        name = display(u.get("Name"))
        disabled = bool(first(u, "Disabled", "IsDisabled", default=False))
        if disabled and not include_disabled:
            continue
        seen = activity.last_seen.get(name.lower())
        if seen is None or seen < cutoff:
            inactive.append(UserActivity(name, display(u.get("FullName")), display(first(u, "EMail", "Email")), disabled, seen))
    inactive.sort(key=lambda a: (a.last_seen is not None, a.last_seen or datetime.min.replace(tzinfo=timezone.utc)))
    return InactiveUsersResult(days, activity, inactive, len(users))


def format_report(result: InactiveUsersResult, server: str = "") -> str:
    a = result.activity
    window = f"{a.earliest:%Y-%m-%d} to {a.latest:%Y-%m-%d}" if a.earliest and a.latest else "no entries found"
    text = header(
        f"Users inactive for {result.days}+ days",
        Server=server,
        Log_files=a.files,
        Log_coverage=window,
        Users=f"{result.total_users} total, {len(result.inactive)} inactive",
    )
    if a.earliest and (datetime.now(timezone.utc) - a.earliest).days < result.days:
        text += (
            f"\nNote: the logs only go back to {a.earliest:%Y-%m-%d}, less than {result.days} days.\n"
            '"Not in logs" means not seen since then, not never.\n'
        )
    rows = [
        (
            u.name,
            u.full_name,
            u.email,
            f"{u.last_seen:%Y-%m-%d}" if u.last_seen else "not in logs",
            "yes" if u.disabled else "",
        )
        for u in result.inactive
    ]
    text += section("Inactive users", table(rows, ["Login", "Name", "Email", "Last seen", "Disabled"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--log", action="append", help="Access log file or glob (repeatable)")
    parser.add_argument("--days", type=int, default=INACTIVE_DAYS)
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    activity = last_seen_from_logs(args.log or ACCESS_LOGS)
    if not activity.files:
        log.error("No access log files matched %s", args.log or ACCESS_LOGS)
        return 1
    try:
        result = find_inactive_users(client_from_args(args), activity, args.days, args.include_disabled)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(result, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
