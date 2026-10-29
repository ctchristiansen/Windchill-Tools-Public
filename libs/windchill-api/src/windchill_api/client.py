"""Client for the PTC Windchill OData REST API (Windchill REST Services)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

from windchill_api import endpoints
from windchill_api.endpoints import EntitySet

log = logging.getLogger(__name__)

Entity = dict[str, Any]


class WindchillError(RuntimeError):
    """Raised when the Windchill server returns an error response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FolderInfo:
    """A folder found while walking a container's folder tree."""

    container: Entity
    folder: Entity
    path: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.folder["ID"]

    @property
    def name(self) -> str:
        return self.folder.get("Name", "")

    @property
    def container_id(self) -> str:
        return self.container["ID"]

    @property
    def full_path(self) -> str:
        """Path as ``/<container name>/<folder>/<subfolder>/...``."""
        return "/" + "/".join([self.container.get("Name", "")] + self.path)


def odata_key(value: str) -> str:
    """Format a string as an OData key literal, e.g. ``('OR:wt.folder.SubFolder:1')``."""
    escaped = value.replace("'", "''")
    return "('" + quote(escaped, safe=":") + "')"


def odata_string(value: str) -> str:
    """Format a string as an OData string literal for use in ``$filter``."""
    return "'" + value.replace("'", "''") + "'"


def is_type(entity: Entity, type_name: str) -> bool:
    return entity.get("@odata.type", "").endswith(type_name)


