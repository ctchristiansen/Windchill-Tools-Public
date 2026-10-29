"""Pull errors and stack traces out of Windchill server logs and group the repeats.

Reads MethodServer, BackgroundMethodServer and ServerManager logs (plain or
.gz). Errors that differ only in ids, numbers or timestamps are grouped, so
the report shows each distinct problem once with a count.

Use from another app:
    from windchill_scripts.log_scanner import scan_logs
    groups = scan_logs(["/opt/ptc/Windchill/logs/MethodServer-*.log"])

Run on its own (on the Windchill server):
    uv run log-scanner [--log GLOB ...] [--since-hours 24] [--output FILE]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import logging
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from windchill_api.cli import setup_logging, write_report
from windchill_api.report import header, section, table

# TODO: where Windchill writes its logs ($WT_HOME/logs).
LOG_FILES = [
    "/opt/ptc/Windchill/logs/MethodServer-*.log*",
    "/opt/ptc/Windchill/logs/BackgroundMethodServer-*.log*",
    "/opt/ptc/Windchill/logs/ServerManager-*.log*",
]

SINCE_HOURS = 24
TOP = 50
LEVELS = ("ERROR", "FATAL", "SEVERE")
OUTPUT_FILE = "log_errors.txt"

log = logging.getLogger(__name__)

# 2026-10-28 08:16:39,123 ERROR [thread] logger user - message
ENTRY_START = re.compile(r"^(?P<time>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
LEVEL = re.compile(r"\b(" + "|".join(LEVELS) + r")\b")
EXCEPTION = re.compile(r"\b((?:[a-z_$][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error|Throwable))\b")
CAUSED_BY = re.compile(r"^Caused by: (\S+?)(?::|$)")

# Parts of a message that change between occurrences of the same problem.
VOLATILE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,.\d]*"), "<time>"),
    (re.compile(r"\[[^\]]*\]"), "[…]"),
    (re.compile(r"OR:[\w.]+:\d+|VR:[\w.]+:\d+"), "<oid>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b\d+\b", re.I), "<n>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "'…'"),
]


@dataclass
class ErrorGroup:
    signature: str
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    files: set[str] = field(default_factory=set)
    example: str = ""


def normalize(message: str) -> str:
    for pattern, repl in VOLATILE:
        message = pattern.sub(repl, message)
    return re.sub(r"\s+", " ", message).strip()[:300]


def _open(path: str):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def _entries(path: str) -> Iterator[tuple[datetime | None, list[str]]]:
    """Yield (time, lines) per log entry; continuation lines (stack traces) join the entry before them."""
    current: list[str] = []
    when: datetime | None = None
    with _open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            m = ENTRY_START.match(line)
            if m:
                if current:
                    yield when, current
                current = [line]
                try:
                    when = datetime.strptime(m["time"].replace("T", " "), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    when = None
            elif current:
                current.append(line)
    if current:
        yield when, current


def signature(lines: list[str]) -> str:
    """What makes an error distinct: the root cause exception plus the normalized message."""
    message = lines[0]
    message = message.split(" - ", 1)[1] if " - " in message else LEVEL.split(message, 1)[-1]
    root = ""
    for line in lines[1:]:
        m = CAUSED_BY.match(line.strip())
        if m:
            root = m[1]
    if not root:
        m = EXCEPTION.search("\n".join(lines[:3]))
        root = m[1] if m else ""
    return f"{root}: {normalize(message)}" if root else normalize(message)


def scan_logs(patterns: Iterable[str], since: datetime | None = None) -> list[ErrorGroup]:
    groups: dict[str, ErrorGroup] = {}
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            for when, lines in _entries(path):
                if not LEVEL.search(lines[0]) or (since and when and when < since):
                    continue
                sig = signature(lines)
                g = groups.setdefault(sig, ErrorGroup(sig, example="\n".join(lines[:15])))
                g.count += 1
                g.files.add(path.rsplit("/", 1)[-1])
                if when:
                    g.first_seen = min(filter(None, (g.first_seen, when)))
                    g.last_seen = max(filter(None, (g.last_seen, when)))
    return sorted(groups.values(), key=lambda g: -g.count)


def format_report(groups: list[ErrorGroup], since: datetime | None = None, top: int = TOP) -> str:
    text = header(
        "Windchill log errors",
        Since=f"{since:%Y-%m-%d %H:%M}" if since else "start of logs",
        Found=f"{sum(g.count for g in groups)} errors, {len(groups)} distinct",
    )
    rows = [(g.count, f"{g.last_seen:%m-%d %H:%M}" if g.last_seen else "?", g.signature[:120]) for g in groups[:top]]
    text += section(f"Most frequent (top {top})", table(rows, ["Count", "Last seen", "Error"]))
    body = ""
    for i, g in enumerate(groups[:top], 1):
        seen = f"{g.first_seen:%Y-%m-%d %H:%M} to {g.last_seen:%Y-%m-%d %H:%M}" if g.first_seen and g.last_seen else "?"
        body += f"\n#{i}  {g.count}x  {seen}  in {', '.join(sorted(g.files))}\n{g.example}\n"
    text += section("Examples", body or "(none)\n")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", action="append", help="Log file or glob (repeatable)")
    parser.add_argument("--since-hours", type=float, default=SINCE_HOURS, help="0 for the whole log")
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    patterns = args.log or LOG_FILES
    if not any(glob.glob(p) for p in patterns):
        log.error("No log files matched %s", patterns)
        return 1
    since = datetime.now() - timedelta(hours=args.since_hours) if args.since_hours else None
    groups = scan_logs(patterns, since)
    write_report(args.output, format_report(groups, since, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
