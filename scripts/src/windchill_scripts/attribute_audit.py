"""Check parts, documents and CAD documents for blank or invalid attribute values and bad names.

Edit RULES below for your site's attributes. Each object number is checked
once (the first version the server returns).

Use from another app:
    from windchill_scripts.attribute_audit import Rule, audit
    issues = audit(client, [Rule(endpoints.PARTS, required=["Source"])])

Run on its own:
    uv run attribute-audit [--output FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from windchill_api import EntitySet, WindchillClient, WindchillError, endpoints
from windchill_api.cli import add_connection_args, client_from_args, setup_logging, write_report
from windchill_api.naming import find_name_problems, visible
from windchill_api.report import header, section, table
from windchill_api.values import display, value

# TODO: enter your Windchill server name (host, optionally with :port).
SERVER = "windchill.example.com"

OUTPUT_FILE = "attribute_audit.txt"

log = logging.getLogger(__name__)


@dataclass
class Rule:
    entity_set: EntitySet
    # Attributes that must not be blank. Use the OData property names from $metadata.
    required: list[str] = field(default_factory=list)
    # Attribute -> allowed values (internal value or display text both accepted).
    allowed: dict[str, list[str]] = field(default_factory=dict)
    # Also flag illegal/hidden characters and stray whitespace in Name and Number.
    check_names: bool = True


# TODO: replace the examples with your site's required attributes and value lists.
RULES = [
    Rule(
        endpoints.PARTS, required=["Name", "Number", "DefaultUnit", "Source"], allowed={"Source": ["make", "buy", "singlesource"]}
    ),
    Rule(endpoints.DOCUMENTS, required=["Name", "Number"]),
    Rule(endpoints.CAD_DOCUMENTS, required=["Name", "Number"]),
]


@dataclass
class Issue:
    type: str
    number: str
    name: str
    attribute: str
    problem: str


def _blank(v: object) -> bool:
    v = value(v)
    return v is None or (isinstance(v, str) and not v.strip())


def check_entity(entity: dict, rule: Rule) -> list[tuple[str, str]]:
    """Return ``(attribute, problem)`` pairs for one entity."""
    problems = []
    for attr in rule.required:
        if _blank(entity.get(attr)):
            problems.append((attr, "blank" if attr in entity else "missing"))
    for attr, allowed in rule.allowed.items():
        v = entity.get(attr)
        if _blank(v):
            continue
        ok = {a.lower() for a in allowed}
        if str(value(v)).lower() not in ok and display(v).lower() not in ok:
            problems.append((attr, f"value {display(v)!r} is not in the allowed list"))
    if rule.check_names:
        for attr in ("Name", "Number"):
            text = entity.get(attr)
            if isinstance(text, str):
                for p in find_name_problems(text):
                    problems.append((attr, p))
    return problems


def audit(client: WindchillClient, rules: Iterable[Rule] = RULES) -> list[Issue]:
    issues = []
    for rule in rules:
        es = rule.entity_set
        log.info("Checking %ss", es.label)
        seen: set[str] = set()
        for e in client.query(es.path):
            number = display(e.get("Number"))
            if number in seen:
                continue
            seen.add(number)
            for attr, problem in check_entity(e, rule):
                issues.append(Issue(es.label, number, display(e.get("Name")), attr, problem))
    return issues


def _kind(problem: str) -> str:
    """Group problems for the summary, e.g. all "not in the allowed list" values together."""
    if "allowed list" in problem:
        return "not in allowed list"
    return problem.split(":")[0]


def format_report(issues: list[Issue], server: str = "") -> str:
    summary = Counter((i.type, i.attribute, _kind(i.problem)) for i in issues)
    text = header("Attribute audit", Server=server, Found=f"{len(issues)} problems")
    text += section("Summary", table([(*k, n) for k, n in summary.most_common()], ["Type", "Attribute", "Problem", "Count"]))
    rows = [(i.type, i.number, visible(i.name), i.attribute, i.problem) for i in issues]
    text += section("Details", table(rows, ["Type", "Number", "Name", "Attribute", "Problem"]))
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_connection_args(parser, SERVER)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args(argv)
    setup_logging()

    try:
        issues = audit(client_from_args(args))
    except WindchillError as exc:
        log.error("%s", exc)
        return 1
    write_report(args.output, format_report(issues, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