class WindchillClient:
    """Client for Windchill's OData endpoints.

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
        page_size: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.odata_url = f"{self.base_url}/servlet/odata"
        self.timeout = timeout
        self.page_size = page_size
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify
        self.session.headers.update({"Accept": "application/json"})
        self._nonce: str | None = None

    # ------------------------------------------------------------------ HTTP

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.odata_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        response = self.session.request(method, self.url(path), **kwargs)
        if not response.ok:
            raise WindchillError(
                f"{method} {response.url} failed: {response.status_code} {response.text[:500]}",
                response.status_code,
            )
        return response

    def get(self, path: str, params: dict[str, Any] | None = None) -> Entity:
        """GET an OData path (relative to ``/servlet/odata``) or an absolute URL."""
        return self.request("GET", path, params=params).json()

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Entity]:
        """Yield every entity of a collection, following ``@odata.nextLink`` paging."""
        headers = {"Prefer": f"odata.maxpagesize={self.page_size}"} if self.page_size else None
        data = self.request("GET", path, params=params, headers=headers).json()
        while True:
            yield from data.get("value", [])
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break
            data = self.request("GET", next_link, headers=headers).json()

    def query(
        self,
        path: str,
        *,
        select: list[str] | None = None,
        filter: str | None = None,
        expand: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
    ) -> Iterator[Entity]:
        """Yield entities from a collection using the common OData query options."""
        params: dict[str, Any] = {}
        if select:
            params["$select"] = ",".join(select)
        if filter:
            params["$filter"] = filter
        if expand:
            params["$expand"] = expand
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = top
        if top is not None:
            # A $top query should not follow paging links past the limit.
            yield from self.get(path, params).get("value", [])
        else:
            yield from self.get_all(path, params)

    def query_where(
        self,
        path: str,
        server_filter: str | None,
        predicate: Callable[[Entity], bool],
        **kwargs: Any,
    ) -> Iterator[Entity]:
        """Yield entities matching ``predicate``.

        ``server_filter`` is tried first to keep the result set small. Some
        Windchill properties are not filterable, so if the server rejects the
        filter, every entity is fetched and ``predicate`` is applied locally.
        ``predicate`` is applied either way.
        """
        if server_filter:
            try:
                yield from (e for e in self.query(path, filter=server_filter, **kwargs) if predicate(e))
                return
            except WindchillError as exc:
                if exc.status_code not in (400, 501):
                    raise
                log.info("Server rejected $filter on %s; filtering locally instead", path)
        yield from (e for e in self.query(path, **kwargs) if predicate(e))

    # --------------------------------------------------------------- Writing

    def csrf_token(self, refresh: bool = False) -> str:
        """Nonce that Windchill requires on every POST, PATCH, PUT and DELETE."""
        if self._nonce is None or refresh:
            self._nonce = self.get(endpoints.CSRF_TOKEN)["NonceValue"]
        return self._nonce

    def write(self, method: str, path: str, body: Any = None) -> Entity:
        headers = {"CSRF_NONCE": self.csrf_token(), "Content-Type": "application/json"}
        response = self.request(method, path, json=body, headers=headers)
        return response.json() if response.content else {}

    def post(self, path: str, body: Any = None) -> Entity:
        return self.write("POST", path, body)

    def patch(self, path: str, body: Any) -> Entity:
        return self.write("PATCH", path, body)

    # ------------------------------------------------------------ Containers

    def get_containers(self) -> list[Entity]:
        """All application containers visible to the user (products, libraries, projects, ...)."""
        return list(self.get_all(endpoints.CONTAINERS))

    def get_products(self) -> list[Entity]:
        return [c for c in self.get_containers() if is_type(c, endpoints.PRODUCT_TYPE)]

    def get_libraries(self) -> list[Entity]:
        return [c for c in self.get_containers() if is_type(c, endpoints.LIBRARY_TYPE)]

    def get_products_and_libraries(self) -> list[tuple[str, Entity]]:
        """``("Product" | "Library", container)`` pairs, sorted by kind then name."""
        kinds = {endpoints.PRODUCT_TYPE: "Product", endpoints.LIBRARY_TYPE: "Library"}
        result = [(kind, c) for c in self.get_containers() for type_name, kind in kinds.items() if is_type(c, type_name)]
        result.sort(key=lambda t: (t[0], t[1].get("Name", "").lower()))
        return result

    # --------------------------------------------------------------- Folders

    @staticmethod
    def folder_path(container_id: str, folder_id: str | None = None) -> str:
        path = f"{endpoints.CONTAINERS}{odata_key(container_id)}/Folders"
        return path + odata_key(folder_id) if folder_id else path

    def get_root_folders(self, container_id: str) -> list[Entity]:
        """Top-level folders of a container."""
        return list(self.get_all(self.folder_path(container_id)))

    def get_subfolders(self, container_id: str, folder_id: str) -> list[Entity]:
        """Direct child folders of a folder."""
        return list(self.get_all(self.folder_path(container_id, folder_id) + "/Folders"))

    def get_folder_contents(self, container_id: str, folder_id: str, top: int | None = None) -> list[Entity]:
        """Objects (parts, documents, ...) directly inside a folder."""
        path = self.folder_path(container_id, folder_id) + "/FolderContents"
        if top is not None:
            return self.get(path, {"$top": top}).get("value", [])
        return list(self.get_all(path))

    def rename_folder(self, container_id: str, folder_id: str, new_name: str) -> Entity:
        return self.patch(self.folder_path(container_id, folder_id), {"Name": new_name})

    def walk_folders(self, container: Entity) -> Iterator[FolderInfo]:
        """Yield every folder in a container, depth first, with its path."""
        container_id = container["ID"]
        stack = [FolderInfo(container, f, [f.get("Name", "")]) for f in self.get_root_folders(container_id)]
        stack.reverse()
        while stack:
            info = stack.pop()
            yield info
            try:
                children = self.get_subfolders(container_id, info.id)
            except WindchillError as exc:
                log.warning("Could not list subfolders of %s: %s", info.full_path, exc)
                continue
            stack.extend(FolderInfo(container, child, info.path + [child.get("Name", "")]) for child in reversed(children))

    # ------------------------------------------------------- Users & groups

    def get_users(self, expand_groups: bool = False) -> list[Entity]:
        return list(self.query(endpoints.USERS, expand="Groups" if expand_groups else None))

    def find_user(self, name: str) -> Entity | None:
        """Look up a user by login name."""
        users = list(self.query(endpoints.USERS, filter=f"Name eq {odata_string(name)}"))
        return users[0] if users else None

    # ------------------------------------------------------------- Workflow

    def get_work_items(self, filter: str | None = None) -> list[Entity]:
        """Open and completed work items visible to the logged-in user."""
        return list(self.query(endpoints.WORK_ITEMS, filter=filter, expand="Activity,Owner"))

    def reassign_work_items(self, work_item_ids: list[str], user_id: str, comment: str = "") -> Entity:
        body: dict[str, Any] = {
            "WorkItems": [{"ID": i} for i in work_item_ids],
            "User": {"ID": user_id},
        }
        if comment:
            body["Comment"] = comment
        return self.post(endpoints.REASSIGN_WORK_ITEMS, body)

    # ------------------------------------------------------- Workable objects

    def undo_checkout(self, entity_set: EntitySet, object_id: str) -> Entity:
        """Undo a checkout. Changes made in the working copy are discarded."""
        return self.post(f"{entity_set.path}{odata_key(object_id)}/{entity_set.namespace}.UndoCheckOut")
