"""List who holds each team role in every product and library, and flag problems.

Flags disabled users still on a team, and products/libraries where a role you
consider essential (REQUIRED_ROLES) has nobody in it.

The REST navigation to a container's team is set in windchill_api.endpoints
(CONTAINER_TEAM_ROLES). Windchill REST Services does not document it for every
release; if your server names it differently, check
/Windchill/servlet/odata/DataAdmin/$metadata and update it there.

Use from another app:
    from windchill_scripts.team_membership_audit import audit_teams
    result = audit_teams(client, required_roles=["Product Manager"])

Run on its own:
    uv run team-membership-audit [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

from windchill_api import Entity, WindchillClient, WindchillError, endpoints, odata_key
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.report import header, section, table
from windchill_api.values import display, first

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

# TODO: roles every product and library should have at least one member in.
REQUIRED_ROLES = ["Product Manager"]

OUTPUT_FILE = "team_membership.txt"

log = logging.getLogger(__name__)


@dataclass
class Member:
    name: str
    full_name: str
    kind: str  # "User" or "Group"
    disabled: bool


@dataclass
class ContainerTeam:
    kind: str
    name: str
    roles: dict[str, list[Member]] = field(default_factory=dict)
    error: str = ""


@dataclass
class TeamAuditResult:
    teams: list[ContainerTeam]
    required_roles: list[str]

    def empty_required_roles(self) -> list[tuple[ContainerTeam, str]]:
        out = []
        for t in self.teams:
            if t.error:
                continue
            present = {r.lower(): m for r, m in t.roles.items()}
            out += [(t, r) for r in self.required_roles if not present.get(r.lower())]
        return out

    def disabled_members(self) -> list[tuple[ContainerTeam, str, Member]]:
        return [(t, r, m) for t in self.teams for r, ms in t.roles.items() for m in ms if m.disabled]


def _member(e: Entity) -> Member:
    kind = "Group" if "Group" in e.get("@odata.type", "") or "WTGroup" in e.get("ID", "") else "User"
    return Member(
        display(e.get("Name")),
        display(first(e, "FullName", "Description")),
        kind,
        bool(first(e, "Disabled", "IsDisabled", default=False)),
    )


def get_team(client: WindchillClient, container: Entity) -> dict[str, list[Member]]:
    path = endpoints.CONTAINER_TEAM_ROLES.format(key=odata_key(container["ID"]))
    roles = {}
    for role in client.query(path, expand=endpoints.TEAM_ROLE_MEMBERS_EXPAND):
        role_name = display(first(role, "Display", "Name", "Role"))
        members = role.get(endpoints.TEAM_ROLE_MEMBERS_EXPAND) or []
        roles[role_name] = sorted((_member(m) for m in members), key=lambda m: m.name.lower())
    return roles


def audit_teams(client: WindchillClient, required_roles: Iterable[str] = REQUIRED_ROLES) -> TeamAuditResult:
    teams = []
    for kind, container in client.get_products_and_libraries():
        team = ContainerTeam(kind, container.get("Name", ""))
        log.info("Reading team of %s: %s", kind, team.name)
        try:
            team.roles = get_team(client, container)
        except WindchillError as exc:
            team.error = str(exc)
            log.warning("Could not read team of %s: %s", team.name, exc)
        teams.append(team)
    return TeamAuditResult(teams, list(required_roles))


def format_report(result: TeamAuditResult, server: str = "") -> str:
    empty = result.empty_required_roles()
    disabled = result.disabled_members()
    text = header(
        "Team membership audit",
        Server=server,
        Teams=len(result.teams),
        Required_roles=", ".join(result.required_roles) or "(none)",
        Problems=f"{len(empty)} empty required roles, {len(disabled)} disabled members",
    )
    text += section(
        "Required roles with nobody in them",
        table([(t.kind, t.name, r) for t, r in empty], ["Kind", "Container", "Role"]),
    )
    text += section(
        "Disabled users still on a team",
        table([(t.kind, t.name, r, m.name, m.full_name) for t, r, m in disabled], ["Kind", "Container", "Role", "Login", "Name"]),
    )
    body = ""
    for t in result.teams:
        body += f"\n[{t.kind}] {t.name}\n"
        if t.error:
            body += f"    could not read team: {t.error}\n"
            continue
        for role, members in sorted(t.roles.items()):
            names = ", ".join(
                f"{m.name}{' (group)' if m.kind == 'Group' else ''}{' (disabled)' if m.disabled else ''}" for m in members
            )
            body += f"    {role}: {names or '(empty)'}\n"
    text += section("All teams", body)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--required-role", action="append", help="Role every container must fill (repeatable)")
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        result = audit_teams(client_from_args(args), args.required_role or REQUIRED_ROLES)
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(result, args.server))
    if result.teams and all(t.error for t in result.teams):
        log.error("No team could be read. Check CONTAINER_TEAM_ROLES in windchill_api/endpoints.py against $metadata.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
