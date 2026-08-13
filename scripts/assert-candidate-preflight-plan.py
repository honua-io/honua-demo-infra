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
    "data.aws_secretsmanager_secret.admin_password",
}
EXPECTED_TAGS = {
    "Environment": "demo",
    "ManagedBy": "terraform",
    "Project": "honua-server",
    "Purpose": "public-demo",
}
EXPECTED_PROVIDERS = {
    "archive": {
        "name": "archive",
        "full_name": "registry.terraform.io/hashicorp/archive",
        "version_constraint": ">= 2.4.0, < 3.0.0",
    },
    "aws": {
        "name": "aws",
        "full_name": "registry.terraform.io/hashicorp/aws",
        "version_constraint": ">= 5.0.0, < 7.0.0",
        "expressions": {
            "allowed_account_ids": {"constant_value": ["585192672263"]},
            "region": {"constant_value": "us-west-2"},
        },
    },
}
EXPECTED_RESOURCE_EXPRESSIONS = {
    "aws_cloudwatch_log_group.candidate_preflight": {
        "name": {"references": ["local.candidate_preflight_log_group"]},
        "retention_in_days": {"constant_value": 90},
        "tags": {"references": ["local.common_tags"]},
    },
    "aws_iam_role.candidate_preflight": {
        "assume_role_policy": {
            "references": [
                "data.aws_iam_policy_document.candidate_preflight_assume.json",
                "data.aws_iam_policy_document.candidate_preflight_assume",
            ]
        },
        "name": {"references": ["local.candidate_preflight_role_name"]},
        "tags": {"references": ["local.common_tags"]},
    },
    "aws_iam_role_policy.candidate_preflight": {
        "name": {"constant_value": "credential-safe-candidate-preflight-v1"},
        "policy": {
            "references": [
                "local.admin_password_secret_arn",
                "local.candidate_preflight_app_function_arn",
                "local.candidate_preflight_candidate_version",
                "local.candidate_preflight_app_function_arn",
                "local.candidate_preflight_live_alias_name",
                "local.candidate_preflight_app_function_arn",
                "local.candidate_preflight_candidate_version",
                "local.candidate_preflight_log_group_arn",
            ]
        },
        "role": {"references": ["local.candidate_preflight_role_name"]},
    },
    "aws_lambda_function.candidate_preflight": {
        "architectures": {"constant_value": ["arm64"]},
        "environment": [
            {
                "variables": {
                    "references": [
                        "local.admin_password_secret_arn",
                        "local.candidate_preflight_app_function_name",
                        "local.candidate_preflight_artifact_reference",
                        "local.candidate_preflight_candidate_revision_id",
                        "local.candidate_preflight_candidate_version",
                        "local.candidate_preflight_image_digest",
                        "local.candidate_preflight_live_alias_name",
                        "local.candidate_preflight_live_revision_id",
                        "local.candidate_preflight_live_version",
                        "local.candidate_preflight_source_commit",
                        "local.candidate_preflight_classification_sha256",
                        "local.candidate_preflight_handler_sha256",
                    ]
                }
            }
        ],
        "filename": {
            "references": [
                "data.archive_file.candidate_preflight.output_path",
                "data.archive_file.candidate_preflight",
            ]
        },
        "function_name": {"references": ["local.candidate_preflight_function_name"]},
        "handler": {"constant_value": "handler.handler"},
        "memory_size": {"constant_value": 128},
        "publish": {"constant_value": True},
        "reserved_concurrent_executions": {"constant_value": 1},
        "role": {"references": ["local.candidate_preflight_role_arn"]},
        "runtime": {"constant_value": "python3.13"},
        "source_code_hash": {
            "references": [
                "data.archive_file.candidate_preflight.output_base64sha256",
                "data.archive_file.candidate_preflight",
            ]
        },
        "tags": {"references": ["local.common_tags"]},
        "timeout": {"constant_value": 120},
    },
    "data.archive_file.candidate_preflight": {
        "output_path": {"references": ["path.module"]},
        "source": [
            {
                "content": {"references": ["local.candidate_preflight_handler_source"]},
                "filename": {"constant_value": "handler.py"},
            },
            {
                "content": {"references": ["local.candidate_preflight_classification_source"]},
                "filename": {"constant_value": "classification.v1.json"},
            },
        ],
        "type": {"constant_value": "zip"},
    },
    "data.aws_iam_policy_document.candidate_preflight_assume": {
        "statement": [
            {
                "actions": {"constant_value": ["sts:AssumeRole"]},
                "principals": [
                    {
                        "identifiers": {"constant_value": ["lambda.amazonaws.com"]},
                        "type": {"constant_value": "Service"},
                    }
                ],
            }
        ]
    },
    "data.aws_secretsmanager_secret.admin_password": {
        "name": {"constant_value": "honua-demo-demo/admin-password"}
    },
}
EXPECTED_CONFIGURATION_OUTPUTS = {
    "candidate_preflight_qualified_arn": {
        "description": "Immutable published candidate-preflight Lambda ARN; invoke only this qualified ARN.",
        "expression": {
            "references": [
                "aws_lambda_function.candidate_preflight.qualified_arn",
                "aws_lambda_function.candidate_preflight",
            ]
        },
    },
    "candidate_preflight_version": {
        "description": "Immutable published candidate-preflight Lambda version.",
        "expression": {
            "references": [
                "aws_lambda_function.candidate_preflight.version",
                "aws_lambda_function.candidate_preflight",
            ]
        },
    },
}
EXPECTED_RESOURCE_METADATA = {
    "aws_cloudwatch_log_group.candidate_preflight": {
        "mode": "managed",
        "type": "aws_cloudwatch_log_group",
        "name": "candidate_preflight",
        "provider_config_key": "aws",
        "schema_version": 0,
    },
    "aws_iam_role.candidate_preflight": {
        "mode": "managed",
        "type": "aws_iam_role",
        "name": "candidate_preflight",
        "provider_config_key": "aws",
        "schema_version": 0,
    },
    "aws_iam_role_policy.candidate_preflight": {
        "mode": "managed",
        "type": "aws_iam_role_policy",
        "name": "candidate_preflight",
        "provider_config_key": "aws",
        "schema_version": 0,
        "depends_on": ["aws_iam_role.candidate_preflight"],
    },
    "aws_lambda_function.candidate_preflight": {
        "mode": "managed",
        "type": "aws_lambda_function",
        "name": "candidate_preflight",
        "provider_config_key": "aws",
        "schema_version": 0,
        "depends_on": [
            "aws_cloudwatch_log_group.candidate_preflight",
            "aws_iam_role_policy.candidate_preflight",
        ],
    },
    "data.archive_file.candidate_preflight": {
        "mode": "data",
        "type": "archive_file",
        "name": "candidate_preflight",
        "provider_config_key": "archive",
        "schema_version": 0,
    },
    "data.aws_iam_policy_document.candidate_preflight_assume": {
        "mode": "data",
        "type": "aws_iam_policy_document",
        "name": "candidate_preflight_assume",
        "provider_config_key": "aws",
        "schema_version": 0,
    },
    "data.aws_secretsmanager_secret.admin_password": {
        "mode": "data",
        "type": "aws_secretsmanager_secret",
        "name": "admin_password",
        "provider_config_key": "aws",
        "schema_version": 0,
    },
}
PROVIDER_KEYS = {
    "aws_cloudwatch_log_group.candidate_preflight": "aws",
    "aws_iam_role.candidate_preflight": "aws",
    "aws_iam_role_policy.candidate_preflight": "aws",
    "aws_lambda_function.candidate_preflight": "aws",
    "data.archive_file.candidate_preflight": "archive",
    "data.aws_iam_policy_document.candidate_preflight_assume": "aws",
    "data.aws_secretsmanager_secret.admin_password": "aws",
}
EXPECTED_CHECKS = {
    "aws_lambda_function.candidate_preflight",
    "check.default_workspace_only",
    "data.archive_file.candidate_preflight",
    "data.aws_secretsmanager_secret.admin_password",
}
ACCOUNT = "585192672263"
REGION = "us-west-2"
FUNCTION_NAME = "honua-demo-demo-honua"
FUNCTION_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION_NAME}"
HELPER_NAME = "honua-demo-demo-candidate-preflight"
LOG_GROUP = f"/aws/lambda/{HELPER_NAME}"
LOG_GROUP_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}"
ROLE_NAME = f"{HELPER_NAME}-role"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
SECRET_PATTERN = re.compile(
    rf"^arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{{6}}$"
)
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
ARTIFACT = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}"
HANDLER_SHA256 = "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb"
CLASSIFICATION_SHA256 = "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579"
ARCHIVE_BASE64SHA256 = "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA="


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
    for forbidden in (
        "skip_credentials_validation",
        "skip_requesting_account_id",
        "var.",
        "ignore_changes",
        "terraform_remote_state",
        "aws_secretsmanager_secret_version",
        "secret_string",
        "source_dir",
    ):
        require(forbidden not in versions + main, f"production escape hatch present: {forbidden}")
    require('data "aws_secretsmanager_secret" "admin_password"' in main, "metadata-only secret lookup missing")
    require('name = "honua-demo-demo/admin-password"' in main, "exact secret name drifted")
    require('self.tags == tomap(local.common_tags)' in main, "exact type-normalized secret tag guard missing")
    require("terraform.workspace == \"default\"" in main, "default-workspace precondition missing")
    require("module.honua" not in main, "isolated root contains a module.honua dependency")
    require(not re.search(r"admin-password-[A-Za-z0-9]{6}(?:\"|$)", main), "secret ARN suffix was hard-coded")
    require(
        re.search(
            r"admin_password_secret_arn\s*=\s*data\.aws_secretsmanager_secret\.admin_password\.arn",
            main,
        )
        is not None,
        "secret ARN provenance is disconnected from metadata lookup",
    )
    require(main.count("source {") == 2, "archive source allowlist is not exactly two files")
    require('filename = "handler.py"' in main, "handler.py archive member missing")
    require('filename = "classification.v1.json"' in main, "classification archive member missing")
    require(re.search(rf'candidate_preflight_handler_sha256\s*=\s*"{HANDLER_SHA256}"', main) is not None, "handler source hash drifted")
    require(re.search(rf'candidate_preflight_classification_sha256\s*=\s*"{CLASSIFICATION_SHA256}"', main) is not None, "classification source hash drifted")
    require(re.search(rf'candidate_preflight_archive_base64sha256\s*=\s*"{re.escape(ARCHIVE_BASE64SHA256)}"', main) is not None, "archive hash drifted")


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


