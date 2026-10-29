# Windchill-Tools-Public

Python monorepo (a [uv](https://docs.astral.sh/uv/) workspace) of admin tools for PTC Windchill.

```
libs/windchill-api/      Client library for the Windchill OData REST API (Windchill REST Services)
scripts/                 Admin scripts (package: windchill_scripts)
tests/                   Tests that run every script against a fake Windchill server
```

## Setup

```sh
uv sync
```

Each script has a `SERVER = "windchill.example.com"` placeholder at the top. Set it there,
or pass `--server`, or set `WINDCHILL_SERVER`. Credentials come from `WINDCHILL_USER` and
`WINDCHILL_PASSWORD`, or you are prompted. Use an account that can see everything the
script covers (a site or organization administrator). For an internal certificate
authority, pass `--ca-bundle` or set `WINDCHILL_CA_BUNDLE`.

Every script writes a text report (`--output` to change the file name).

## Scripts

| Command | What it does |
| --- | --- |
| `find-illegal-folder-names` | Folders in products and libraries with illegal characters in their names |
| `rename-folders` | Renames those folders. **Dry run unless `--apply`** |
| `empty-folders` | Folders with nothing in them, including in any subfolder |
| `checked-out-report` | Everything checked out, by whom and for how long (`--min-days`, `--user`) |
| `duplicate-names` | Parts, documents and CAD documents whose names differ only by case, spaces or punctuation |
| `attribute-audit` | Blank required attributes, values outside an allowed list, bad characters in names. Edit `RULES` in the script |
| `inactive-users` | Users not seen in the web server access logs for N days (`--log`, `--days`) |
| `team-membership-audit` | Who holds each team role, empty required roles, disabled users on teams |
| `group-membership-snapshot` | Saves group membership and lists what changed since the last run |
| `stalled-workflow-tasks` | Open workflow tasks older than N days, by assignee, flagging disabled assignees |
| `change-aging` | Open problem reports, change requests, notices and tasks by state and age |
| `release-activity` | What was created, changed and released in the last N days, per product or library |
| `bulk-reassign FROM TO` | Moves a departing user's workflow tasks to someone else and lists their checkouts. **Dry run unless `--apply`** |
| `smoke-test` | Log in, search, open, download, and check every endpoint the tools use. Exits 1 on failure |
| `queue-monitor` | Failed and backed-up queue entries, read from the database. Exits 2 when something needs attention |
| `log-scanner` | Errors in the method server logs, grouped so each distinct problem shows once |
| `vault-usage` | Vault size and disk space, with growth and days until full. Exits 2 on a warning |

Run any of them with `uv run <command> --help`.

`queue-monitor`, `log-scanner` and `vault-usage` work on the server itself rather than
through REST. `queue-monitor` needs a database driver: `uv sync --extra oracle` or
`uv sync --extra sqlserver`.

## Using the scripts from another app

Each script is an importable module with a function that returns data, a
`format_report` function, and a `main` for the command line. Scripts do not depend
on each other, only on `windchill-api`.

```python
from windchill_api import WindchillClient
from windchill_scripts.checked_out_report import find_checked_out
from windchill_scripts.stalled_workflow_tasks import find_stalled_tasks

client = WindchillClient("https://plm.example.com/Windchill", "user", "password")
for item in find_checked_out(client, min_days=30):
    print(item.checked_out_by, item.number, round(item.days))
```

## Endpoints to check on your server

All OData entity set names live in `libs/windchill-api/src/windchill_api/endpoints.py`.
If your Windchill REST Services release names one differently, change it there; the
exact names are in `/Windchill/servlet/odata/<Domain>/$metadata`. Run `smoke-test`
first: it reports which endpoints answer.

- **Team roles** (`team-membership-audit`): PTC does not document a REST endpoint for
  container teams, so `CONTAINER_TEAM_ROLES` is a best guess. Check `DataAdmin/$metadata`.
- **Queue tables** (`queue-monitor`): the SQL follows the standard Windchill schema.
  Try it in a SQL client before relying on it.
- **Work items** (`stalled-workflow-tasks`, `bulk-reassign`): Windchill returns the
  work items the logged-in user may see, so run these as a site administrator.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
