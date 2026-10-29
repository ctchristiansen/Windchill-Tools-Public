"""A small in-process imitation of Windchill REST Services for tests.

It serves only the requests the tools make, shaped like real WRS responses.
Writes (rename, reassign, undo checkout) are recorded in ``FakeWindchill.writes``.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

NOW = datetime.now(timezone.utc)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def state(v: str, d: str | None = None) -> dict:
    return {"Value": v, "Display": d or v.title()}


PRODUCT = "#PTC.DataAdmin.ProductContainer"
LIBRARY = "#PTC.DataAdmin.LibraryContainer"
PROJECT = "#PTC.DataAdmin.ProjectContainer"


def build_data() -> dict:
    containers = [
        {"ID": "OR:wt.pdmlink.PDMLinkProduct:1", "Name": "Drone", "@odata.type": PRODUCT},
        {"ID": "OR:wt.pdmlink.PDMLinkProduct:2", "Name": "Rover", "@odata.type": PRODUCT},
        {"ID": "OR:wt.inf.library.WTLibrary:3", "Name": "Lib's Standard Parts", "@odata.type": LIBRARY},
        {"ID": "OR:wt.projmgmt.admin.Project2:4", "Name": "Proj", "@odata.type": PROJECT},
    ]
    # parent id -> child folders
    folders = {
        "OR:wt.pdmlink.PDMLinkProduct:1": [{"ID": "f1", "Name": "CAD"}, {"ID": "f2", "Name": "Bad:Name"}],
        "f1": [{"ID": "f3", "Name": "Sub ?"}, {"ID": "f4", "Name": "Archive"}],
        "f4": [{"ID": "f5", "Name": "Old"}, {"ID": "f6", "Name": "Older"}],
        "f6": [{"ID": "f7", "Name": "zero​width "}],
        "OR:wt.pdmlink.PDMLinkProduct:2": [{"ID": "g1", "Name": "Docs"}, {"ID": "g2", "Name": "Docs?"}],
        "OR:wt.inf.library.WTLibrary:3": [{"ID": "h1", "Name": "Fasteners<M3>"}],
    }
    contents = {
        "f1": [{"ID": "OR:wt.part.WTPart:100", "Name": "Frame"}],
        "f2": [{"ID": "OR:wt.part.WTPart:101", "Name": "Arm"}],
        "f3": [{"ID": "OR:wt.folder.SubFolder:x", "@odata.type": "#PTC.DataAdmin.Folder"}],  # only a folder entry
        "g1": [{"ID": "OR:wt.doc.WTDocument:200", "Name": "Spec"}],
        "h1": [{"ID": "OR:wt.part.WTPart:102", "Name": "Screw"}],
    }
    parts = [
        {
            "ID": "OR:wt.part.WTPart:100",
            "Number": "P-100",
            "Name": "Frame",
            "Version": "A.3",
            "State": state("INWORK", "In Work"),
            "CheckoutState": state("c/o", "Checked out"),
            "ModifiedBy": "jsmith",
            "CreatedOn": ts(400),
            "LastModified": ts(45),
            "DefaultUnit": state("ea"),
            "Source": state("make", "Make"),
            "Context": {"Name": "Drone"},
        },
        {
            "ID": "OR:wt.part.WTPart:100w",
            "Number": "P-100",
            "Name": "Frame",
            "Version": "A.3",
            "State": state("INWORK"),
            "CheckoutState": state("wrk", "Working"),
            "ModifiedBy": "jsmith",
            "CreatedOn": ts(400),
            "LastModified": ts(2),
            "DefaultUnit": state("ea"),
            "Source": state("make"),
        },
        {
            "ID": "OR:wt.part.WTPart:101",
            "Number": "P-101",
            "Name": "FRAME ",
            "Version": "A.1",
            "State": state("RELEASED", "Released"),
            "CheckoutState": state("c/i", "Checked in"),
            "CreatedOn": ts(3),
            "LastModified": ts(1),
            "DefaultUnit": state("ea"),
            "Source": state("bogus"),
            "Context": {"Name": "Drone"},
        },
        {
            "ID": "OR:wt.part.WTPart:102",
            "Number": "P-102",
            "Name": "Screw M3",
            "Version": "B.1",
            "State": state("RELEASED"),
            "CheckoutState": state("c/o"),
            "ModifiedBy": "mjones",
            "CreatedOn": ts(900),
            "LastModified": ts(5),
            "DefaultUnit": None,
            "Source": state("buy"),
            "Context": {"Name": "Lib's Standard Parts"},
        },
        {
            "ID": "OR:wt.part.WTPart:103",
            "Number": "P-103",
            "Name": "Screw-M3",
            "Version": "A.1",
            "State": state("INWORK"),
            "CheckoutState": state("c/i"),
            "CreatedOn": ts(30),
            "LastModified": ts(30),
            "DefaultUnit": state("ea"),
            "Source": state("buy"),
        },
    ]
    documents = [
        {
            "ID": "OR:wt.doc.WTDocument:200",
            "Number": "D-200",
            "Name": "Spec",
            "Version": "A.1",
            "State": state("INWORK"),
            "CheckoutState": state("c/i"),
            "CreatedOn": ts(10),
            "LastModified": ts(10),
            "PrimaryContent": {"Content": {"URL": "/Windchill/content/D-200.pdf"}},
        },
    ]
    users = [
        {
            "ID": "OR:wt.org.WTUser:1",
            "Name": "jsmith",
            "FullName": "Jo Smith",
            "EMail": "jo@example.com",
            "Groups": [{"Name": "Engineering"}, {"Name": "CAD Users"}],
        },
        {
            "ID": "OR:wt.org.WTUser:2",
            "Name": "mjones",
            "FullName": "Max Jones",
            "EMail": "max@example.com",
            "Groups": [{"Name": "Engineering"}],
        },
        {
            "ID": "OR:wt.org.WTUser:3",
            "Name": "olduser",
            "FullName": "Old User",
            "EMail": "",
            "Disabled": True,
            "Groups": [{"Name": "CAD Users"}],
        },
        {"ID": "OR:wt.org.WTUser:4", "Name": "quiet", "FullName": "Quiet Person", "EMail": "q@example.com", "Groups": []},
    ]
    work_items = [
        {
            "ID": "OR:wt.workflow.work.WorkItem:1",
            "Status": state("POTENTIAL", "Potential"),
            "CreatedOn": ts(40),
            "Activity": {"Name": "Review Change"},
            "Owner": users[0],
            "Subject": "CN-1",
        },
        {
            "ID": "OR:wt.workflow.work.WorkItem:2",
            "Status": state("ACCEPTED", "Accepted"),
            "CreatedOn": ts(20),
            "DueDate": ts(5),
            "Activity": {"Name": "Approve"},
            "Owner": users[2],
            "Subject": "CR-7",
        },
        {
            "ID": "OR:wt.workflow.work.WorkItem:3",
            "Status": state("COMPLETED", "Completed"),
            "CreatedOn": ts(90),
            "Activity": {"Name": "Old"},
            "Owner": users[0],
            "Subject": "CN-0",
        },
        {
            "ID": "OR:wt.workflow.work.WorkItem:4",
            "Status": state("POTENTIAL"),
            "CreatedOn": ts(2),
            "Activity": {"Name": "Fresh"},
            "Owner": users[1],
            "Subject": "CN-2",
        },
    ]
    changes = {
        "ChangeMgmt/ProblemReports": [
            {
                "Number": "PR-1",
                "Name": "Cracked arm",
                "State": state("OPEN", "Open"),
                "CreatedOn": ts(200),
                "LastModified": ts(120),
                "CreatedBy": "jsmith",
            },
        ],
        "ChangeMgmt/ChangeRequests": [
            {
                "Number": "CR-7",
                "Name": "Thicker arm",
                "State": state("UNDERREVIEW", "Under Review"),
                "CreatedOn": ts(60),
                "LastModified": ts(40),
                "CreatedBy": "mjones",
            },
            {"Number": "CR-6", "Name": "Done", "State": state("RESOLVED"), "CreatedOn": ts(300), "LastModified": ts(250)},
        ],
        "ChangeMgmt/ChangeNotices": [
            {
                "Number": "CN-1",
                "Name": "Arm update",
                "State": state("IMPLEMENTATION"),
                "CreatedOn": ts(4),
                "LastModified": ts(1),
                "CreatedBy": "jsmith",
            },
        ],
        "ChangeMgmt/ChangeTasks": [],
    }
    teams = {
        "OR:wt.pdmlink.PDMLinkProduct:1": [
            {"Name": "Product Manager", "Members": [users[1]]},
            {"Name": "Designer", "Members": [users[0], users[2]]},
        ],
        "OR:wt.pdmlink.PDMLinkProduct:2": [
            {"Name": "Product Manager", "Members": []},
            {
                "Name": "Members",
                "Members": [{"ID": "OR:wt.org.WTGroup:9", "Name": "Engineering", "@odata.type": "#PTC.PrincipalMgmt.Group"}],
            },
        ],
    }
    return {
        "containers": containers,
        "folders": folders,
        "contents": contents,
        "parts": parts,
        "documents": documents,
        "cad": [],
        "users": users,
        "work_items": work_items,
        "changes": changes,
        "teams": teams,
    }


KEY = r"\('((?:[^']|'')*)'\)"


def _key(s: str) -> str:
    return unquote(s).replace("''", "'")


class FakeWindchill:
    def __init__(self) -> None:
        self.data = build_data()
        self.writes: list[tuple[str, str, object]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/Windchill"

    def __enter__(self) -> FakeWindchill:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()

    # ------------------------------------------------------------- routing

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: object = None, raw: bytes | None = None) -> None:
                data = raw if raw is not None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json" if raw is None else "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                code, body = fake.get(self.path)
                if isinstance(body, bytes):
                    self._send(code, raw=body)
                else:
                    self._send(code, body)

            def _write(self, method: str):
                if self.headers.get("CSRF_NONCE") != "nonce-1":
                    return self._send(400, {"error": "INVALID_NONCE"})
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                code, resp = fake.write(method, self.path, body)
                self._send(code, resp)

            def do_POST(self):
                self._write("POST")

            def do_PATCH(self):
                self._write("PATCH")

        return Handler

    def get(self, raw_path: str) -> tuple[int, object]:
        url = urlparse(raw_path)
        path = unquote(url.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not path.startswith("/Windchill/"):
            return 404, {"error": "not found"}
        if path == "/Windchill/content/D-200.pdf":
            return 200, b"%PDF-1.4 fake"
        p = url.path.split("/servlet/odata/", 1)[-1]
        d = self.data

        if p == "PTC/GetCSRFToken()":
            return 200, {"NonceKey": "CSRF_NONCE", "NonceValue": "nonce-1"}
        if p == "DataAdmin/Containers":
            return self._page(d["containers"], q, p)
        m = re.fullmatch(r"DataAdmin/Containers" + KEY + r"/ContainerTeam/Roles", p)
        if m:
            cid = _key(m[1])
            if cid not in d["teams"]:
                return 404, {"error": "no team"}
            return 200, {"value": d["teams"][cid]}
        m = re.fullmatch(r"DataAdmin/Containers" + KEY + r"/Folders(?:" + KEY + r"/(Folders|FolderContents))?", p)
        if m:
            cid, fid, nav = _key(m[1]), m[2] and _key(m[2]), m[3]
            if not fid:
                return self._page(d["folders"].get(cid, []), q, p)
            if nav == "Folders":
                return self._page(d["folders"].get(fid, []), q, p)
            return self._page(d["contents"].get(fid, []), q, p)

        sets = {
            "ProdMgmt/Parts": d["parts"],
            "DocMgmt/Documents": d["documents"],
            "CADDocumentMgmt/CADDocuments": d["cad"],
            "PrincipalMgmt/Users": d["users"],
            "Workflow/WorkItems": d["work_items"],
            **d["changes"],
        }
        m = re.fullmatch(r"(\w+/\w+)" + KEY, p)
        if m and m[1] in sets:
            for e in sets[m[1]]:
                if e.get("ID") == _key(m[2]):
                    return 200, e
            return 404, {"error": "not found"}
        if p in sets:
            items = sets[p]
            if "$filter" in q:
                try:
                    items = [e for e in items if self._match(e, q["$filter"])]
                except ValueError as exc:
                    return 400, {"error": {"message": str(exc)}}
            if p == "PrincipalMgmt/Users" and q.get("$expand") != "Groups":
                items = [{k: v for k, v in u.items() if k != "Groups"} for u in items]
            return self._page(items, q, p)
        return 404, {"error": f"no route for {p}"}

    @staticmethod
    def _match(entity: dict, flt: str) -> bool:
        m = re.fullmatch(r"(\w+) eq '((?:[^']|'')*)'", flt)
        if m:
            return str(entity.get(m[1])) == m[2].replace("''", "'")
        m = re.fullmatch(r"LastModified ge (\S+)", flt)
        if m:
            return entity.get("LastModified", "") >= m[1]
        raise ValueError(f"The query specified in the URI is not valid: non-filterable property in {flt}")

    def _page(self, items: list, q: dict, path: str, size: int = 2) -> tuple[int, dict]:
        if "$top" in q:
            return 200, {"value": items[: int(q["$top"])]}
        skip = int(q.get("$skiptoken", 0))
        body: dict = {"value": items[skip : skip + size]}
        if skip + size < len(items):
            rest = "&".join(f"{k}={v}" for k, v in q.items() if k != "$skiptoken")
            body["@odata.nextLink"] = f"{self.base_url}/servlet/odata/{path}?{rest}&$skiptoken={skip + size}"
        return 200, body

    def write(self, method: str, raw_path: str, body: object) -> tuple[int, object]:
        p = urlparse(raw_path).path.split("/servlet/odata/", 1)[-1]
        self.writes.append((method, unquote(p), body))
        m = re.fullmatch(r"DataAdmin/Containers" + KEY + r"/Folders" + KEY, p)
        if method == "PATCH" and m:
            fid = _key(m[2])
            for children in self.data["folders"].values():
                for f in children:
                    if f["ID"] == fid:
                        f["Name"] = body["Name"]
                        return 200, f
            return 404, {"error": "no folder"}
        if method == "POST" and p == "Workflow/ReassignWorkItems":
            return 200, {"value": body["WorkItems"]}
        if method == "POST" and p.endswith(".UndoCheckOut"):
            return 200, {}
        return 404, {"error": f"no route for {method} {p}"}
