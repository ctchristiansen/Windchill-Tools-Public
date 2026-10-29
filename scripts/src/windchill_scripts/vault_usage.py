"""Track file vault size and disk space over time, and warn before a disk fills up.

Each run measures the vault folders, appends to a CSV history file, and uses
that history to estimate how many days are left on each disk. Run it on the
server that mounts the vaults, ideally on a schedule (daily).

Use from another app:
    from windchill_scripts.vault_usage import measure, estimate_days_left
    sample = measure(["/vaults/master"])

Run on its own:
    uv run vault-usage [--vault DIR ...] [--output FILE]
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from windchill_api.cli import setup_logging, write_report
from windchill_api.report import header, section, table

# TODO: the vault root folders (the mount points configured in External Storage Administration).
VAULT_DIRS = ["/vaults/defaultSystemVault", "/vaults/master"]

HISTORY_FILE = "vault_usage_history.csv"
OUTPUT_FILE = "vault_usage.txt"
WARN_PERCENT = 85
WARN_DAYS = 60

log = logging.getLogger(__name__)

GB = 1024**3


@dataclass
class VaultSample:
    time: datetime
    path: str
    vault_bytes: int
    files: int
    disk_total: int
    disk_used: int
    disk_free: int

    @property
    def disk_percent(self) -> float:
        return 100 * self.disk_used / self.disk_total if self.disk_total else 0


def folder_size(path: str | Path) -> tuple[int, int]:
    """Total bytes and file count under a folder (does not follow symlinks)."""
    total = count = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: log.warning("%s", e)):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
                count += 1
            except OSError:
                pass
    return total, count


def measure(vault_dirs: Iterable[str], when: datetime | None = None) -> list[VaultSample]:
    when = when or datetime.now()
    samples = []
    for d in vault_dirs:
        if not os.path.isdir(d):
            log.warning("Vault folder not found: %s", d)
            continue
        log.info("Measuring %s", d)
        size, files = folder_size(d)
        disk = shutil.disk_usage(d)
        samples.append(VaultSample(when, d, size, files, disk.total, disk.used, disk.free))
    return samples


FIELDS = ["time", "path", "vault_bytes", "files", "disk_total", "disk_used", "disk_free"]


def append_history(samples: Iterable[VaultSample], path: str | Path) -> None:
    path = Path(path)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        for s in samples:
            w.writerow(
                [s.time.isoformat(timespec="seconds"), s.path, s.vault_bytes, s.files, s.disk_total, s.disk_used, s.disk_free]
            )


def load_history(path: str | Path) -> list[VaultSample]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [
            VaultSample(datetime.fromisoformat(r["time"]), r["path"], *(int(r[k]) for k in FIELDS[2:])) for r in csv.DictReader(f)
        ]


def growth_per_day(history: list[VaultSample], path: str, window_days: float = 30) -> float | None:
    """Average disk growth in bytes per day for one vault over the last ``window_days``."""
    points = sorted((s for s in history if s.path == path), key=lambda s: s.time)
    if points:
        cutoff = points[-1].time - timedelta(days=window_days)
        points = [s for s in points if s.time >= cutoff]
    if len(points) < 2:
        return None
    days = (points[-1].time - points[0].time).total_seconds() / 86400
    if days < 0.5:
        return None
    return (points[-1].disk_used - points[0].disk_used) / days


def estimate_days_left(sample: VaultSample, history: list[VaultSample]) -> float | None:
    rate = growth_per_day(history, sample.path)
    if not rate or rate <= 0:
        return None
    return sample.disk_free / rate


def find_warnings(
    samples: list[VaultSample], history: list[VaultSample], warn_percent: float = WARN_PERCENT, warn_days: float = WARN_DAYS
) -> list[str]:
    warnings = []
    for s in samples:
        days = estimate_days_left(s, history)
        if s.disk_percent >= warn_percent:
            warnings.append(f"{s.path}: disk is {s.disk_percent:.0f}% full")
        if days is not None and days <= warn_days:
            warnings.append(f"{s.path}: about {days:.0f} days until the disk is full")
    return warnings


def format_report(
    samples: list[VaultSample], history: list[VaultSample], warn_percent: float = WARN_PERCENT, warn_days: float = WARN_DAYS
) -> str:
    warnings = find_warnings(samples, history, warn_percent, warn_days)
    rows = []
    for s in samples:
        rate = growth_per_day(history, s.path)
        days = estimate_days_left(s, history)
        rows.append(
            (
                s.path,
                f"{s.vault_bytes / GB:,.1f}",
                f"{s.files:,}",
                f"{s.disk_free / GB:,.1f}",
                f"{s.disk_percent:.0f}%",
                f"{rate / GB:,.2f}" if rate is not None else "n/a",
                f"{days:,.0f}" if days is not None else "n/a",
            )
        )
    text = header("Vault and disk usage", Vaults=len(samples), Warnings=len(warnings))
    text += section("Warnings", "".join(f"  {w}\n" for w in warnings) or "(none)\n")
    text += section(
        "Vaults",
        table(rows, ["Vault", "Vault GB", "Files", "Disk free GB", "Disk used", "GB/day", "Days left"]),
    )
    if not any(growth_per_day(history, s.path) is not None for s in samples):
        text += "\nGrowth and days left need at least two runs a day or more apart.\n"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", action="append", help="Vault root folder (repeatable)")
    parser.add_argument("--history", default=HISTORY_FILE)
    parser.add_argument("--warn-percent", type=float, default=WARN_PERCENT)
    parser.add_argument("--warn-days", type=float, default=WARN_DAYS)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    samples = measure(args.vault or VAULT_DIRS)
    if not samples:
        log.error("None of the vault folders exist: %s", args.vault or VAULT_DIRS)
        return 1
    append_history(samples, args.history)
    history = load_history(args.history)
    write_report(args.output, format_report(samples, history, args.warn_percent, args.warn_days))
    # Non-zero exit when something needs attention, so it can drive alerts from a scheduler.
    return 2 if find_warnings(samples, history, args.warn_percent, args.warn_days) else 0


if __name__ == "__main__":
    sys.exit(main())
