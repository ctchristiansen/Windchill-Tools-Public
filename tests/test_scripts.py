"""End-to-end tests of every script against the fake Windchill server."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fake_windchill import FakeWindchill
from windchill_api import WindchillClient
from windchill_api.naming import find_name_problems
from windchill_scripts import (
    attribute_audit,
    bulk_reassign,
    change_aging,
    checked_out_report,
    duplicate_names,
    empty_folders,
    find_illegal_folder_names,
    group_membership_snapshot,
    inactive_users,
    log_scanner,
    queue_monitor,
    release_activity,
    rename_folders,
    smoke_test,
    stalled_workflow_tasks,
    team_membership_audit,
    vault_usage,
)


@pytest.fixture
def fake():
    with FakeWindchill() as server:
        yield server


@pytest.fixture
def client(fake):
    return WindchillClient(fake.base_url, "admin", "pw")


@pytest.fixture
def cli_env(fake, monkeypatch):
    """Environment so a script's main() talks to the fake server."""
    monkeypatch.setenv("WINDCHILL_SERVER", fake.base_url)
    monkeypatch.setenv("WINDCHILL_USER", "admin")
    monkeypatch.setenv("WINDCHILL_PASSWORD", "pw")


def test_name_problems():
    assert find_name_problems("ok name") == []
    assert find_name_problems("a:b") == ["illegal characters: :"]
    assert find_name_problems("x​ ") == ["hidden characters: U+200B", "leading/trailing whitespace"]


def test_illegal_folder_names(client):
    result = find_illegal_folder_names.scan(client)
    paths = sorted(f.path for f in result.bad_folders)
    assert result.containers == 3  # the project is skipped
    assert paths == [
        "/Drone/Bad:Name",
        "/Drone/CAD/Archive/Older/zero​width ",
        "/Drone/CAD/Sub ?",
        "/Lib's Standard Parts/Fasteners<M3>",
        "/Rover/Docs?",
    ]
    assert "zero\\u200bwidth" in find_illegal_folder_names.format_report(result)


def test_illegal_folder_names_cli(cli_env, tmp_path):
    out = tmp_path / "report.txt"
    assert find_illegal_folder_names.main(["--output", str(out)]) == 0
    assert "5 folders with problems" in out.read_text()


def test_checked_out(client):
    items = checked_out_report.find_checked_out(client)
    assert [(i.number, i.checked_out_by) for i in items] == [("P-100", "jsmith"), ("P-102", "mjones")]
    assert [i.number for i in checked_out_report.find_checked_out(client, min_days=30)] == ["P-100"]
    assert [i.number for i in checked_out_report.find_checked_out(client, user="MJONES")] == ["P-102"]
    assert "jsmith" in checked_out_report.format_report(items)


def test_empty_folders(client):
    result = empty_folders.find_empty_folders(client)
    found = {f.path: f.empty_subfolders for f in result.empty_folders}
    # Archive holds only empty folders (Old, Older, Older/zero-width), so it is reported once.
    # "Sub ?" holds only a folder entry in its contents, so it is empty too.
    assert found == {"/Drone/CAD/Sub ?": 0, "/Drone/CAD/Archive": 3, "/Rover/Docs?": 0}
    assert result.folders == 10


def test_duplicate_names(client):
    groups = duplicate_names.find_duplicate_names(client)
    by_key = {g.key: ([o.number for o in g.objects], g.exact) for g in groups}
    assert by_key == {"frame": (["P-100", "P-101"], True), "screwm3": (["P-102", "P-103"], False)}
    exact = duplicate_names.find_duplicate_names(client, exact_only=True)
    assert [g.key for g in exact] == ["frame"]


def test_attribute_audit(client):
    issues = attribute_audit.audit(client)
    found = {(i.number, i.attribute, i.problem.split(":")[0]) for i in issues}
    assert ("P-102", "DefaultUnit", "blank") in found
    assert ("P-101", "Source", "value 'Bogus' is not in the allowed list") in found
    assert ("P-101", "Name", "leading/trailing whitespace") in found
    assert not any(i.number == "P-103" for i in issues)
    assert "not in allowed list" in attribute_audit.format_report(issues)


