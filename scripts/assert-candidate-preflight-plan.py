#!/usr/bin/env python3
"""Fail closed unless a saved candidate-preflight plan and source are exact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGURATION_ROOT = ROOT / "stacks" / "aws-candidate-preflight"

EXPECTED_MANAGED = {
    "aws_cloudwatch_log_group.candidate_preflight",
    "aws_iam_role.candidate_preflight",
    "aws_iam_role_policy.candidate_preflight",
    "aws_lambda_function.candidate_preflight",
}
EXPECTED_DATA = {
    "data.archive_file.candidate_preflight",
    "data.aws_iam_policy_document.candidate_preflight_assume",
    "data.terraform_remote_state.primary",
}
EXPECTED_TAGS = {
    "Environment": "demo",
    "ManagedBy": "terraform",
    "Project": "honua-server",
    "Purpose": "public-demo",
}
ACCOUNT = "585192672263"
REGION = "us-west-2"
FUNCTION_NAME = "honua-demo-demo-honua"
FUNCTION_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION_NAME}"
HELPER_NAME = "honua-demo-demo-candidate-preflight"
LOG_GROUP = f"/aws/lambda/{HELPER_NAME}"
LOG_GROUP_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}"
SECRET_PATTERN = re.compile(
    rf"^arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{{6}}$"
)
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
ARTIFACT = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def expression_constant(resource: dict, name: str):
    expression = resource.get("expressions", {}).get(name, {})
    require("constant_value" in expression, f"{resource['address']}.{name} must be constant")
    return expression["constant_value"]


def assert_production_source(configuration_root: Path = DEFAULT_CONFIGURATION_ROOT) -> None:
    versions = (configuration_root / "versions.tf").read_text(encoding="utf-8")
    main = (configuration_root / "main.tf").read_text(encoding="utf-8")
    require('key          = "demo/aws-demo/candidate-preflight.tfstate"' in versions, "helper state key drifted")
    require('region       = "us-east-1"' in versions, "helper backend region drifted")
    require('bucket       = "honua-tfstate-585192672263"' in versions, "helper backend bucket drifted")
    require("encrypt      = true" in versions, "helper backend encryption drifted")
    require("use_lockfile = true" in versions, "helper backend locking drifted")
    require('region              = "us-west-2"' in versions, "provider region drifted")
    require('allowed_account_ids = ["585192672263"]' in versions, "provider account guard drifted")
    for forbidden in ("skip_credentials_validation", "skip_requesting_account_id", "var.", "ignore_changes"):
        require(forbidden not in versions + main, f"production escape hatch present: {forbidden}")
    require('backend   = "s3"' in main, "primary handoff backend drifted")
    require('workspace = "default"' in main, "primary handoff workspace drifted")
    require('bucket       = "honua-tfstate-585192672263"' in main, "primary handoff bucket drifted")
    require('key          = "demo/aws-demo/terraform.tfstate"' in main, "primary handoff key drifted")
    require('region       = "us-east-1"' in main, "primary handoff backend region drifted")
    require("terraform.workspace == \"default\"" in main, "default-workspace precondition missing")
    require("module.honua" not in main, "isolated root contains a module.honua dependency")
    require(not re.search(r"admin-password-[A-Za-z0-9]{6}(?:\"|$)", main), "secret ARN suffix was hard-coded")


def assert_policy(policy_text: str, secret_arn: str) -> None:
    policy = json.loads(policy_text)
    require(set(policy) == {"Version", "Statement"}, "IAM policy top-level fields drifted")
    require(policy["Version"] == "2012-10-17", "IAM policy version drifted")
    expected = [
        {
            "Sid": "ReadExactAdminPassword",
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": [secret_arn],
        },
        {
            "Sid": "ReadExactCandidate",
            "Effect": "Allow",
            "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration"],
            "Resource": [f"{FUNCTION_ARN}:40"],
        },
        {
            "Sid": "ReadExactLiveAlias",
            "Effect": "Allow",
            "Action": ["lambda:GetAlias"],
            "Resource": [f"{FUNCTION_ARN}:live"],
        },
        {
            "Sid": "InvokeExactCandidate",
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": [f"{FUNCTION_ARN}:40"],
        },
        {
            "Sid": "WriteExactLogGroup",
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": [f"{LOG_GROUP_ARN}:*"],
        },
    ]
    require(policy["Statement"] == expected, "IAM policy is not the exact least-privilege document")


def assert_plan(
    plan: dict,
    configuration_root: Path = DEFAULT_CONFIGURATION_ROOT,
    remote_state_contract: tuple[str, str, dict] | None = None,
) -> None:
    assert_production_source(configuration_root)
    require(plan.get("errored") is not True, "candidate-preflight plan is marked errored")
    require(plan.get("complete") is True, "candidate-preflight plan is incomplete or deferred")
    require(not plan.get("resource_drift"), "candidate-preflight plan contains resource drift")
    require(not plan.get("deferred_changes"), "candidate-preflight plan contains deferred changes")

    configuration = plan.get("configuration", {}).get("root_module", {})
    require(not configuration.get("module_calls"), "candidate-preflight configuration has child modules")
    config_resources = {resource["address"]: resource for resource in configuration.get("resources", [])}
    require(set(config_resources) == EXPECTED_MANAGED | EXPECTED_DATA, "configuration graph is not exactly four resources and three data sources")
    for address in EXPECTED_MANAGED:
        require(config_resources[address].get("mode") == "managed", f"{address} must be managed")
    for address in EXPECTED_DATA:
        require(config_resources[address].get("mode") == "data", f"{address} must be data")

    remote = config_resources["data.terraform_remote_state.primary"]
    expected_backend, expected_workspace, expected_config = remote_state_contract or (
        "s3",
        "default",
        {
            "bucket": "honua-tfstate-585192672263",
            "encrypt": True,
            "key": "demo/aws-demo/terraform.tfstate",
            "region": "us-east-1",
            "use_lockfile": True,
        },
    )
    require(expression_constant(remote, "backend") == expected_backend, "primary remote-state backend drifted")
    require(expression_constant(remote, "workspace") == expected_workspace, "primary remote-state workspace drifted")
    require(
        expression_constant(remote, "config") == expected_config,
        "primary remote-state configuration drifted",
    )

    changes = {change["address"]: change for change in plan.get("resource_changes", [])}
    require(set(changes) == EXPECTED_MANAGED, "resource-change set is not exactly the four helper resources")
    for address, change in changes.items():
        require(change["change"]["actions"] == ["create"], f"{address} must be create-only")
        require(not change.get("action_reason"), f"{address} has an action reason")

    outputs = plan.get("output_changes", {})
    require(set(outputs) == {"candidate_preflight_function_name"}, "helper output set drifted")
    output = outputs["candidate_preflight_function_name"]
    require(output.get("actions") == ["create"], "helper output must be create-only")
    require(output.get("after") == HELPER_NAME, "helper output value drifted")
    require(output.get("after_unknown") is False, "helper output must be known")
    require(output.get("after_sensitive") is False, "helper output must be non-sensitive")

    after = {address: change["change"]["after"] for address, change in changes.items()}
    log_group = after["aws_cloudwatch_log_group.candidate_preflight"]
    require(log_group["name"] == LOG_GROUP and log_group["retention_in_days"] == 90, "log-group contract drifted")
    require(log_group["tags"] == EXPECTED_TAGS, "log-group tags drifted")

    role = after["aws_iam_role.candidate_preflight"]
    require(role["name_prefix"] == f"{HELPER_NAME}-", "role name prefix drifted")
    require(role["tags"] == EXPECTED_TAGS, "role tags drifted")
    assume = json.loads(role["assume_role_policy"])
    require(
        assume == {"Statement": [{"Action": "sts:AssumeRole", "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}}], "Version": "2012-10-17"},
        "role assume policy drifted",
    )

    function = after["aws_lambda_function.candidate_preflight"]
    environment = function["environment"][0]["variables"]
    secret_arn = environment["ADMIN_PASSWORD_SECRET_ARN"]
    require(bool(SECRET_PATTERN.fullmatch(secret_arn)), "helper secret ARN is not the exact authoritative identity")
    expected_environment = {
        "ADMIN_PASSWORD_SECRET_ARN": secret_arn,
        "EXPECTED_APP_FUNCTION_NAME": FUNCTION_NAME,
        "EXPECTED_ARCHITECTURE": "arm64",
        "EXPECTED_ARTIFACT_REFERENCE": ARTIFACT,
        "EXPECTED_CANDIDATE_REVISION_ID": "0326e209-4231-4acd-9bb4-d3cb89402db0",
        "EXPECTED_CANDIDATE_VERSION": "40",
        "EXPECTED_IMAGE_DIGEST": IMAGE_DIGEST,
        "EXPECTED_LIVE_ALIAS_NAME": "live",
        "EXPECTED_LIVE_REVISION_ID": "4f73dd76-0294-44d3-8362-c6f8606f034e",
        "EXPECTED_LIVE_VERSION": "39",
        "EXPECTED_PACKAGE_TYPE": "Image",
        "EXPECTED_SKIP_MIGRATIONS": "true",
        "EXPECTED_SOURCE_COMMIT": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
    }
    require(environment == expected_environment, "helper environment contract drifted")
    require(function["function_name"] == HELPER_NAME, "helper function name drifted")
    require(function["architectures"] == ["arm64"], "helper architecture drifted")
    require(function["runtime"] == "python3.13" and function["handler"] == "handler.handler", "helper runtime contract drifted")
    require(function["timeout"] == 120 and function["memory_size"] == 128, "helper resource bounds drifted")
    require(function["reserved_concurrent_executions"] == 1, "helper concurrency bound drifted")
    require(function["tags"] == EXPECTED_TAGS, "helper tags drifted")
    require(not function.get("vpc_config"), "helper unexpectedly has VPC configuration")

    role_policy = after["aws_iam_role_policy.candidate_preflight"]
    require(role_policy["name"] == "credential-safe-candidate-preflight-v1", "inline policy name drifted")
    assert_policy(role_policy["policy"], secret_arn)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("show_json", type=Path, help="local terraform show -json output")
    parser.add_argument("--configuration-root", type=Path, default=DEFAULT_CONFIGURATION_ROOT)
    args = parser.parse_args()
    assert_plan(json.loads(args.show_json.read_text(encoding="utf-8")), args.configuration_root)
    print("candidate-preflight plan scope: PASS")


if __name__ == "__main__":
    main()
