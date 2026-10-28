"""Minimal client for the PTC Windchill OData REST API (Windchill REST Services).

Only read operations are implemented. Write operations would additionally
need a CSRF nonce (``GET /servlet/odata/PTC/GetCSRFToken()``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

PRODUCT_TYPE = "PTC.DataAdmin.ProductContainer"
LIBRARY_TYPE = "PTC.DataAdmin.LibraryContainer"


class WindchillError(RuntimeError):
    """Raised when the Windchill server returns an error response."""


@dataclass
class FolderInfo:
    """A folder found while walking a container's folder tree."""

    container: dict[str, Any]
    folder: dict[str, Any]
    path: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.folder.get("Name", "")

    @property
    def full_path(self) -> str:
        """Path as ``/<container name>/<folder>/<subfolder>/...``."""
        return "/" + "/".join([self.container.get("Name", "")] + self.path)


def odata_key(value: str) -> str:
    """Format a string as an OData key literal, e.g. ``('OR:wt.folder.SubFolder:1')``."""
    escaped = value.replace("'", "''")
    return "('" + quote(escaped, safe=":") + "')"


class WindchillClient:
    """Read-only client for Windchill's OData endpoints.

    ``base_url`` is the Windchill web app URL, e.g. ``https://plm.example.com/Windchill``.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify: bool | str = True,
        timeout: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.odata_url = f"{self.base_url}/servlet/odata"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------ HTTP

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET an OData path (relative to ``/servlet/odata``) or an absolute URL."""
        url = path if path.startswith("http") else f"{self.odata_url}/{path.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        if not response.ok:
            raise WindchillError(
                f"GET {response.url} failed: {response.status_code} {response.text[:500]}"
            )
        return response.json()

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Yield every entity of a collection, following ``@odata.nextLink`` paging."""
        data = self.get(path, params)
        while True:
            yield from data.get("value", [])
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break
            data = self.get(next_link)

    # ------------------------------------------------------------ Containers

    def get_containers(self) -> list[dict[str, Any]]:
        """All application containers visible to the user (products, libraries, projects, ...)."""
        return list(self.get_all("DataAdmin/Containers"))

    def get_products(self) -> list[dict[str, Any]]:
        return [c for c in self.get_containers() if c.get("@odata.type", "").endswith(PRODUCT_TYPE)]

    def get_libraries(self) -> list[dict[str, Any]]:
        return [c for c in self.get_containers() if c.get("@odata.type", "").endswith(LIBRARY_TYPE)]

    # --------------------------------------------------------------- Folders

    def get_root_folders(self, container_id: str) -> list[dict[str, Any]]:
        """Top-level folders of a container."""
        return list(self.get_all(f"DataAdmin/Containers{odata_key(container_id)}/Folders"))

    def get_subfolders(self, container_id: str, folder_id: str) -> list[dict[str, Any]]:
        """Direct child folders of a folder."""
        return list(
            self.get_all(
                f"DataAdmin/Containers{odata_key(container_id)}"
                f"/Folders{odata_key(folder_id)}/Folders"
            )
        )

    def walk_folders(self, container: dict[str, Any]) -> Iterator[FolderInfo]:
        """Yield every folder in a container, depth first, with its path."""
        container_id = container["ID"]
        stack = [FolderInfo(container, f, [f.get("Name", "")]) for f in self.get_root_folders(container_id)]
        stack.reverse()
        while stack:
            info = stack.pop()
            yield info
            try:
                children = self.get_subfolders(container_id, info.folder["ID"])
            except WindchillError as exc:
                log.warning("Could not list subfolders of %s: %s", info.full_path, exc)
                continue
            stack.extend(
                FolderInfo(container, child, info.path + [child.get("Name", "")])
                for child in reversed(children)
            )
