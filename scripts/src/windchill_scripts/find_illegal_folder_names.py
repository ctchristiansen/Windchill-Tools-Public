"""Find folders in every product and library whose names contain illegal characters.

Use from another app:
    from windchill_scripts.find_illegal_folder_names import scan
    result = scan(client)

Run on its own:
    uv run find-illegal-folder-names [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

from windchill_api import WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.naming import DEFAULT_ILLEGAL_CHARACTERS, find_name_problems, visible
from windchill_api.report import header, section

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "illegal_folder_names.txt"

log = logging.getLogger(__name__)


@dataclass
class BadFolder:
    kind: str  # "Product" or "Library"
    container_id: str
    folder_id: str
    path: str
    name: str
    problems: list[str]


@dataclass
class ScanResult:
    containers: int = 0
    folders: int = 0
    bad_folders: list[BadFolder] = field(default_factory=list)
    failed_containers: list[str] = field(default_factory=list)


def scan(
    client: WindchillClient,
    illegal_characters: Iterable[str] = DEFAULT_ILLEGAL_CHARACTERS,
    *,
    check_hidden: bool = True,
    check_whitespace: bool = True,
) -> ScanResult:
    illegal = set(illegal_characters)
    result = ScanResult()
    targets = client.get_products_and_libraries()
    result.containers = len(targets)
    for kind, container in targets:
        log.info("Scanning %s: %s", kind, container.get("Name"))
        try:
            for info in client.walk_folders(container):
                result.folders += 1
                problems = find_name_problems(info.name, illegal, check_hidden=check_hidden, check_whitespace=check_whitespace)
                if problems:
                    result.bad_folders.append(BadFolder(kind, info.container_id, info.id, info.full_path, info.name, problems))
        except WindchillError as exc:
            log.warning("Could not scan %s %s: %s", kind, container.get("Name"), exc)
            result.failed_containers.append(f"{kind}: {container.get('Name')}")
    return result


def format_report(result: ScanResult, server: str = "") -> str:
    text = header(
        "Windchill folders with illegal characters",
        Server=server,
        Scanned=f"{result.containers} containers, {result.folders} folders",
        Found=f"{len(result.bad_folders)} folders with problems",
    )
    text += "\n"
    for f in result.bad_folders:
        text += f"[{f.kind}] {visible(f.path)}\n    -> {'; '.join(f.problems)}\n"
    if result.failed_containers:
        text += section("Containers that could not be scanned", "".join(f"  {c}\n" for c in result.failed_containers))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        result = scan(client_from_args(args))
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(result, args.server))
    log.info("Found %d folders with problems", len(result.bad_folders))
    return 0


if __name__ == "__main__":
    sys.exit(main())
