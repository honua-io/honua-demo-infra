#!/usr/bin/env python3
"""Classify a saved Terraform plan and render a redacted Markdown report.

Usage:
  terraform show -json tfplan > plan.json
  scripts/plan-report.py --plan plan.json --title "stacks/aws" --out report.md \
      [--stack aws]   # apply that stack's standing entries from stacks/drift-stacks.json

Exit codes: 0 = no changes, 2 = changes (drift, or a PR's proposed change),
1 = usage or input error.

Any resource change other than a read or a no-op, and any output change,
counts. The only exception is a `standing` entry in stacks/drift-stacks.json:
an exact, issue-linked provider artifact (address, actions, ONE attribute and
its exact before and after values). It is listed in the report and never
hides anything else on the same resource.

The repository and its Actions logs are public, so the report never prints a
value Terraform marks sensitive in either the before or the after state, and
never prints the plan's own text output. It shows addresses, actions, the
changed top-level attributes and their non-sensitive values (truncated).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "stacks" / "drift-stacks.json"
MAX_VALUE = 240
SENSITIVE = "(sensitive)"
UNKNOWN = "(known after apply)"


@dataclass
class Result:
    status: str
    changes: list = field(default_factory=list)
    standing: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    drift: list = field(default_factory=list)


def validate_standing(entries: list) -> None:
    for entry in entries:
        missing = [k for k in ("address", "actions", "attribute", "before", "after", "issue") if k not in entry]
        if missing:
            raise SystemExit(f"standing entry {entry.get('address')} lacks {missing}")
        if not str(entry["issue"]).startswith("https://github.com/"):
            raise SystemExit(f"standing entry {entry['address']} must link the issue that removes it")
        if entry["actions"] != ["update"]:
            raise SystemExit(f"standing entry {entry['address']} may only tolerate an in-place update")


def changed_keys(before, after) -> list[str]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def is_standing(rc: dict, entries: list) -> dict | None:
    ch = rc["change"]
    for entry in entries:
        if entry["address"] != rc["address"] or entry["actions"] != ch["actions"]:
            continue
        if any_true(ch.get("after_unknown")):
            continue
        if changed_keys(ch.get("before"), ch.get("after")) != [entry["attribute"]]:
            continue
        if ch["before"].get(entry["attribute"]) == entry["before"] and ch["after"].get(entry["attribute"]) == entry["after"]:
            return entry
    return None


def classify(plan: dict, standing: list) -> Result:
    validate_standing(standing)
    result = Result(status="clean")
    for rc in plan.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        entry = is_standing(rc, standing)
        if entry:
            result.standing.append((rc, entry))
        else:
            result.changes.append(rc)
    for name, oc in sorted((plan.get("output_changes") or {}).items()):
        if oc.get("actions") not in (["no-op"], None):
            result.outputs.append((name, oc))
    result.drift = list(plan.get("resource_drift", []))
    if result.changes or result.outputs:
        result.status = "drift"
    return result


def any_true(marks) -> bool:
    """True when a sensitivity/unknown mark tree has a true leaf anywhere."""
    if marks is True:
        return True
    if isinstance(marks, dict):
        return any(any_true(v) for v in marks.values())
    if isinstance(marks, list):
        return any(any_true(v) for v in marks)
    return False


def show(value, sensitive, unknown=False) -> str:
    if any_true(sensitive):
        # Any sensitivity mark anywhere inside the value redacts the whole value.
        return SENSITIVE
    if any_true(unknown):
        return UNKNOWN
    text = json.dumps(value, sort_keys=True)
    if len(text) > MAX_VALUE:
        text = text[: MAX_VALUE - 1] + "…"
    return text


def mark(marks, key):
    return marks.get(key) if isinstance(marks, dict) else marks


def attribute_lines(ch: dict) -> list[str]:
    before, after = ch.get("before") or {}, ch.get("after") or {}
    unknown = ch.get("after_unknown") or {}
    keys = changed_keys(before, after)
    keys += sorted(k for k, v in unknown.items() if any_true(v) and k not in keys)
    lines = []
    for key in keys:
        b = show(before.get(key), mark(ch.get("before_sensitive"), key)) if key in before else "(none)"
        a = show(after.get(key), mark(ch.get("after_sensitive"), key), mark(unknown, key)) if (key in after or key in unknown) else "(none)"
        lines.append(f"  - `{key}`: `{b}` → `{a}`")
    return lines


def render(result: Result, title: str) -> str:
    out = [f"### {title}: " + ("no changes" if result.status == "clean" else "CHANGES")]
    if result.changes:
        out.append("")
        out.append("| Action | Address |")
        out.append("|---|---|")
        for rc in result.changes:
            out.append(f"| {'/'.join(rc['change']['actions'])} | `{rc['address']}` |")
        out.append("")
        for rc in result.changes:
            out.append(f"- **{'/'.join(rc['change']['actions'])}** `{rc['address']}`")
            out.extend(attribute_lines(rc["change"]))
    if result.outputs:
        out.append("")
        out.append("Outputs: " + ", ".join(f"`{name}` ({'/'.join(oc['actions'])})" for name, oc in result.outputs))
    if result.drift:
        out.append("")
        out.append("Changed outside Terraform (refresh): " + ", ".join(f"`{d['address']}`" for d in result.drift))
    if result.standing:
        out.append("")
        out.append("Tolerated standing provider artifacts (exact match, see linked issue):")
        for rc, entry in result.standing:
            out.append(f"- `{rc['address']}` `{entry['attribute']}` ({entry['issue']})")
    return "\n".join(out) + "\n"


def standing_for(stack: str | None) -> list:
    if not stack:
        return []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return manifest["stacks"][stack].get("standing", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True, type=Path, help="output of terraform show -json")
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--stack", help="apply this stack's standing entries")
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    result = classify(plan, standing_for(args.stack))
    text = render(result, args.title)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result.status == "clean" else 2


if __name__ == "__main__":
    sys.exit(main())
