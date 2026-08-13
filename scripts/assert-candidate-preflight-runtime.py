#!/usr/bin/env python3
"""Audit a published helper version and bind it to a deployment receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ACCOUNT = "585192672263"
REGION = "us-west-2"
NAME = "honua-demo-demo-candidate-preflight"
ROLE_NAME = f"{NAME}-role"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
POLICY_NAME = "credential-safe-candidate-preflight-v1"
CODE_SHA256 = "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA="
SECRET_PATTERN = re.compile(
    rf"^arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{{6}}$"
)
REVISION_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}$")
VERSION_PATTERN = re.compile(r"^[1-9][0-9]*$")
HANDLER_SHA256 = "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb"
CLASSIFICATION_SHA256 = "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579"
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
PLAN_RECEIPT_SCHEMA = "honua-candidate-preflight-plan-receipt-v1"
DEPLOYMENT_RECEIPT_SCHEMA = "honua-candidate-preflight-deployment-receipt-v1"
ECR_EVIDENCE_SCHEMA = "honua-candidate-preflight-ecr-evidence-v1"
TERRAFORM_VERSION = "1.15.8"
PLAN_FORMAT_VERSION = "1.2"
ARCHIVE_SHA256 = "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0"
SOURCE_HASHES = {
    "handler.py": HANDLER_SHA256,
    "classification.v1.json": CLASSIFICATION_SHA256,
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MERGED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_plan_receipt(receipt: dict, merged_sha: str) -> None:
    require(isinstance(receipt, dict), "plan receipt must be a JSON object")
    require(
        set(receipt) == {"schema", "mergedSha", "terraformVersion", "planFormatVersion", "artifacts", "sourceSha256"},
        "plan receipt keyset drifted",
    )
    require(receipt["schema"] == PLAN_RECEIPT_SCHEMA, "plan receipt schema drifted")
    require(MERGED_SHA_PATTERN.fullmatch(merged_sha) is not None, "merged SHA is not an exact Git commit")
    require(receipt["mergedSha"] == merged_sha, "plan receipt merged SHA drifted")
    require(receipt["terraformVersion"] == TERRAFORM_VERSION, "plan receipt Terraform version drifted")
    require(receipt["planFormatVersion"] == PLAN_FORMAT_VERSION, "plan receipt format version drifted")

    artifacts = receipt["artifacts"]
    require(isinstance(artifacts, dict), "plan receipt artifacts must be an object")
    require(
        set(artifacts) == {"savedPlanSha256", "showJsonSha256", "archiveSha256"},
        "plan receipt artifact keyset drifted",
    )
    require(
        all(isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None for value in artifacts.values()),
        "plan receipt contains an invalid artifact SHA-256",
    )
    require(artifacts["archiveSha256"] == ARCHIVE_SHA256, "plan receipt ZIP hash drifted")
    require(receipt["sourceSha256"] == SOURCE_HASHES, "plan receipt source hash set drifted")


def validate_deployment_receipt(receipt: dict, merged_sha: str) -> None:
    require(isinstance(receipt, dict), "deployment receipt must be a JSON object")
    require(
        set(receipt)
        == {"schema", "mergedSha", "planReceiptSha256", "ecrEvidenceSha256", "qualifiedArn", "version", "revisionId", "codeSha256", "roleArn", "policyName", "secretArn"},
        "deployment receipt keyset drifted",
    )
    require(receipt["schema"] == DEPLOYMENT_RECEIPT_SCHEMA, "deployment receipt schema drifted")
    require(receipt["mergedSha"] == merged_sha, "deployment receipt merged SHA drifted")
    require(SHA256_PATTERN.fullmatch(receipt["planReceiptSha256"]) is not None, "deployment receipt plan hash is invalid")
    require(SHA256_PATTERN.fullmatch(receipt["ecrEvidenceSha256"]) is not None, "deployment receipt ECR hash is invalid")
    require(VERSION_PATTERN.fullmatch(receipt["version"]) is not None, "deployment receipt helper version is invalid")
    require(
        receipt["qualifiedArn"] == f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{NAME}:{receipt['version']}",
        "deployment receipt qualified ARN drifted",
    )
    require(REVISION_PATTERN.fullmatch(receipt["revisionId"]) is not None, "deployment receipt revision id is invalid")
    require(receipt["codeSha256"] == CODE_SHA256, "deployment receipt code hash drifted")
    require(receipt["roleArn"] == ROLE_ARN, "deployment receipt role ARN drifted")
    require(receipt["policyName"] == POLICY_NAME, "deployment receipt policy name drifted")
    require(SECRET_PATTERN.fullmatch(receipt["secretArn"]) is not None, "deployment receipt secret ARN drifted")


def expected_policy(secret_arn: str) -> dict:
    app = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-honua"
    log = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{NAME}:*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ReadExactAdminPassword", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": [secret_arn]},
            {"Sid": "ReadExactCandidate", "Effect": "Allow", "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration"], "Resource": [f"{app}:40"]},
            {"Sid": "ReadExactLiveAlias", "Effect": "Allow", "Action": ["lambda:GetAlias"], "Resource": [f"{app}:live"]},
            {"Sid": "InvokeExactCandidate", "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": [f"{app}:40"]},
            {"Sid": "WriteExactLogGroup", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [log]},
        ],
    }


def validate_ecr_evidence(evidence: dict) -> None:
    require(
        evidence
        == {
            "schema": ECR_EVIDENCE_SCHEMA,
            "registryId": ACCOUNT,
            "region": REGION,
            "repositoryName": "honua-server",
            "imageDigest": IMAGE_DIGEST,
            "manifestMediaType": "application/vnd.oci.image.manifest.v1+json",
            "manifestSha256": IMAGE_DIGEST,
            "config": {
                "digest": "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f",
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": 6052,
                "sha256": "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f",
                "architecture": "arm64",
                "os": "linux",
                "entrypoint": ["/var/task/Honua.Server"],
                "cmd": None,
                "workingDir": "/var/task",
                "nativeAot": "native-aot",
                "runtimeEntrypoint": "/var/task/Honua.Server",
                "ociRevision": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
                "source": "https://github.com/honua-io/honua-server",
                "honuaGitSha": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
            },
        },
        "ECR image provenance evidence drifted",
    )


def audit(args) -> dict:
    function = load(args.function)["Configuration"]
    concurrency = load(args.concurrency)
    role = load(args.role)["Role"]
    role_policy = load(args.role_policy)
    attached = load(args.attached_policies)
    inline = load(args.inline_policies)
    plan_receipt = load(args.plan_receipt)
    ecr_evidence = load(args.ecr_evidence)
    validate_plan_receipt(plan_receipt, args.merged_sha)
    validate_ecr_evidence(ecr_evidence)

    version = str(function.get("Version", ""))
    qualified_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{NAME}:{version}"
    revision = function.get("RevisionId", "")
    environment = function.get("Environment", {}).get("Variables", {})
    secret_arn = environment.get("ADMIN_PASSWORD_SECRET_ARN", "")
    expected_environment = {
        "ADMIN_PASSWORD_SECRET_ARN": secret_arn,
        "EXPECTED_APP_FUNCTION_NAME": "honua-demo-demo-honua",
        "EXPECTED_ARCHITECTURE": "arm64",
        "EXPECTED_ARTIFACT_REFERENCE": f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}",
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
    require(VERSION_PATTERN.fullmatch(version) is not None, "helper version is not immutable and numeric")
    require(function.get("FunctionName") == NAME, "helper function name drifted")
    require(function.get("FunctionArn") == qualified_arn, "helper function ARN is not exactly qualified")
    require(function.get("Runtime") == "python3.13", "helper runtime drifted")
    require(function.get("Role") == ROLE_ARN, "helper role ARN drifted")
    require(function.get("Handler") == "handler.handler", "helper handler drifted")
    require(function.get("CodeSha256") == CODE_SHA256, "helper code hash drifted")
    require(REVISION_PATTERN.fullmatch(revision) is not None, "helper revision id is invalid")
    require(function.get("PackageType") == "Zip", "helper package type drifted")
    require(function.get("Architectures") == ["arm64"], "helper architecture drifted")
    require(function.get("Timeout") == 120 and function.get("MemorySize") == 128, "helper resource bounds drifted")
    require(environment == expected_environment, "helper environment drifted")
    require(SECRET_PATTERN.fullmatch(secret_arn) is not None, "helper secret ARN identity drifted")
    require(not function.get("Layers"), "helper has layers")
    vpc = function.get("VpcConfig", {})
    require(not vpc.get("SubnetIds") and not vpc.get("SecurityGroupIds") and not vpc.get("VpcId"), "helper has VPC attachment")
    require(not function.get("FileSystemConfigs"), "helper has filesystem attachment")
    require(not function.get("DeadLetterConfig", {}).get("TargetArn"), "helper has a dead-letter target")
    require(concurrency == {"ReservedConcurrentExecutions": 1}, "helper reserved concurrency drifted")

    require(role.get("RoleName") == ROLE_NAME and role.get("Arn") == ROLE_ARN and role.get("Path") == "/", "helper IAM role identity drifted")
    require("PermissionsBoundary" not in role, "helper IAM role has a permissions boundary")
    require(
        role.get("AssumeRolePolicyDocument")
        == {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "helper assume-role policy drifted",
    )
    require(attached.get("AttachedPolicies") == [] and attached.get("IsTruncated") is False, "helper has attached managed policies")
    require(inline.get("PolicyNames") == [POLICY_NAME] and inline.get("IsTruncated") is False, "helper inline policy set drifted")
    require(role_policy.get("RoleName") == ROLE_NAME and role_policy.get("PolicyName") == POLICY_NAME, "helper inline policy identity drifted")
    require(role_policy.get("PolicyDocument") == expected_policy(secret_arn), "helper inline policy document drifted")

    receipt = {
        "schema": DEPLOYMENT_RECEIPT_SCHEMA,
        "mergedSha": args.merged_sha,
        "planReceiptSha256": sha256(args.plan_receipt),
        "ecrEvidenceSha256": sha256(args.ecr_evidence),
        "qualifiedArn": qualified_arn,
        "version": version,
        "revisionId": revision,
        "codeSha256": CODE_SHA256,
        "roleArn": ROLE_ARN,
        "policyName": POLICY_NAME,
        "secretArn": secret_arn,
    }
    validate_deployment_receipt(receipt, args.merged_sha)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--function", type=Path, required=True)
    parser.add_argument("--concurrency", type=Path, required=True)
    parser.add_argument("--role", type=Path, required=True)
    parser.add_argument("--role-policy", type=Path, required=True)
    parser.add_argument("--attached-policies", type=Path, required=True)
    parser.add_argument("--inline-policies", type=Path, required=True)
    parser.add_argument("--plan-receipt", type=Path, required=True)
    parser.add_argument("--ecr-evidence", type=Path, required=True)
    parser.add_argument("--merged-sha", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = audit(args)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = load(args.receipt)
        validate_deployment_receipt(expected, args.merged_sha)
        if actual != expected:
            raise RuntimeError("published helper differs from the deployment receipt")
    print(f"candidate-preflight runtime audit {args.mode}: PASS")


if __name__ == "__main__":
    main()
