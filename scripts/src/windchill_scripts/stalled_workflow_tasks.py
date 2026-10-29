"""List open workflow tasks older than a threshold, grouped by assignee.

Tasks assigned to disabled users are flagged, since nobody will ever complete
them. Windchill returns the work items the logged-in user is allowed to see,
so run it as a site administrator to cover everyone.

Use from another app:
    from windchill_scripts.stalled_workflow_tasks import find_stalled_tasks
    tasks = find_stalled_tasks(client, min_days=14)

Run on its own:
    uv run stalled-workflow-tasks [--min-days 14] [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from windchill_api import WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import age_days, display, first, parse_datetime, value

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

MIN_DAYS = 14
OUTPUT_FILE = "stalled_workflow_tasks.txt"

# Work item statuses that mean the task is finished ("Accepted" is still open: someone took it on).
CLOSED_STATUSES = {"completed", "complete", "terminated", "cancelled", "canceled", "aborted"}

log = logging.getLogger(__name__)


@dataclass
class StalledTask:
    id: str
    task: str
    subject: str
    owner: str
    owner_name: str
    owner_disabled: bool
    status: str
    created: datetime | None
    due: datetime | None

    @property
    def days(self) -> float | None:
        return age_days(self.created)

    @property
    def overdue(self) -> bool:
        return bool(self.due and (age_days(self.due) or 0) > 0)


def is_open(work_item: dict) -> bool:
    if work_item.get("Completed") is True:
        return False
    status = str(value(work_item.get("Status")) or "").lower()
    return status not in CLOSED_STATUSES and display(work_item.get("Status")).lower() not in CLOSED_STATUSES


def find_stalled_tasks(client: WindchillClient, min_days: float = MIN_DAYS) -> list[StalledTask]:
    tasks = []
    for w in client.get_work_items():
        if not is_open(w):
            continue
        owner = w.get("Owner") or {}
        task = StalledTask(
            id=w.get("ID", ""),
            task=display(first(w.get("Activity") or {}, "Name", default=w.get("Name"))),
            subject=display(first(w, "Subject", "PrimaryBusinessObjectName", "Description", default="")),
            owner=display(owner.get("Name")) if owner else "(unassigned)",
            owner_name=display(owner.get("FullName")) if owner else "",
            owner_disabled=bool(first(owner, "Disabled", "IsDisabled", default=False)) if owner else False,
            status=display(w.get("Status")),
            created=parse_datetime(first(w, "CreatedOn", "CreateTimestamp", "StartTime")),
            due=parse_datetime(first(w, "DueDate", "Deadline")),
        )
        if (task.days or 0) >= min_days:
            tasks.append(task)
    tasks.sort(key=lambda t: (t.owner.lower(), -(t.days or 0)))
    return tasks


def format_report(tasks: list[StalledTask], server: str = "", min_days: float = MIN_DAYS) -> str:
    by_owner = Counter(t.owner for t in tasks)
    disabled = [t for t in tasks if t.owner_disabled]
    text = header(
        f"Open workflow tasks older than {min_days:g} days",
        Server=server,
        Found=f"{len(tasks)} tasks, {len(disabled)} assigned to disabled users",
    )
    text += section("By assignee", table(by_owner.most_common(), ["Assignee", "Tasks"]))
    text += section(
        "Assigned to disabled users (reassign these)",
        table([(t.owner, t.task, t.subject, f"{t.days:.0f}") for t in disabled], ["Assignee", "Task", "Subject", "Days"]),
    )
    rows = [
        (
            t.owner,
            f"{t.days:.0f}" if t.days is not None else "?",
            f"{t.due:%Y-%m-%d}" + (" OVERDUE" if t.overdue else "") if t.due else "",
            t.status,
            t.task,
            t.subject,
        )
        for t in tasks
    ]
    text += section("All stalled tasks", table(rows, ["Assignee", "Days", "Due", "Status", "Task", "Subject"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--min-days", type=float, default=MIN_DAYS)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        tasks = find_stalled_tasks(client_from_args(args), args.min_days)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(tasks, args.server, args.min_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
