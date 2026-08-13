#!/usr/bin/env python3
"""Fail closed on actionable candidate-preflight post-apply readback."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGURATION_ROOT = ROOT / "stacks" / "aws-candidate-preflight"
PREAPPLY_ASSERTION = ROOT / "scripts" / "assert-candidate-preflight-plan.py"

TERRAFORM_VERSION = "1.15.8"
PLAN_FORMAT_VERSION = "1.2"
SOURCE_LF_SHA256 = {
    "main.tf": "bb7fee2f20f6fe573eae235e8c043a432c9e05e6df5bde37bf465ddd1ed6f00d",
    "versions.tf": "b7443e50e884d7ed228bb7d29373b18c1b8c1a5a4cb5575e8b5a550a8baa60f6",
    ".terraform.lock.hcl": "4f9da38851b151b7100f9403c30048327f8674136132a957b7061535bc4efe41",
    "handler.py": "69a59299be3c49530ed04bce9a0bfa53a79d63b0b23dcffcfd9f65c9bde217a1",
    "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
}
EXPECTED_RESOURCE_CHANGES = {
    "aws_cloudwatch_log_group.candidate_preflight",
    "aws_iam_role.candidate_preflight",
    "aws_iam_role_policy.candidate_preflight",
    "aws_lambda_function.candidate_preflight",
}
EXPECTED_OUTPUTS = {
    "candidate_preflight_qualified_arn",
    "candidate_preflight_version",
}
EXPECTED_CHECKS = {
    "aws_lambda_function.candidate_preflight",
    "check.default_workspace_only",
    "data.archive_file.candidate_preflight",
    "data.aws_secretsmanager_secret.admin_password",
}
ALLOWED_DRIFT = {
    "aws_iam_role.candidate_preflight",
    "aws_lambda_function.candidate_preflight",
}
POLICY_ADDRESS = "aws_iam_role_policy.candidate_preflight"
POLICY_NAME = "credential-safe-candidate-preflight-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_json_exact(value: str, label: str = "post-apply show") -> dict:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
        document: dict[str, object] = {}
        for key, item in pairs:
            require(key not in document, f"{label} contains duplicate key {key!r}")
            document[key] = item
        return document

    try:
        document = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} is malformed JSON") from exc
    require(isinstance(document, dict), f"{label} is not a JSON object")
    return document


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_preapply_assertion():
    spec = importlib.util.spec_from_file_location("candidate_preapply_assertion", PREAPPLY_ASSERTION)
    require(spec is not None and spec.loader is not None, "pre-apply assertion module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREAPPLY = load_preapply_assertion()


def assert_exact_source(configuration_root: Path) -> None:
    PREAPPLY.assert_production_source(configuration_root)
    source_root = configuration_root.parent / "aws" / "candidate-preflight"
    paths = {
        "main.tf": configuration_root / "main.tf",
        "versions.tf": configuration_root / "versions.tf",
        ".terraform.lock.hcl": configuration_root / ".terraform.lock.hcl",
        "handler.py": source_root / "handler.py",
        "classification.v1.json": source_root / "classification.v1.json",
    }
    require(set(paths) == set(SOURCE_LF_SHA256), "post-apply source allowlist drifted")
    for name, path in paths.items():
        require(path.is_file() and lf_sha256(path) == SOURCE_LF_SHA256[name], f"exact post-apply source drifted: {name}")
    lock = paths[".terraform.lock.hcl"].read_text(encoding="utf-8")
    require(
        'provider "registry.terraform.io/hashicorp/aws" {\n  version     = "6.59.0"' in lock,
        "AWS provider lock is not exactly 6.59.0",
    )
    require(
        'provider "registry.terraform.io/hashicorp/archive" {\n  version     = "2.8.0"' in lock,
        "archive provider lock is not exactly 2.8.0",
    )


def resource_map(plan: dict) -> dict[str, dict]:
    changes = plan.get("resource_changes")
    require(isinstance(changes, list), "post-apply resource changes are missing")
    require(all(isinstance(item, dict) and isinstance(item.get("address"), str) for item in changes), "post-apply resource change schema drifted")
    result = {item["address"]: item for item in changes}
    require(len(result) == len(changes), "post-apply resource changes contain duplicate addresses")
    return result


def assert_configuration(plan: dict) -> None:
    PREAPPLY.assert_configuration(plan)


def assert_checks(plan: dict) -> None:
    checks = plan.get("checks")
    require(isinstance(checks, list), "post-apply safety checks are missing")
    actual: dict[str, str] = {}
    for check in checks:
        address = check.get("address", {}).get("to_display")
        require(isinstance(address, str) and address not in actual, "post-apply check address is invalid or duplicated")
        actual[address] = check.get("status")
    require(set(actual) == EXPECTED_CHECKS and set(actual.values()) == {"pass"}, "post-apply safety checks drifted")


def assert_noop_change(item: dict, address: str) -> None:
    require(item.get("action_reason") in (None, ""), f"{address} has an action reason")
    change = item.get("change")
    require(isinstance(change, dict) and change.get("actions") == ["no-op"], f"{address} is not no-op")
    require(change.get("before") == change.get("after"), f"{address} no-op values differ")
    require(not change.get("after_unknown"), f"{address} retains unknown values")
    require(not change.get("replace_paths"), f"{address} contains replacement paths")
    require(change.get("before_sensitive") == change.get("after_sensitive"), f"{address} sensitivity shape drifted")


def assert_outputs(plan: dict) -> None:
    outputs = plan.get("output_changes")
    require(isinstance(outputs, dict) and set(outputs) == EXPECTED_OUTPUTS, "post-apply output set drifted")
    for name, change in outputs.items():
        require(change.get("actions") == ["no-op"], f"post-apply output {name} is actionable")
        require(change.get("before") == change.get("after"), f"post-apply output {name} differs")
        require(change.get("after_unknown") is False, f"post-apply output {name} is unknown")
        require(change.get("before_sensitive") is False and change.get("after_sensitive") is False, f"post-apply output {name} sensitivity drifted")
    require(outputs["candidate_preflight_version"]["after"] == "3", "post-apply helper version is not immutable version 3")
    require(outputs["candidate_preflight_qualified_arn"]["after"] == "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3", "post-apply helper ARN is not qualified version 3")


def assert_published_v3(resources: dict[str, dict]) -> None:
    function = resources["aws_lambda_function.candidate_preflight"]["change"]["after"]
    require(function.get("version") == "3", "post-apply Lambda state is not helper version 3")
    require(function.get("qualified_arn") == "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3", "post-apply Lambda qualified ARN drifted")
    require(function.get("code_sha256") == "kp5S3LTvL8L2csu0yvP9zKwUv+0RP0Q++lgnTobCYgU=", "post-apply Lambda code hash drifted")
    require(function.get("source_code_hash") == "kp5S3LTvL8L2csu0yvP9zKwUv+0RP0Q++lgnTobCYgU=", "post-apply Lambda source hash drifted")
    require(function.get("source_code_size") == 5715, "post-apply Lambda archive size drifted")
    variables = function.get("environment", [{}])[0].get("variables", {})
    require(variables.get("SOURCE_HANDLER_SHA256") == "589d341be3d489d5a7abbce5dd816254121ae4c5ef327a35555ae0a9efe27140", "post-apply Lambda handler hash pin drifted")


def parse_policy(value: object, label: str) -> dict:
    require(isinstance(value, str), f"{label} policy is not normalized JSON text")
    try:
        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
            document: dict[str, object] = {}
            for key, item in pairs:
                require(key not in document, f"{label} policy contains duplicate key {key!r}")
                document[key] = item
            return document

        document = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} policy is malformed") from exc
    require(isinstance(document, dict), f"{label} policy is not a JSON object")
    return document


def assert_role_drift(item: dict, resources: dict[str, dict]) -> None:
    change = item["change"]
    before, after = change.get("before"), change.get("after")
    require(isinstance(before, dict) and isinstance(after, dict), "IAM role readback values are missing")
    require(before.get("inline_policy") == [], "IAM role readback does not begin with an empty aggregate policy")
    observed = after.get("inline_policy")
    require(isinstance(observed, list) and len(observed) == 1, "IAM role readback policy set is not exact")
    policy = observed[0]
    require(isinstance(policy, dict) and set(policy) == {"name", "policy"}, "IAM role readback policy schema drifted")
    require(policy["name"] == POLICY_NAME, "IAM role readback policy name drifted")

    standalone = resources[POLICY_ADDRESS]["change"]["after"]
    require(isinstance(standalone, dict), "standalone IAM policy state is missing")
    require(standalone.get("name") == POLICY_NAME, "standalone IAM policy name drifted")
    require(
        parse_policy(policy["policy"], "IAM role aggregate") == parse_policy(standalone.get("policy"), "standalone IAM"),
        "IAM role aggregate policy differs semantically from the standalone policy",
    )

    normalized_before = copy.deepcopy(before)
    normalized_before["inline_policy"] = copy.deepcopy(observed)
    require(normalized_before == after, "IAM role readback changes fields beyond the exact aggregate inline policy")
    before_sensitive = change.get("before_sensitive")
    after_sensitive = change.get("after_sensitive")
    require(isinstance(before_sensitive, dict) and isinstance(after_sensitive, dict), "IAM role readback sensitivity shape is missing")
    require(before_sensitive.get("inline_policy") == [], "IAM role readback sensitivity does not begin empty")
    require(after_sensitive.get("inline_policy") == [{}], "IAM role readback sensitivity does not contain exactly one policy")
    normalized_sensitive = copy.deepcopy(before_sensitive)
    normalized_sensitive["inline_policy"] = [{}]
    require(normalized_sensitive == after_sensitive, "IAM role readback changes sensitivity fields beyond the exact policy")


def assert_lambda_drift(item: dict) -> None:
    change = item["change"]
    before, after = change.get("before"), change.get("after")
    require(isinstance(before, dict) and isinstance(after, dict), "Lambda readback values are missing")
    require("layers" in before and before["layers"] is None, "Lambda readback does not begin with null layers")
    require("layers" in after and after["layers"] == [], "Lambda readback does not normalize layers to an empty list")
    normalized_before = copy.deepcopy(before)
    normalized_before["layers"] = []
    require(normalized_before == after, "Lambda readback changes fields beyond exact empty-layer normalization")
    before_sensitive = change.get("before_sensitive")
    after_sensitive = change.get("after_sensitive")
    require(isinstance(before_sensitive, dict) and isinstance(after_sensitive, dict), "Lambda readback sensitivity shape is missing")
    require("layers" not in before_sensitive, "Lambda readback sensitivity unexpectedly begins with layers")
    require(after_sensitive.get("layers") == [], "Lambda readback sensitivity does not normalize layers to empty")
    normalized_sensitive = copy.deepcopy(before_sensitive)
    normalized_sensitive["layers"] = []
    require(normalized_sensitive == after_sensitive, "Lambda readback changes sensitivity fields beyond exact empty layers")


def assert_drift(plan: dict, resources: dict[str, dict]) -> None:
    drift = plan.get("resource_drift", [])
    require(isinstance(drift, list), "post-apply resource drift schema is invalid")
    addresses = [item.get("address") for item in drift]
    require(len(addresses) == len(set(addresses)), "post-apply provider readback contains duplicate resources")
    require(set(addresses).issubset(ALLOWED_DRIFT), "post-apply provider readback contains an unreviewed resource")
    for item in drift:
        address = item["address"]
        require(item.get("action_reason") in (None, ""), f"provider readback {address} has an action reason")
        change = item.get("change")
        require(isinstance(change, dict) and change.get("actions") == ["update"], f"provider readback {address} action shape drifted")
        require(not change.get("after_unknown"), f"provider readback {address} contains unknown values")
        require(not change.get("replace_paths"), f"provider readback {address} contains replacement paths")
        if address == "aws_iam_role.candidate_preflight":
            assert_role_drift(item, resources)
        elif address == "aws_lambda_function.candidate_preflight":
            assert_lambda_drift(item)


def assert_postapply(plan: dict, configuration_root: Path = DEFAULT_CONFIGURATION_ROOT) -> None:
    assert_exact_source(configuration_root)
    require(plan.get("terraform_version") == TERRAFORM_VERSION, "post-apply Terraform version drifted")
    require(plan.get("format_version") == PLAN_FORMAT_VERSION, "post-apply plan format version drifted")
    require(plan.get("applyable") is False, "post-apply readback is applyable")
    require(plan.get("complete") is True, "post-apply readback is incomplete")
    require(plan.get("errored") is False, "post-apply readback is errored")
    for field in ("deferred_changes", "actions", "action_invocations", "action_triggers"):
        require(not plan.get(field), f"post-apply readback contains top-level {field}")

    assert_configuration(plan)
    assert_checks(plan)
    resources = resource_map(plan)
    require(set(resources) == EXPECTED_RESOURCE_CHANGES, "post-apply resource change set drifted")
    for address, item in resources.items():
        assert_noop_change(item, address)
    assert_outputs(plan)
    assert_published_v3(resources)
    assert_drift(plan, resources)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("show_json", type=Path, help="saved post-apply terraform show -json output")
    parser.add_argument("--configuration-root", type=Path, default=DEFAULT_CONFIGURATION_ROOT)
    args = parser.parse_args()
    assert_postapply(parse_json_exact(args.show_json.read_text(encoding="utf-8")), args.configuration_root)
    print("candidate-preflight apply-actionless provider readback: PASS")


if __name__ == "__main__":
    main()