def test_inactive_users(client, tmp_path):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=3)).strftime("%d/%b/%Y:%H:%M:%S +0000")
    old = (now - timedelta(days=200)).strftime("%d/%b/%Y:%H:%M:%S +0000")
    (tmp_path / "access_log").write_text(
        f'10.0.0.1 - jsmith [{recent}] "GET /Windchill/app HTTP/1.1" 200 5\n'
        f'10.0.0.2 - - [{recent}] "GET /Windchill/ HTTP/1.1" 401 5\n'
        "garbage line\n"
    )
    with gzip.open(tmp_path / "access_log.1.gz", "wt") as f:
        f.write(f'10.0.0.3 - MJones [{old}] "GET /Windchill/app HTTP/1.1" 200 5\n')
    activity = inactive_users.last_seen_from_logs([str(tmp_path / "access_log*")])
    assert activity.files == 2 and set(activity.last_seen) == {"jsmith", "mjones"}
    result = inactive_users.find_inactive_users(client, activity, days=90)
    assert [u.name for u in result.inactive] == ["quiet", "mjones"]  # never seen first; olduser is disabled
    result = inactive_users.find_inactive_users(client, activity, days=90, include_disabled=True)
    assert "olduser" in [u.name for u in result.inactive]
    assert "not in logs" in inactive_users.format_report(result)


def test_team_membership(client):
    result = team_membership_audit.audit_teams(client, ["Product Manager"])
    assert [(t.name, r) for t, r in result.empty_required_roles()] == [("Rover", "Product Manager")]
    assert [(t.name, m.name) for t, _, m in result.disabled_members()] == [("Drone", "olduser")]
    errors = [t.name for t in result.teams if t.error]
    assert errors == ["Lib's Standard Parts"]
    report = team_membership_audit.format_report(result)
    assert "Engineering (group)" in report


def test_group_snapshot(client, tmp_path):
    snap = group_membership_snapshot.take_snapshot(client)
    assert snap == {"CAD Users": ["jsmith", "olduser"], "Engineering": ["jsmith", "mjones"]}
    old = {"CAD Users": ["jsmith"], "Engineering": ["jsmith", "mjones", "gone"], "Retired": ["x"]}
    diff = group_membership_snapshot.diff_snapshots(old, snap)
    assert diff.added_members == [("CAD Users", "olduser")]
    assert diff.removed_members == [("Engineering", "gone"), ("Retired", "x")]
    assert diff.removed_groups == ["Retired"] and diff.added_groups == []
    saved = group_membership_snapshot.save_snapshot(snap, tmp_path)
    assert group_membership_snapshot.latest_snapshot(tmp_path) == saved
    assert group_membership_snapshot.load_snapshot(saved) == snap


def test_stalled_tasks(client):
    tasks = stalled_workflow_tasks.find_stalled_tasks(client, min_days=14)
    assert [(t.owner, t.task) for t in tasks] == [("jsmith", "Review Change"), ("olduser", "Approve")]
    assert tasks[1].owner_disabled and tasks[1].overdue
    assert "reassign these" in stalled_workflow_tasks.format_report(tasks)


def test_change_aging(client):
    changes, skipped = change_aging.find_open_changes(client)
    assert sorted(c.number for c in changes) == ["CN-1", "CR-7", "PR-1"]
    assert skipped == ["Variance"]
    report = change_aging.format_report(changes, skipped, stale_days=30)
    assert "PR-1" in report and "CR-7" in report
    stale_section = report.split("Untouched for")[1]
    assert "CN-1" not in stale_section


def test_release_activity(client):
    s = release_activity.summarize_activity(client, days=7)
    drone = s.counts[("Drone", "Part")]
    assert (drone.created, drone.changed, drone.released) == (1, 0, 1)
    assert ("Lib's Standard Parts", "Part", "P-102", "B.1", "Screw M3") in s.released
    assert s.counts[("(unknown context)", "Part")].changed == 1  # the working copy
    assert s.counts[("(unknown context)", "Change Notice")].created == 1


