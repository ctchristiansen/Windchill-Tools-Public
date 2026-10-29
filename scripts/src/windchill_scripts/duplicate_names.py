"""Find parts, documents and CAD documents whose names differ only by case, spacing or punctuation.

Each object number is counted once, so different versions of the same object
are not reported as duplicates.

Use from another app:
    from windchill_scripts.duplicate_names import find_duplicate_names
    groups = find_duplicate_names(client)

Run on its own:
    uv run duplicate-names [--exact-only] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from windchill_api import EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.naming import visible
from windchill_api.report import header, section
from windchill_api.values import display, first

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "duplicate_names.txt"

log = logging.getLogger(__name__)


@dataclass
class NamedObject:
    type: str
    number: str
    name: str
    container: str


@dataclass
class DuplicateGroup:
    type: str
    key: str  # the normalized name the objects share
    exact: bool  # True if the names are identical, not just similar
    objects: list[NamedObject]


def normalize(name: str, exact: bool = False) -> str:
    """Comparison key. Exact mode only ignores case and surrounding spaces."""
    name = unicodedata.normalize("NFKC", name).casefold().strip()
    if exact:
        return name
    return re.sub(r"[\W_]+", "", name)


def find_duplicate_names(
    client: WindchillClient,
    entity_sets: Iterable[EntitySet] = endpoints.WORKABLE_SETS,
    *,
    exact_only: bool = False,
) -> list[DuplicateGroup]:
    groups: list[DuplicateGroup] = []
    for es in entity_sets:
        log.info("Reading %ss", es.label)
        by_number: dict[str, NamedObject] = {}
        for e in client.query(es.path, select=["ID", "Number", "Name"]):
            number = display(e.get("Number"))
            if number and number not in by_number:
                by_number[number] = NamedObject(
                    es.label, number, display(e.get("Name")), display(first(e, "ContainerName", "Context"))
                )
        by_key: dict[str, list[NamedObject]] = defaultdict(list)
        for obj in by_number.values():
            key = normalize(obj.name, exact_only)
            if key:
                by_key[key].append(obj)
        for key, objs in sorted(by_key.items()):
            if len(objs) > 1:
                exact = len({normalize(o.name, True) for o in objs}) == 1
                groups.append(DuplicateGroup(es.label, key, exact, sorted(objs, key=lambda o: o.number)))
    return groups


def format_report(groups: list[DuplicateGroup], server: str = "") -> str:
    exact = [g for g in groups if g.exact]
    similar = [g for g in groups if not g.exact]
    text = header(
        "Duplicate and near-duplicate names",
        Server=server,
        Found=f"{len(exact)} groups with the same name (ignoring case), {len(similar)} with similar names",
    )
    for title, subset in (("Same name (ignoring case)", exact), ("Similar names (ignoring spaces and punctuation)", similar)):
        body = ""
        for g in subset:
            body += f"[{g.type}] {len(g.objects)} objects\n"
            body += "".join(f"    {o.number:<25} {visible(o.name)}   {o.container}\n" for o in g.objects)
        text += section(title, body or "(none)\n")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--exact-only", action="store_true", help="Only names that are identical ignoring case")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        groups = find_duplicate_names(client_from_args(args), exact_only=args.exact_only)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(groups, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
