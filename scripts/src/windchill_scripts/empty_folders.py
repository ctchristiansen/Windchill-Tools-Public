"""Find folders in every product and library that hold nothing, directly or in any subfolder.

Only the topmost folder of an empty branch is reported, since deleting it
removes the empty folders below it too.

Use from another app:
    from windchill_scripts.empty_folders import find_empty_folders
    result = find_empty_folders(client)

Run on its own:
    uv run empty-folders [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from windchill_api import Entity, WindchillClient, WindchillError
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.naming import visible
from windchill_api.report import header, section

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "empty_folders.txt"

log = logging.getLogger(__name__)


@dataclass
class EmptyFolder:
    kind: str
    container_id: str
    folder_id: str
    path: str
    empty_subfolders: int  # empty folders below this one


@dataclass
class EmptyFolderResult:
    containers: int = 0
    folders: int = 0
    empty_folders: list[EmptyFolder] = field(default_factory=list)
    failed_containers: list[str] = field(default_factory=list)


def _is_folder(entity: Entity) -> bool:
    return entity.get("@odata.type", "").endswith("Folder") or "wt.folder." in entity.get("ID", "")


def _walk(
    client: WindchillClient, container: Entity, folder: Entity, path: list[str], found: list[tuple], result: EmptyFolderResult
) -> int:
    """Post-order walk of one folder.

    Returns the number of folders in this branch if the whole branch is empty,
    or -1 if it holds anything. Empty branches directly under a non-empty
    folder are appended to ``found`` as ``(folder, path, size)``.
    """
    result.folders += 1
    cid = container["ID"]
    has_content = any(not _is_folder(e) for e in client.get_folder_contents(cid, folder["ID"], top=5))
    empty_children = []
    for child in client.get_subfolders(cid, folder["ID"]):
        child_path = path + [child.get("Name", "")]
        size = _walk(client, container, child, child_path, found, result)
        if size < 0:
            has_content = True
        else:
            empty_children.append((child, child_path, size))
    if not has_content:
        return 1 + sum(size for _, _, size in empty_children)
    found.extend(empty_children)
    return -1


def find_empty_folders(client: WindchillClient) -> EmptyFolderResult:
    result = EmptyFolderResult()
    targets = client.get_products_and_libraries()
    result.containers = len(targets)
    for kind, container in targets:
        name = container.get("Name", "")
        log.info("Scanning %s: %s", kind, name)
        found: list[tuple] = []
        try:
            for root in client.get_root_folders(container["ID"]):
                root_path = [root.get("Name", "")]
                size = _walk(client, container, root, root_path, found, result)
                if size >= 0:
                    found.append((root, root_path, size))
        except WindchillError as exc:
            log.warning("Could not scan %s %s: %s", kind, name, exc)
            result.failed_containers.append(f"{kind}: {name}")
            continue
        for folder, path, size in found:
            result.empty_folders.append(EmptyFolder(kind, container["ID"], folder["ID"], "/" + "/".join([name] + path), size - 1))
    return result


def format_report(result: EmptyFolderResult, server: str = "") -> str:
    text = header(
        "Empty Windchill folders",
        Server=server,
        Scanned=f"{result.containers} containers, {result.folders} folders",
        Found=f"{len(result.empty_folders)} empty folders (topmost of each empty branch)",
    )
    text += "\n"
    for f in result.empty_folders:
        extra = f"  (+{f.empty_subfolders} empty subfolders)" if f.empty_subfolders else ""
        text += f"[{f.kind}] {visible(f.path)}{extra}\n"
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
        result = find_empty_folders(client_from_args(args))
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(result, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