def assert_configuration(
    plan: dict,
    secret_data_address: str = "data.aws_secretsmanager_secret.admin_password",
    provider_contract: dict | None = None,
) -> dict[str, dict]:
    full_configuration = plan.get("configuration")
    require(isinstance(full_configuration, dict), "candidate-preflight configuration is missing")
    require(set(full_configuration) == {"provider_config", "root_module"}, "configuration top-level schema drifted")
    configuration = full_configuration.get("root_module")
    require(isinstance(configuration, dict), "candidate-preflight root configuration is missing")
    require(set(configuration) == {"outputs", "resources"}, "configuration root schema drifted")
    require(configuration.get("outputs") == EXPECTED_CONFIGURATION_OUTPUTS, "configuration output contract drifted")
    provider_config = full_configuration.get("provider_config")
    require(provider_config == (provider_contract or EXPECTED_PROVIDERS), "provider configuration set or values drifted")
    resources = configuration.get("resources", [])
    require(all(isinstance(resource, dict) and isinstance(resource.get("address"), str) for resource in resources), "configuration resource schema drifted")
    config_resources = {resource["address"]: resource for resource in resources}
    require(len(config_resources) == len(resources), "configuration graph contains duplicate addresses")
    expected_data = (EXPECTED_DATA - {"data.aws_secretsmanager_secret.admin_password"}) | {secret_data_address}
    require(set(config_resources) == EXPECTED_MANAGED | expected_data, "configuration graph is not exactly four resources and three data sources")
    for address in EXPECTED_MANAGED:
        require(config_resources[address].get("mode") == "managed", f"{address} must be managed")
    for address in expected_data:
        require(config_resources[address].get("mode") == "data", f"{address} must be data")
    expected_expressions = dict(EXPECTED_RESOURCE_EXPRESSIONS)
    expected_metadata = dict(EXPECTED_RESOURCE_METADATA)
    if secret_data_address != "data.aws_secretsmanager_secret.admin_password":
        expected_expressions.pop("data.aws_secretsmanager_secret.admin_password")
        expected_expressions[secret_data_address] = {
            "output_path": {"references": ["path.module"]},
            "source_file": {"references": ["path.module"]},
            "type": {"constant_value": "zip"},
        }
        expected_metadata.pop("data.aws_secretsmanager_secret.admin_password")
        expected_metadata[secret_data_address] = {
            "mode": "data",
            "type": "archive_file",
            "name": "admin_password",
            "provider_config_key": "archive",
            "schema_version": 0,
        }
    require(set(expected_expressions) == set(config_resources), "configuration expression contract address set drifted")
    for address, expected in expected_expressions.items():
        expected_resource = {
            "address": address,
            **expected_metadata[address],
            "expressions": expected,
        }
        require(config_resources[address] == expected_resource, f"{address} exact configuration object drifted")
    return config_resources


