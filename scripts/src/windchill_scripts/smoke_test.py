"""Check that Windchill works end to end after a restart or patch.

Logs in, lists products and libraries, searches for a part, opens it,
downloads a document's file, and checks that every REST endpoint the other
scripts use answers. Exits non-zero if any step fails.

Use from another app:
    from windchill_scripts.smoke_test import run_smoke_test
    results = run_smoke_test(client)
    ok = all(r.ok for r in results)

Run on its own:
    uv run smoke-test [--part-number 0000012345] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from windchill_api import WindchillClient, endpoints, odata_key, odata_string
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "smoke_test.txt"

log = logging.getLogger(__name__)


@dataclass
class StepResult:
    name: str
    ok: bool
    seconds: float
    detail: str


def _find_url(obj: Any) -> str | None:
    """First download URL anywhere in a (nested) content entity."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("url", "downloadurl") and isinstance(v, str) and v:
                return v
            found = _find_url(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_url(v)
            if found:
                return found
    return None


def _step(name: str, fn: Callable[[], str]) -> StepResult:
    start = time.perf_counter()
    try:
        detail = fn()
        ok = True
    except Exception as exc:  # any failure is a failed step, not a crash
        detail, ok = str(exc)[:300], False
    result = StepResult(name, ok, time.perf_counter() - start, detail)
    log.info("%s %s (%.1fs) %s", "PASS" if ok else "FAIL", name, result.seconds, detail)
    return result


def run_smoke_test(client: WindchillClient, part_number: str | None = None) -> list[StepResult]:
    results = []
    state: dict[str, Any] = {}

    def login() -> str:
        client.csrf_token(refresh=True)
        return "logged in, REST services answering"

    def containers() -> str:
        found = client.get_products_and_libraries()
        if not found:
            raise RuntimeError("no products or libraries visible to this user")
        return f"{len(found)} products and libraries"

    def search() -> str:
        flt = f"Number eq {odata_string(part_number)}" if part_number else None
        parts = list(client.query(endpoints.PARTS.path, filter=flt, top=1))
        if not parts:
            raise RuntimeError(f"no part found{' with number ' + part_number if part_number else ''}")
        state["part"] = parts[0]
        return f"found {display(parts[0].get('Number'))} {display(parts[0].get('Name'))}"

    def open_object() -> str:
        part = state.get("part")
        if not part:
            raise RuntimeError("skipped: search step found no part")
        e = client.get(endpoints.PARTS.path + odata_key(part["ID"]))
        return f"opened {display(e.get('Number'))} version {display(e.get('Version'))}"

    def download() -> str:
        for doc in client.query(endpoints.DOCUMENTS.path, expand="PrimaryContent", top=20):
            url = _find_url(doc.get("PrimaryContent"))
            if not url:
                continue
            if not url.startswith("http"):
                url = client.base_url.split("/Windchill")[0] + "/" + url.lstrip("/")
            with client.session.get(url, stream=True, timeout=client.timeout) as r:
                r.raise_for_status()
                size = sum(len(chunk) for chunk in r.iter_content(65536))
            return f"downloaded {size:,} bytes from {display(doc.get('Number'))}"
        raise RuntimeError("no document with a primary file in the first 20 documents")

    results += [
        _step("Log in", login),
        _step("List products and libraries", containers),
        _step("Search for a part", search),
        _step("Open the part", open_object),
        _step("Download a document file", download),
    ]

    endpoint_paths = [
        endpoints.USERS,
        endpoints.WORK_ITEMS,
        *(es.path for es in endpoints.WORKABLE_SETS),
        *(es.path for es in endpoints.CHANGE_SETS),
    ]
    for path in endpoint_paths:
        results.append(_step(f"Endpoint {path}", lambda p=path: f"{len(list(client.query(p, top=1)))} row(s)"))
    return results


def format_report(results: list[StepResult], server: str = "") -> str:
    failed = [r for r in results if not r.ok]
    text = header(
        "Windchill smoke test",
        Server=server,
        Result="PASS" if not failed else f"FAIL ({len(failed)} of {len(results)} checks failed)",
    )
    rows = [("PASS" if r.ok else "FAIL", f"{r.seconds:.1f}s", r.name, r.detail) for r in results]
    text += section("Checks", table(rows, ["Result", "Time", "Check", "Detail"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--part-number", help="A part number that should always exist")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    results = run_smoke_test(client_from_args(args), args.part_number)
    write_report(args.output, format_report(results, args.server))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
