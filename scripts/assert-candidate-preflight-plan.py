#!/usr/bin/env python3
"""Fail closed unless a saved candidate-preflight plan has the exact scope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_CREATES = {
    "aws_cloudwatch_log_group.candidate_preflight",
    "aws_iam_role.candidate_preflight",
    "aws_iam_role_policy.candidate_preflight",
    "aws_lambda_function.candidate_preflight",
}
ALLOWED_READS = {
    "data.archive_file.candidate_preflight",
    "data.aws_iam_policy_document.candidate_preflight_assume",
    "data.terraform_remote_state.primary",
}
EXPECTED_OUTPUTS = {"candidate_preflight_function_name"}


def assert_plan(plan: dict) -> None:
    if plan.get("errored") is True:
        raise RuntimeError("candidate-preflight plan is marked errored")
    if plan.get("complete") is not True:
        raise RuntimeError("candidate-preflight plan is incomplete or deferred")

    creates: set[str] = set()
    reads: set[str] = set()
    for change in plan.get("resource_changes", []):
        address = change["address"]
        actions = change["change"]["actions"]
        if actions == ["no-op"]:
            continue
        if actions == ["create"] and address in EXPECTED_CREATES:
            creates.add(address)
            continue
        if actions == ["read"] and address in ALLOWED_READS:
            reads.add(address)
            continue
        raise RuntimeError(f"candidate-preflight plan contains prohibited action {actions} at {address}")

    if creates != EXPECTED_CREATES:
        missing = sorted(EXPECTED_CREATES - creates)
        extra = sorted(creates - EXPECTED_CREATES)
        raise RuntimeError(f"candidate-preflight create set mismatch; missing={missing}, extra={extra}")

    changed_outputs = {
        name
        for name, change in plan.get("output_changes", {}).items()
        if change.get("actions") != ["no-op"]
    }
    if changed_outputs != EXPECTED_OUTPUTS:
        raise RuntimeError(
            f"candidate-preflight output set mismatch: {sorted(changed_outputs)}"
        )

    configuration = plan.get("configuration", {}).get("root_module", {})
    if configuration.get("module_calls"):
        raise RuntimeError("candidate-preflight root must not contain module calls")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("show_json", type=Path, help="local terraform show -json output")
    args = parser.parse_args()
    assert_plan(json.loads(args.show_json.read_text(encoding="utf-8")))
    print("candidate-preflight plan scope: PASS")


if __name__ == "__main__":
    main()