def test_smoke_test(client):
    results = smoke_test.run_smoke_test(client)
    failed = {r.name for r in results if not r.ok}
    assert failed == {"Endpoint ChangeMgmt/Variances"}
    download = next(r for r in results if r.name == "Download a document file")
    assert "13 bytes" in download.detail
    assert not next(r for r in smoke_test.run_smoke_test(client, "NOPE") if r.name == "Search for a part").ok


def test_rename_folders(client, fake):
    plan = rename_folders.plan_renames(client)
    by_path = {r.path: (r.new_name, r.status) for r in plan}
    assert by_path["/Drone/Bad:Name"] == ("Bad-Name", "planned")
    assert by_path["/Drone/CAD/Archive/Older/zero​width "] == ("zerowidth", "planned")
    # "Docs?" would become "Docs", which already exists next to it.
    assert by_path["/Rover/Docs?"][1].startswith("skipped")
    assert fake.writes == []  # planning never writes

    rename_folders.apply_renames(client, plan)
    patched = {body["Name"] for method, _, body in fake.writes if method == "PATCH"}
    assert patched == {"Bad-Name", "Sub", "zerowidth", "Fasteners(M3)"}
    assert find_illegal_folder_names.scan(client).bad_folders[0].path == "/Rover/Docs?"


def test_bulk_reassign(client, fake):
    plan = bulk_reassign.plan_handover(client, "jsmith", "mjones")
    assert [w["ID"] for w in plan.tasks] == ["OR:wt.workflow.work.WorkItem:1"]
    assert [e["Number"] for _, e in plan.checkouts] == ["P-100"]
    bulk_reassign.apply_handover(client, plan)
    assert fake.writes == [
        (
            "POST",
            "Workflow/ReassignWorkItems",
            {
                "WorkItems": [{"ID": "OR:wt.workflow.work.WorkItem:1"}],
                "User": {"ID": "OR:wt.org.WTUser:2"},
                "Comment": "Reassigned from jsmith",
            },
        )
    ]
    bulk_reassign.apply_handover(client, plan, undo_checkouts=True)
    assert fake.writes[-1][1] == "ProdMgmt/Parts('OR:wt.part.WTPart:100')/PTC.ProdMgmt.UndoCheckOut"
    assert "checkout undone" in bulk_reassign.format_report(plan, applied=True)


def test_bulk_reassign_unknown_user(client):
    with pytest.raises(Exception, match="not found"):
        bulk_reassign.plan_handover(client, "nobody", "mjones")


