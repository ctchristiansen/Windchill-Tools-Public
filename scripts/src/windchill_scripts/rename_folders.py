"""Rename folders whose names contain illegal characters.

Dry run by default: it lists each folder with the name it would get. Review
that list, adjust REPLACEMENTS if needed, then run again with --apply.

Each illegal character is replaced using REPLACEMENTS (or "_" if not listed),
hidden characters are removed and surrounding whitespace is trimmed. Folders
whose new name would clash with a sibling are skipped.

Use from another app:
    from windchill_scripts.rename_folders import plan_renames, apply_renames
    plan = plan_renames(client)
    apply_renames(client, plan)

Run on its own:
    uv run rename-folders              # dry run, writes the plan
    uv run rename-folders --apply      # renames
"""

from __future__ import annotations

import argparse
import logging
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from windchill_api import WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.naming import DEFAULT_ILLEGAL_CHARACTERS, find_name_problems, visible
from windchill_api.report import header, section, table

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

# What to put in place of each illegal character. Anything not listed becomes "_".
REPLACEMENTS = {
    "/": "-",
    "\\": "-",
    ":": "-",
    "*": "",
    "?": "",
    '"': "'",
    "<": "(",
    ">": ")",
    "|": "-",
}

OUTPUT_FILE = "folder_renames.txt"

log = logging.getLogger(__name__)


@dataclass
class Rename:
    kind: str
    container_id: str
    folder_id: str
    path: str
    old_name: str
    new_name: str
    status: str = "planned"  # planned, renamed, skipped: <why>, failed: <why>


def fix_name(name: str, replacements: dict[str, str] = REPLACEMENTS) -> str:
    out = []
    for c in name:
        if unicodedata.category(c).startswith("C"):
            continue
        out.append(replacements.get(c, "_") if c in DEFAULT_ILLEGAL_CHARACTERS else c)
    return " ".join("".join(out).split())  # trim and collapse runs of spaces


def plan_renames(client: WindchillClient, replacements: dict[str, str] = REPLACEMENTS) -> list[Rename]:
    plan: list[Rename] = []
    for kind, container in client.get_products_and_libraries():
        log.info("Scanning %s: %s", kind, container.get("Name"))
        siblings: dict[tuple[str, ...], set[str]] = defaultdict(set)
        bad = []
        try:
            for info in client.walk_folders(container):
                siblings[tuple(info.path[:-1])].add(info.name.casefold())
                if find_name_problems(info.name):
                    bad.append(info)
        except WindchillError as exc:
            log.warning("Could not scan %s: %s", container.get("Name"), exc)
            continue
        for info in bad:
            new = fix_name(info.name, replacements)
            r = Rename(kind, info.container_id, info.id, info.full_path, info.name, new)
            taken = siblings[tuple(info.path[:-1])]
            if not new:
                r.status = "skipped: new name would be empty"
            elif new.casefold() in taken and new.casefold() != info.name.casefold():
                r.status = "skipped: a folder with the new name already exists here"
            else:
                taken.add(new.casefold())
            plan.append(r)
    return plan


def apply_renames(client: WindchillClient, plan: list[Rename]) -> list[Rename]:
    for r in plan:
        if r.status != "planned":
            continue
        try:
            client.rename_folder(r.container_id, r.folder_id, r.new_name)
            r.status = "renamed"
            log.info("Renamed %s -> %s", r.path, r.new_name)
        except WindchillError as exc:
            r.status = f"failed: {exc}"
            log.warning("Could not rename %s: %s", r.path, exc)
    return plan


def format_report(plan: list[Rename], applied: bool, server: str = "") -> str:
    counts: dict[str, int] = defaultdict(int)
    for r in plan:
        counts[r.status.split(":")[0]] += 1
    text = header(
        "Folder renames" + ("" if applied else " (DRY RUN, nothing changed)"),
        Server=server,
        Folders=len(plan),
        Status=", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing to do",
    )
    rows = [(r.status, visible(r.path), visible(r.old_name), r.new_name) for r in plan]
    text += section("Folders", table(rows, ["Status", "Path", "Old name", "New name"]))
    if not applied and counts.get("planned"):
        text += "\nRun again with --apply to rename the 'planned' folders.\n"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--apply", action="store_true", help="Actually rename (default is a dry run)")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    client = client_from_args(args)
    try:
        plan = plan_renames(client)
        if args.apply:
            apply_renames(client, plan)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(plan, args.apply, args.server))
    return 1 if any(r.status.startswith("failed") for r in plan) else 0


if __name__ == "__main__":
    sys.exit(main())
