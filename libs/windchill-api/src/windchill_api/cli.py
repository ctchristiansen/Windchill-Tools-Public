"""Command-line plumbing shared by the scripts: connection options, credentials, output."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
from pathlib import Path

from windchill_api.client import WindchillClient


def add_connection_args(parser: argparse.ArgumentParser, default_server: str) -> None:
    group = parser.add_argument_group("connection")
    group.add_argument(
        "--server",
        default=os.environ.get("WINDCHILL_SERVER", default_server),
        help="Windchill host name, optionally with :port (default: %(default)s)",
    )
    group.add_argument(
        "--ca-bundle",
        default=os.environ.get("WINDCHILL_CA_BUNDLE"),
        help="CA bundle for an internal certificate authority",
    )
    group.add_argument("--insecure", action="store_true", help="Do not verify the TLS certificate")


def get_credentials() -> tuple[str, str]:
    """Read WINDCHILL_USER / WINDCHILL_PASSWORD, prompting for any that are not set."""
    username = os.environ.get("WINDCHILL_USER") or input("Windchill username: ")
    password = os.environ.get("WINDCHILL_PASSWORD") or getpass.getpass("Windchill password: ")
    return username, password


def base_url(server: str) -> str:
    if server.startswith("http"):
        return server.rstrip("/")
    return f"https://{server}/Windchill"


def client_from_args(args: argparse.Namespace) -> WindchillClient:
    verify: bool | str = False if args.insecure else (args.ca_bundle or True)
    return WindchillClient(base_url(args.server), *get_credentials(), verify=verify)


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s: %(message)s")


def write_report(path: str | Path, text: str) -> Path:
    path = Path(path)
    path.write_text(text, encoding="utf-8")
    logging.getLogger(__name__).info("Wrote %s", path.resolve())
    return path