def test_queue_monitor():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE ProcessingQueue (idA2A2 INTEGER, name TEXT);
        CREATE TABLE QueueEntry (idA3A5 INTEGER, codeC5 TEXT);
        CREATE TABLE ScheduleQueue (idA2A2 INTEGER, name TEXT);
        CREATE TABLE ScheduleQueueEntry (idA3A5 INTEGER, codeC5 TEXT);
        INSERT INTO ProcessingQueue VALUES (1, 'PublisherQueue1'), (2, 'WfPropagationQueue');
        INSERT INTO QueueEntry VALUES (1, 'READY'), (1, 'READY'), (1, 'FAILED'), (2, 'SEVERE'), (2, 'COMPLETED');
        """
    )
    status = queue_monitor.queue_status(db)
    assert status.counts["PublisherQueue1"] == {"READY": 2, "FAILED": 1}
    assert sorted(status.failed()) == [("PublisherQueue1", 1), ("WfPropagationQueue", 1)]
    assert status.backlog(max_ready=1) == [("PublisherQueue1", 2)]
    assert "PublisherQueue1" in queue_monitor.format_report(status)


def test_log_scanner(tmp_path):
    log = tmp_path / "MethodServer-1.log"
    log.write_text(
        "2026-10-28 08:00:00,001 INFO  [main] wt.method - started\n"
        "2026-10-28 08:01:00,001 ERROR [ajp-1] wt.fc.Persist jsmith - Save failed for OR:wt.part.WTPart:123\n"
        "wt.util.WTException: wt.pom.UniquenessException: Number 42 not unique\n"
        "\tat wt.fc.Persist.save(Persist.java:10)\n"
        "Caused by: wt.pom.UniquenessException: Number 42 not unique\n"
        "2026-10-28 09:01:00,001 ERROR [ajp-2] wt.fc.Persist mjones - Save failed for OR:wt.part.WTPart:999\n"
        "Caused by: wt.pom.UniquenessException: Number 77 not unique\n"
        "2026-10-28 09:05:00,001 ERROR [ajp-3] wt.vc.Iterated jsmith - Checkout failed 'P-1'\n"
    )
    groups = log_scanner.scan_logs([str(log)])
    assert [(g.count, g.signature) for g in groups] == [
        (2, "wt.pom.UniquenessException: Save failed for <oid>"),
        (1, "Checkout failed '…'"),
    ]
    assert groups[0].first_seen.hour == 8 and groups[0].last_seen.hour == 9
    assert log_scanner.scan_logs([str(log)], since=datetime(2026, 10, 28, 9, 3))[0].signature.startswith("Checkout")
    assert "2 distinct" in log_scanner.format_report(groups)


def test_vault_usage(tmp_path):
    vault = tmp_path / "vault"
    (vault / "sub").mkdir(parents=True)
    (vault / "a.bin").write_bytes(b"x" * 1000)
    (vault / "sub" / "b.bin").write_bytes(b"y" * 500)
    history_file = tmp_path / "history.csv"

    week_ago = datetime.now() - timedelta(days=7)
    first = vault_usage.measure([str(vault), str(tmp_path / "missing")], when=week_ago)
    assert len(first) == 1 and first[0].vault_bytes == 1500 and first[0].files == 2
    first[0].disk_used -= 70 * 1024**3  # pretend the disk grew 10 GB/day
    first[0].disk_free += 70 * 1024**3
    vault_usage.append_history(first, history_file)
    now = vault_usage.measure([str(vault)])
    vault_usage.append_history(now, history_file)

    history = vault_usage.load_history(history_file)
    assert len(history) == 2
    rate = vault_usage.growth_per_day(history, str(vault))
    assert rate == pytest.approx(10 * 1024**3, rel=0.01)
    days = vault_usage.estimate_days_left(now[0], history)
    assert days == pytest.approx(now[0].disk_free / rate)
    warnings = vault_usage.find_warnings(now, history, warn_percent=101, warn_days=days + 1)
    assert warnings and "days until the disk is full" in warnings[0]
    assert "GB/day" in vault_usage.format_report(now, history)


@pytest.mark.parametrize(
    "module",
    [
        checked_out_report,
        empty_folders,
        duplicate_names,
        attribute_audit,
        stalled_workflow_tasks,
        change_aging,
        release_activity,
        team_membership_audit,
    ],
)
def test_cli_writes_report(module, cli_env, tmp_path):
    out = tmp_path / "out.txt"
    assert module.main(["--output", str(out)]) == 0
    assert out.read_text().strip()


def test_cli_dry_runs_do_not_write(cli_env, fake, tmp_path):
    assert rename_folders.main(["--output", str(tmp_path / "r.txt")]) == 0
    assert bulk_reassign.main(["jsmith", "mjones", "--output", str(tmp_path / "b.txt")]) == 0
    assert fake.writes == []
    assert "DRY RUN" in Path(tmp_path / "r.txt").read_text()


def test_cli_group_snapshot(cli_env, tmp_path):
    args = ["--dir", str(tmp_path / "snaps"), "--output", str(tmp_path / "g.txt")]
    assert group_membership_snapshot.main(args) == 0
    assert "no earlier snapshot" in (tmp_path / "g.txt").read_text()
