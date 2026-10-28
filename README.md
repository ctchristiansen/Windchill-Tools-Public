# Windchill-Tools-Public

Python monorepo (a [uv](https://docs.astral.sh/uv/) workspace) of tools for PTC Windchill.

```
libs/windchill-api/   Client library for the Windchill OData REST API (WRS)
scripts/              Standalone scripts that use the library
```

## Setup

```sh
uv sync
```

## Scripts

### `find_illegal_folder_names.py`

Walks every folder in every Product and Library and writes the full path of each
folder whose name contains illegal characters (`/ \ : * ? " < > |`), hidden/control
characters, or leading/trailing whitespace to `illegal_folder_names.txt`.

1. Set `SERVER` at the top of `scripts/find_illegal_folder_names.py`.
2. Run it with an account that can see all products and libraries (e.g. a site or org admin):

```sh
export WINDCHILL_USER=wcadmin          # optional; prompted for if not set
export WINDCHILL_PASSWORD=...          # optional; prompted for if not set
uv run scripts/find_illegal_folder_names.py
```

Requires Windchill REST Services (the `DataAdmin` OData domain) to be enabled on the server.
