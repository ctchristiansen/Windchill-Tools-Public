"""Hand a departing user's open workflow tasks to someone else, and list their checkouts.

Dry run by default. With --apply, open tasks are reassigned to the new user.
Windchill cannot transfer a checkout to another user: checkouts are listed so
the owner can check them in before they leave. --undo-checkouts undoes them
instead, which discards any changes in the working copies, so only use it when
those changes are not wanted.

Use from another app:
    from windchill_scripts.bulk_reassign import plan_handover, apply_handover
    plan = plan_handover(client, "jsmith", "mjones")
    apply_handover(client, plan)

Run on its own:
    uv run bulk-reassign jsmith mjones            # dry run
    uv run bulk-reassign jsmith mjones --apply    # reassign tasks
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from windchill_api import Entity, EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display, first, value
from windchill_api.workable import checked_out_by, is_checked_out

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "handover.txt"
CLOSED_STATUSES = {"completed", "complete", "terminated", "cancelled", "canceled", "aborted"}

log = logging.getLogger(__name__)


@dataclass
class Handover:
    from_user: Entity
    to_user: Entity
    tasks: list[Entity] = field(default_factory=list)
    checkouts: list[tuple[EntitySet, Entity]] = field(default_factory=list)
    task_status: str = "planned"
    checkout_status: dict[str, str] = field(default_factory=dict)  # object ID -> status


def _open(w: Entity) -> bool:
    status = str(value(w.get("Status")) or "").lower()
    return w.get("Completed") is not True and status not in CLOSED_STATUSES


def plan_handover(client: WindchillClient, from_login: str, to_login: str) -> Handover:
    from_user = client.find_user(from_login)
    to_user = client.find_user(to_login)
    if not from_user:
        raise WindchillError(f"User {from_login!r} not found")
    if not to_user:
        raise WindchillError(f"User {to_login!r} not found")
    plan = Handover(from_user, to_user)
    plan.tasks = [
        w
        for w in client.get_work_items()
        if _open(w) and display((w.get("Owner") or {}).get("Name")).lower() == from_login.lower()
    ]
    for es in endpoints.WORKABLE_SETS:
        log.info("Looking for %ss checked out by %s", es.label, from_login)
        for e in client.query_where(es.path, None, is_checked_out):
            if checked_out_by(e).lower() in (from_login.lower(), display(from_user.get("FullName")).lower()):
                plan.checkouts.append((es, e))
    return plan


def apply_handover(client: WindchillClient, plan: Handover, *, undo_checkouts: bool = False, comment: str = "") -> Handover:
    if plan.tasks:
        try:
            client.reassign_work_items(
                [w["ID"] for w in plan.tasks],
                plan.to_user["ID"],
                comment or f"Reassigned from {display(plan.from_user.get('Name'))}",
            )
            plan.task_status = "reassigned"
        except WindchillError as exc:
            plan.task_status = f"failed: {exc}"
    if undo_checkouts:
        for es, e in plan.checkouts:
            try:
                client.undo_checkout(es, e["ID"])
                plan.checkout_status[e["ID"]] = "checkout undone"
            except WindchillError as exc:
                plan.checkout_status[e["ID"]] = f"failed: {exc}"
    return plan


def format_report(plan: Handover, applied: bool, server: str = "") -> str:
    frm, to = display(plan.from_user.get("Name")), display(plan.to_user.get("Name"))
    text = header(
        f"Handover from {frm} to {to}" + ("" if applied else " (DRY RUN, nothing changed)"),
        Server=server,
        Open_tasks=f"{len(plan.tasks)} ({plan.task_status})" if plan.tasks else "0",
        Checkouts=len(plan.checkouts),
    )
    rows = [
        (
            display(first(w.get("Activity") or {}, "Name", default=w.get("Name"))),
            display(w.get("Subject")),
            display(w.get("Status")),
        )
        for w in plan.tasks
    ]
    text += section("Open tasks", table(rows, ["Task", "Subject", "Status"]))
    rows = [
        (
            es.label,
            display(e.get("Number")),
            display(first(e, "Version", "Revision")),
            display(e.get("Name")),
            plan.checkout_status.get(e["ID"], "still checked out"),
        )
        for es, e in plan.checkouts
    ]
    text += section(
        "Checked-out objects (ask the user to check these in; Windchill cannot transfer a checkout)",
        table(rows, ["Type", "Number", "Version", "Name", "Status"]),
    )
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("from_user", help="Login name of the departing user")
    parser.add_argument("to_user", help="Login name of the user taking over")
    parser.add_argument("--apply", action="store_true", help="Reassign the tasks (default is a dry run)")
    parser.add_argument("--undo-checkouts", action="store_true", help="With --apply: undo checkouts, DISCARDING changes")
    parser.add_argument("--comment", default="")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    client = client_from_args(args)
    try:
        plan = plan_handover(client, args.from_user, args.to_user)
        if args.apply:
            apply_handover(client, plan, undo_checkouts=args.undo_checkouts, comment=args.comment)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(plan, args.apply, args.server))
    failed = plan.task_status.startswith("failed") or any(s.startswith("failed") for s in plan.checkout_status.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
