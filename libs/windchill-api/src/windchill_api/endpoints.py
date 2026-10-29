"""OData entity sets and type names used by the tools.

Paths are relative to ``/Windchill/servlet/odata``. If your server's Windchill
REST Services release names something differently, change it here; check
``/Windchill/servlet/odata/<Domain>/$metadata`` for the exact names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySet:
    label: str
    path: str
    namespace: str  # used for bound actions, e.g. PTC.ProdMgmt.UndoCheckOut


PARTS = EntitySet("Part", "ProdMgmt/Parts", "PTC.ProdMgmt")
DOCUMENTS = EntitySet("Document", "DocMgmt/Documents", "PTC.DocMgmt")
CAD_DOCUMENTS = EntitySet("CAD Document", "CADDocumentMgmt/CADDocuments", "PTC.CADDocumentMgmt")

# Objects that can be checked out (Workable).
WORKABLE_SETS = (PARTS, DOCUMENTS, CAD_DOCUMENTS)

PROBLEM_REPORTS = EntitySet("Problem Report", "ChangeMgmt/ProblemReports", "PTC.ChangeMgmt")
CHANGE_REQUESTS = EntitySet("Change Request", "ChangeMgmt/ChangeRequests", "PTC.ChangeMgmt")
CHANGE_NOTICES = EntitySet("Change Notice", "ChangeMgmt/ChangeNotices", "PTC.ChangeMgmt")
CHANGE_TASKS = EntitySet("Change Task", "ChangeMgmt/ChangeTasks", "PTC.ChangeMgmt")
VARIANCES = EntitySet("Variance", "ChangeMgmt/Variances", "PTC.ChangeMgmt")

CHANGE_SETS = (PROBLEM_REPORTS, CHANGE_REQUESTS, CHANGE_NOTICES, CHANGE_TASKS, VARIANCES)

CONTAINERS = "DataAdmin/Containers"
USERS = "PrincipalMgmt/Users"
GROUPS = "PrincipalMgmt/Groups"
WORK_ITEMS = "Workflow/WorkItems"
REASSIGN_WORK_ITEMS = "Workflow/ReassignWorkItems"
CSRF_TOKEN = "PTC/GetCSRFToken()"

PRODUCT_TYPE = "PTC.DataAdmin.ProductContainer"
LIBRARY_TYPE = "PTC.DataAdmin.LibraryContainer"

# Container team: PTC does not publicly document a REST endpoint for team roles,
# so this path is a best guess (container -> team -> roles, each expanded with
# its members). Check DataAdmin/$metadata on your server and adjust if needed.
CONTAINER_TEAM_ROLES = "DataAdmin/Containers{key}/ContainerTeam/Roles"
TEAM_ROLE_MEMBERS_EXPAND = "Members"