def assert_plan(
    plan: dict,
    configuration_root: Path = DEFAULT_CONFIGURATION_ROOT,
    secret_data_address: str = "data.aws_secretsmanager_secret.admin_password",
    provider_contract: dict | None = None,
    check_contract: set[str] | None = None,
) -> None:
    assert_production_source(configuration_root)
    require(plan.get("errored") is not True, "candidate-preflight plan is marked errored")
    require(plan.get("complete") is True, "candidate-preflight plan is incomplete or deferred")
    require(plan.get("applyable") is True, "candidate-preflight plan is not applyable")
    format_version = str(plan.get("format_version", ""))
    require(format_version.split(".", 1)[0] == "1", f"unsupported plan format version {format_version!r}")
    require(not plan.get("resource_drift"), "candidate-preflight plan contains resource drift")
    require(not plan.get("deferred_changes"), "candidate-preflight plan contains deferred changes")
    for field in ("actions", "action_invocations", "action_triggers"):
        require(not plan.get(field), f"candidate-preflight plan contains top-level {field}")
    checks = plan.get("checks", [])
    require(checks, "candidate-preflight plan has no evaluated safety checks")
    actual_check_addresses = {check.get("address", {}).get("to_display") for check in checks}
    require(actual_check_addresses == (check_contract or EXPECTED_CHECKS), f"safety check address set drifted: {sorted(actual_check_addresses)}")
    for check in checks:
        require(check.get("status") == "pass", f"safety check did not pass: {check.get('address')}")
        for instance in check.get("instances", []):
            require(instance.get("status") == "pass", f"safety check instance did not pass: {instance.get('address')}")

    config_resources = assert_configuration(plan, secret_data_address, provider_contract)

    changes = {change["address"]: change for change in plan.get("resource_changes", [])}
    require(set(changes) == EXPECTED_MANAGED, "resource-change set is not exactly the four helper resources")
    for address, change in changes.items():
        require(change["change"]["actions"] == ["create"], f"{address} must be create-only")
        require(not change.get("action_reason"), f"{address} has an action reason")

    outputs = plan.get("output_changes", {})
    expected_outputs = {"candidate_preflight_qualified_arn", "candidate_preflight_version"}
    require(set(outputs) == expected_outputs, "helper output set drifted")
    for name, output in outputs.items():
        require(output.get("actions") == ["create"], f"{name} must be create-only")
        require(output.get("after") is None, f"{name} must be unknown before apply")
        require(output.get("after_unknown") is True, f"{name} must be resolved only after publication")
        require(output.get("after_sensitive") is False, f"{name} must be non-sensitive")

    planned_outputs = plan.get("planned_values", {}).get("outputs", {})
    require(set(planned_outputs) == expected_outputs, "planned output set contains an unexpected or unqualified output")
    for name, output in planned_outputs.items():
        require("value" not in output, f"{name} unexpectedly has an unqualified or pre-publication value")
        require(output.get("sensitive") is False, f"{name} is sensitive")

    def find_forbidden_secret_fields(value) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).lower() in {"secret_string", "secret_binary"}
                or find_forbidden_secret_fields(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(find_forbidden_secret_fields(child) for child in value)
        return False

    require(not find_forbidden_secret_fields(plan), "plan contains a secret value field")
    planned_resources = {
        resource["address"]: resource
        for resource in plan.get("planned_values", {}).get("root_module", {}).get("resources", [])
    }
    require(set(planned_resources) == EXPECTED_MANAGED, f"planned managed state graph is not exact: {sorted(planned_resources)}")
    archive_config = config_resources["data.archive_file.candidate_preflight"]
    require(expression_constant(archive_config, "type") == "zip", "archive type drifted")
    require(
        archive_config.get("expressions", {}).get("output_path", {}).get("references") == ["path.module"],
        "archive output path provenance drifted",
    )

    after = {address: change["change"]["after"] for address, change in changes.items()}
    log_group = after["aws_cloudwatch_log_group.candidate_preflight"]
    require(log_group["name"] == LOG_GROUP and log_group["retention_in_days"] == 90, "log-group contract drifted")
    require(log_group["tags"] == EXPECTED_TAGS, "log-group tags drifted")

    role = after["aws_iam_role.candidate_preflight"]
    require(role["name"] == ROLE_NAME and not role.get("name_prefix"), "role identity drifted")
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
        "SOURCE_CLASSIFICATION_SHA256": CLASSIFICATION_SHA256,
        "SOURCE_HANDLER_SHA256": HANDLER_SHA256,
    }
    require(environment == expected_environment, "helper environment contract drifted")
    require(function["function_name"] == HELPER_NAME, "helper function name drifted")
    require(function["architectures"] == ["arm64"], "helper architecture drifted")
    require(function["runtime"] == "python3.13" and function["handler"] == "handler.handler", "helper runtime contract drifted")
    require(function["timeout"] == 120 and function["memory_size"] == 128, "helper resource bounds drifted")
    require(function["reserved_concurrent_executions"] == 1, "helper concurrency bound drifted")
    require(function["publish"] is True, "helper version publication drifted")
    require(function["tags"] == EXPECTED_TAGS, "helper tags drifted")
    require(function["role"] == ROLE_ARN, "helper execution role ARN drifted")
    require(function["filename"] == "./candidate-preflight.zip", "helper archive filename drifted")
    require(function["source_code_hash"] == ARCHIVE_BASE64SHA256, f"helper archive content hash drifted: {function['source_code_hash']}")
    require(not function.get("layers"), "helper unexpectedly has Lambda layers")
    require(not function.get("vpc_config"), "helper unexpectedly has VPC configuration")
    require(not function.get("file_system_config"), "helper unexpectedly has filesystem configuration")
    require(not function.get("dead_letter_config"), "helper unexpectedly has dead-letter configuration")
    require(not function.get("image_config"), "helper unexpectedly has image configuration")

    role_policy = after["aws_iam_role_policy.candidate_preflight"]
    require(role_policy["name"] == "credential-safe-candidate-preflight-v1", "inline policy name drifted")
    require(role_policy["role"] == ROLE_NAME, "inline policy is not bound to the exact reviewed role")
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
