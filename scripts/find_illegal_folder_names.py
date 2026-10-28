"""Find Windchill folders whose names contain illegal characters.

Walks every folder in every Product and Library through the Windchill OData
REST API and writes the full path of each offending folder to a text file.

Usage (from the repo root):
    uv run scripts/find_illegal_folder_names.py

Credentials are read from the WINDCHILL_USER / WINDCHILL_PASSWORD environment
variables, or prompted for if those are not set.
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from windchill_api import LIBRARY_TYPE, PRODUCT_TYPE, WindchillClient, WindchillError

# ---------------------------------------------------------------- Settings

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

BASE_URL = f"https://{SERVER}/Windchill"

# True, False, or a path to a CA bundle for an internal certificate authority.
VERIFY_SSL: bool | str = True

OUTPUT_FILE = Path("illegal_folder_names.txt")

# Characters Windchill does not allow in folder names.
ILLEGAL_CHARACTERS = set('/\\:*?"<>|')

# Also flag control/invisible characters and leading/trailing whitespace.
CHECK_CONTROL_CHARACTERS = True
CHECK_SURROUNDING_WHITESPACE = True

# -------------------------------------------------------------------------

log = logging.getLogger("find_illegal_folder_names")


def find_problems(name: str) -> list[str]:
    """Return a description of each problem with a folder name (empty if it is fine)."""
    problems = []
    found = sorted({c for c in name if c in ILLEGAL_CHARACTERS})
    if found:
        problems.append("illegal characters: " + " ".join(found))
    if CHECK_CONTROL_CHARACTERS:
        # Cc = control, Cf = format (zero-width etc.), Co/Cs/Cn = private/surrogate/unassigned
        hidden = sorted({c for c in name if unicodedata.category(c).startswith("C")})
        if hidden:
            problems.append(
                "hidden characters: " + " ".join(f"U+{ord(c):04X}" for c in hidden)
            )
    if CHECK_SURROUNDING_WHITESPACE and name != name.strip():
        problems.append("leading/trailing whitespace")
    return problems


def get_credentials() -> tuple[str, str]:
    username = os.environ.get("WINDCHILL_USER") or input("Windchill username: ")
    password = os.environ.get("WINDCHILL_PASSWORD") or getpass.getpass("Windchill password: ")
    return username, password


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    client = WindchillClient(BASE_URL, *get_credentials(), verify=VERIFY_SSL)

    try:
        containers = client.get_containers()
    except WindchillError as exc:
        log.error("Could not list containers: %s", exc)
        return 1

    kinds = {PRODUCT_TYPE: "Product", LIBRARY_TYPE: "Library"}
    targets = [
        (kind, c)
        for c in containers
        for type_name, kind in kinds.items()
        if c.get("@odata.type", "").endswith(type_name)
    ]
    targets.sort(key=lambda t: (t[0], t[1].get("Name", "").lower()))
    log.info(
        "Found %d products and %d libraries",
        sum(k == "Product" for k, _ in targets),
        sum(k == "Library" for k, _ in targets),
    )

    results: list[tuple[str, str, list[str]]] = []
    failed: list[str] = []
    total_folders = 0

    for kind, container in targets:
        name = container.get("Name", "")
        log.info("Scanning %s: %s", kind, name)
        try:
            for info in client.walk_folders(container):
                total_folders += 1
                problems = find_problems(info.name)
                if problems:
                    results.append((kind, info.full_path, problems))
        except WindchillError as exc:
            log.warning("Could not scan %s %s: %s", kind, name, exc)
            failed.append(f"{kind}: {name}")

    with OUTPUT_FILE.open("w", encoding="utf-8") as out:
        out.write("Windchill folders with illegal characters\n")
        out.write(f"Server:    {SERVER}\n")
        out.write(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        out.write(f"Scanned:   {len(targets)} containers, {total_folders} folders\n")
        out.write(f"Found:     {len(results)} folders with problems\n\n")
        for kind, path, problems in results:
            # repr-style escaping keeps hidden characters visible in the file.
            visible = path.encode("unicode_escape").decode("ascii") if any(
                "hidden" in p for p in problems
            ) else path
            out.write(f"[{kind}] {visible}\n    -> {'; '.join(problems)}\n")
        if failed:
            out.write("\nContainers that could not be scanned:\n")
            out.writelines(f"  {f}\n" for f in failed)

    log.info("Found %d folders with problems; wrote %s", len(results), OUTPUT_FILE.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
