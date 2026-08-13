#!/usr/bin/env python3
"""Validate the one-time state-only primary output materialization plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_OUTPUTS = ROOT / "stacks" / "aws" / "outputs.tf"
OUTPUT_NAME = "admin_password_secret_arn"
SECRET_PATTERN = re.compile(
    r"^arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{6}$"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def assert_plan(plan: dict) -> None:
    source = PRIMARY_OUTPUTS.read_text(encoding="utf-8")
    require(
        re.search(
            r'output\s+"admin_password_secret_arn"\s*\{[^}]*value\s*=\s*module\.honua\.admin_password_secret_arn',
            source,
            re.DOTALL,
        )
        is not None,
        "primary output is not the authoritative honua-iac module output",
    )
    require(plan.get("errored") is not True, "primary plan is marked errored")
    require(plan.get("complete") is True, "primary plan is incomplete")
    require(not plan.get("resource_drift"), "primary plan contains resource drift")
    require(not plan.get("deferred_changes"), "primary plan contains deferred changes")

    for change in plan.get("resource_changes", []):
        require(change["change"]["actions"] == ["no-op"], f"resource action present at {change['address']}")
        require(not change.get("action_reason"), f"resource action reason present at {change['address']}")

    output_changes = plan.get("output_changes", {})
    changed = {name: change for name, change in output_changes.items() if change.get("actions") != ["no-op"]}
    require(set(changed) == {OUTPUT_NAME}, "primary plan must change only admin_password_secret_arn")
    output = changed[OUTPUT_NAME]
    require(output.get("actions") == ["create"], "admin_password_secret_arn must be create-only")
    require(output.get("before") is None, "admin_password_secret_arn unexpectedly existed before")
    require(output.get("after_unknown") is False, "admin_password_secret_arn must be known")
    require(output.get("after_sensitive") is False, "admin_password_secret_arn must be non-sensitive")
    require(isinstance(output.get("after"), str), "admin_password_secret_arn must be a string")
    require(bool(SECRET_PATTERN.fullmatch(output["after"])), "admin_password_secret_arn identity is not exact")

    planned = plan.get("planned_values", {}).get("outputs", {}).get(OUTPUT_NAME, {})
    require(planned.get("value") == output["after"], "planned output value disagrees with output change")
    require(planned.get("sensitive") is False, "planned admin_password_secret_arn is sensitive")

    configuration_output = plan.get("configuration", {}).get("root_module", {}).get("outputs", {}).get(OUTPUT_NAME, {})
    references = configuration_output.get("expression", {}).get("references", [])
    require(set(references) == {"module.honua.admin_password_secret_arn", "module.honua"}, "planned output provenance drifted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("show_json", type=Path)
    args = parser.parse_args()
    assert_plan(json.loads(args.show_json.read_text(encoding="utf-8")))
    print("primary output materialization plan: PASS")


if __name__ == "__main__":
    main()
