"""Save who is in which Windchill group, and show what changed since the last snapshot.

Memberships are read from each user's direct groups
(PrincipalMgmt/Users?$expand=Groups), so a user who belongs to a group only
through another group is listed under that inner group.

Use from another app:
    from windchill_scripts.group_membership_snapshot import take_snapshot, diff_snapshots
    snap = take_snapshot(client)
    changes = diff_snapshots(old_snap, snap)

Run on its own (keeps snapshots in ./group_snapshots):
    uv run group-membership-snapshot [--dir DIR] [--compare-to FILE]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from windchill_api import WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

SNAPSHOT_DIR = "group_snapshots"
OUTPUT_FILE = "group_membership_changes.txt"

log = logging.getLogger(__name__)

# group name -> sorted list of member login names
Snapshot = dict[str, list[str]]


@dataclass
class SnapshotDiff:
    added_groups: list[str] = field(default_factory=list)
    removed_groups: list[str] = field(default_factory=list)
    added_members: list[tuple[str, str]] = field(default_factory=list)  # (group, user)
    removed_members: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added_groups or self.removed_groups or self.added_members or self.removed_members)


def take_snapshot(client: WindchillClient) -> Snapshot:
    groups: dict[str, set[str]] = defaultdict(set)
    for user in client.get_users(expand_groups=True):
        login = display(user.get("Name"))
        for g in user.get("Groups") or []:
            groups[display(g.get("Name"))].add(login)
    return {g: sorted(members, key=str.lower) for g, members in sorted(groups.items(), key=lambda kv: kv[0].lower())}


def diff_snapshots(old: Snapshot, new: Snapshot) -> SnapshotDiff:
    d = SnapshotDiff()
    d.added_groups = sorted(set(new) - set(old))
    d.removed_groups = sorted(set(old) - set(new))
    for g in sorted(set(old) | set(new)):
        before, after = set(old.get(g, [])), set(new.get(g, []))
        d.added_members += [(g, u) for u in sorted(after - before)]
        d.removed_members += [(g, u) for u in sorted(before - after)]
    return d


def save_snapshot(snapshot: Snapshot, directory: str | Path, when: datetime | None = None) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"groups_{(when or datetime.now()):%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: str | Path) -> Snapshot:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def latest_snapshot(directory: str | Path) -> Path | None:
    files = sorted(Path(directory).glob("groups_*.json"))
    return files[-1] if files else None


def format_report(new: Snapshot, diff: SnapshotDiff | None, previous: str = "", server: str = "") -> str:
    text = header(
        "Group membership",
        Server=server,
        Groups=len(new),
        Memberships=sum(len(m) for m in new.values()),
        Compared_to=previous or "(no earlier snapshot)",
    )
    if diff is not None:
        if diff.empty:
            text += "\nNo changes since the previous snapshot.\n"
        else:
            text += section("New groups", "".join(f"  {g}\n" for g in diff.added_groups) or "(none)\n")
            text += section("Removed groups", "".join(f"  {g}\n" for g in diff.removed_groups) or "(none)\n")
            text += section("Members added", table(diff.added_members, ["Group", "User"]))
            text += section("Members removed", table(diff.removed_members, ["Group", "User"]))
    text += section("Current membership", "".join(f"{g}: {', '.join(m)}\n" for g, m in new.items()) or "(none)\n")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--dir", default=SNAPSHOT_DIR, help="Where snapshots are kept")
    parser.add_argument("--compare-to", help="Snapshot file to compare with (default: the latest in --dir)")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    previous = Path(args.compare_to) if args.compare_to else latest_snapshot(args.dir)
    try:
        snapshot = take_snapshot(client_from_args(args))
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    diff = diff_snapshots(load_snapshot(previous), snapshot) if previous else None
    saved = save_snapshot(snapshot, args.dir)
    log.info("Saved snapshot %s", saved)
    write_report(args.output, format_report(snapshot, diff, str(previous or ""), args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
