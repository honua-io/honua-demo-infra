#!/usr/bin/env python3
"""Audit immutable helper :2 plus the app/alias/ECR pins for one invocation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "585192672263"
REGION = "us-west-2"
NAME = "honua-demo-demo-candidate-preflight"
QUALIFIED_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{NAME}:2"
REVISION_ID = "073109fb-d4c7-470e-8f4c-9a4a334a6218"
CODE_SHA256 = "tHFeoSVqm/E5CIsnZNRdKFntc00GP7Sg/lMrtoph4pk="
CODE_SIZE = 5732
ROLE_NAME = f"{NAME}-role"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
POLICY_NAME = "credential-safe-candidate-preflight-v1"
APP_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:honua-demo-demo-honua"
APP_VERSION = "40"
APP_REVISION_ID = "0326e209-4231-4acd-9bb4-d3cb89402db0"
LIVE_VERSION = "39"
LIVE_REVISION_ID = "4f73dd76-0294-44d3-8362-c6f8606f034e"
IMAGE_DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
HANDLER_SHA256 = "589d341be3d489d5a7abbce5dd816254121ae4c5ef327a35555ae0a9efe27140"
CLASSIFICATION_SHA256 = "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579"
SCHEMA = "honua-candidate-preflight-v2-runtime-receipt-v1"
SECRET_PATTERN = re.compile(rf"^arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{{6}}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOVERNANCE = load_module("candidate_preflight_v2_governance", ROOT / "scripts" / "candidate-preflight-v2-governance-receipt.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("v2 runtime audit input is missing or malformed") from exc
    require(isinstance(value, dict), "v2 runtime audit input must be an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_policy(secret_arn: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ReadExactAdminPassword", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": [secret_arn]},
            {"Sid": "ReadExactCandidate", "Effect": "Allow", "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration"], "Resource": [f"{APP_ARN}:40"]},
            {"Sid": "ReadExactLiveAlias", "Effect": "Allow", "Action": ["lambda:GetAlias"], "Resource": [APP_ARN]},
            {"Sid": "InvokeExactCandidate", "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": [f"{APP_ARN}:40"]},
            {"Sid": "WriteExactLogGroup", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{NAME}:*"]},
        ],
    }


def validate_ecr(evidence: dict) -> None:
    require(evidence.get("schema") == "honua-candidate-preflight-ecr-evidence-v1", "ECR evidence schema drifted")
    require(evidence.get("registryId") == ACCOUNT and evidence.get("region") == REGION and evidence.get("repositoryName") == "honua-server", "ECR identity drifted")
    require(evidence.get("imageDigest") == IMAGE_DIGEST and evidence.get("manifestSha256") == IMAGE_DIGEST, "ECR digest drifted")
    config = evidence.get("config", {})
    require(config.get("digest") == "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f", "ECR config digest drifted")
    require(config.get("architecture") == "arm64" and config.get("os") == "linux", "ECR platform drifted")
    require(config.get("entrypoint") == ["/var/task/Honua.Server"] and config.get("nativeAot") == "native-aot", "ECR executable provenance drifted")
    require(config.get("ociRevision") == "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad" and config.get("honuaGitSha") == "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad", "ECR source provenance drifted")


def validate_receipt(receipt: dict, governance_sha: str, governance_receipt_sha: str) -> None:
    keys = {"schema", "governanceSha", "deploymentSha", "applyEvidenceManifestSha256", "governanceReceiptSha256", "ecrEvidenceSha256", "stateLineage", "stateSerial", "qualifiedArn", "version", "revisionId", "codeSha256", "codeSize", "roleArn", "policyName", "secretArn", "candidateVersion", "candidateRevisionId", "candidateImageDigest", "liveVersion", "liveRevisionId"}
    require(set(receipt) == keys, "v2 runtime receipt keyset drifted")
    require(receipt["schema"] == SCHEMA, "v2 runtime receipt schema drifted")
    require(receipt["governanceSha"] == governance_sha and receipt["deploymentSha"] == GOVERNANCE.DEPLOYMENT_SHA, "v2 runtime provenance drifted")
    require(receipt["applyEvidenceManifestSha256"] == GOVERNANCE.APPLY_EVIDENCE_MANIFEST_SHA256, "v2 runtime apply receipt drifted")
    require(receipt["governanceReceiptSha256"] == governance_receipt_sha, "v2 runtime governance receipt drifted")
    require(SHA256_PATTERN.fullmatch(receipt["ecrEvidenceSha256"]) is not None, "v2 runtime ECR hash invalid")
    require(receipt["stateLineage"] == GOVERNANCE.STATE_LINEAGE and receipt["stateSerial"] == GOVERNANCE.STATE_SERIAL, "v2 runtime state binding drifted")
    require(receipt["qualifiedArn"] == QUALIFIED_ARN and receipt["version"] == "2", "v2 runtime helper identity drifted")
    require(receipt["revisionId"] == REVISION_ID and receipt["codeSha256"] == CODE_SHA256 and receipt["codeSize"] == CODE_SIZE, "v2 runtime helper artifact drifted")
    require(receipt["roleArn"] == ROLE_ARN and receipt["policyName"] == POLICY_NAME and SECRET_PATTERN.fullmatch(receipt["secretArn"]), "v2 runtime IAM/secret identity drifted")
    require(receipt["candidateVersion"] == APP_VERSION and receipt["candidateRevisionId"] == APP_REVISION_ID and receipt["candidateImageDigest"] == IMAGE_DIGEST, "v2 runtime candidate pins drifted")
    require(receipt["liveVersion"] == LIVE_VERSION and receipt["liveRevisionId"] == LIVE_REVISION_ID, "v2 runtime live pins drifted")


def audit(args) -> dict:
    function = load(args.function)["Configuration"]
    candidate_doc = load(args.candidate)
    candidate = candidate_doc["Configuration"]
    live = load(args.live_alias)
    concurrency = load(args.concurrency)
    role = load(args.role)["Role"]
    role_policy = load(args.role_policy)
    attached = load(args.attached_policies)
    inline = load(args.inline_policies)
    governance_receipt = load(args.governance_receipt)
    ecr = load(args.ecr_evidence)
    GOVERNANCE.validate_apply_manifest(args.apply_evidence_manifest)
    GOVERNANCE.validate_receipt(governance_receipt, args.governance_sha, args.apply_evidence_manifest)
    validate_ecr(ecr)

    secret_arn = function.get("Environment", {}).get("Variables", {}).get("ADMIN_PASSWORD_SECRET_ARN", "")
    expected_environment = {
        "ADMIN_PASSWORD_SECRET_ARN": secret_arn,
        "EXPECTED_APP_FUNCTION_NAME": "honua-demo-demo-honua",
        "EXPECTED_ARCHITECTURE": "arm64",
        "EXPECTED_ARTIFACT_REFERENCE": f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}",
        "EXPECTED_CANDIDATE_REVISION_ID": APP_REVISION_ID,
        "EXPECTED_CANDIDATE_VERSION": APP_VERSION,
        "EXPECTED_IMAGE_DIGEST": IMAGE_DIGEST,
        "EXPECTED_LIVE_ALIAS_NAME": "live",
        "EXPECTED_LIVE_REVISION_ID": LIVE_REVISION_ID,
        "EXPECTED_LIVE_VERSION": LIVE_VERSION,
        "EXPECTED_PACKAGE_TYPE": "Image",
        "EXPECTED_SKIP_MIGRATIONS": "true",
        "EXPECTED_SOURCE_COMMIT": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
        "SOURCE_CLASSIFICATION_SHA256": CLASSIFICATION_SHA256,
        "SOURCE_HANDLER_SHA256": HANDLER_SHA256,
    }
    require(function.get("FunctionName") == NAME and function.get("FunctionArn") == QUALIFIED_ARN and function.get("Version") == "2", "runtime is not exact helper :2")
    require(function.get("RevisionId") == REVISION_ID and function.get("CodeSha256") == CODE_SHA256 and function.get("CodeSize") == CODE_SIZE, "helper :2 revision/code drifted")
    require(function.get("State") == "Active" and function.get("LastUpdateStatus") == "Successful", "helper :2 is not active/successful")
    require(function.get("Runtime") == "python3.13" and function.get("Handler") == "handler.handler" and function.get("Architectures") == ["arm64"], "helper :2 execution shape drifted")
    require(function.get("Role") == ROLE_ARN and function.get("Timeout") == 120 and function.get("MemorySize") == 128, "helper :2 role/resource bounds drifted")
    require(function.get("Environment", {}).get("Variables") == expected_environment and SECRET_PATTERN.fullmatch(secret_arn), "helper :2 environment drifted")
    require(not function.get("Layers") and not function.get("FileSystemConfigs") and not function.get("DeadLetterConfig", {}).get("TargetArn"), "helper :2 has an attached execution surface")
    vpc = function.get("VpcConfig", {})
    require(not vpc.get("SubnetIds") and not vpc.get("SecurityGroupIds") and not vpc.get("VpcId"), "helper :2 has VPC attachment")
    require(concurrency == {"ReservedConcurrentExecutions": 1}, "helper concurrency drifted")

    require(candidate.get("FunctionArn") == f"{APP_ARN}:40" and candidate.get("Version") == APP_VERSION and candidate.get("RevisionId") == APP_REVISION_ID, "candidate :40 identity drifted")
    require(candidate.get("PackageType") == "Image" and candidate.get("Architectures") == ["arm64"], "candidate :40 package drifted")
    require(candidate.get("Environment", {}).get("Variables", {}).get("HONUA_SKIP_MIGRATIONS") == "true", "candidate :40 migration mode drifted")
    require(candidate.get("Environment", {}).get("Variables", {}).get("ControlPlane__DeployTargets__0__ArtifactReference") == f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}", "candidate :40 artifact reference drifted")
    require(candidate_doc.get("Code", {}).get("ResolvedImageUri") == f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/honua-server@{IMAGE_DIGEST}", "candidate :40 resolved image drifted")
    require(live.get("Name") == "live" and live.get("FunctionVersion") == LIVE_VERSION and live.get("RevisionId") == LIVE_REVISION_ID, "live alias identity drifted")
    require(not live.get("RoutingConfig"), "live alias has routing configuration")

    require(role.get("RoleName") == ROLE_NAME and role.get("Arn") == ROLE_ARN and role.get("Path") == "/", "helper role identity drifted")
    require("PermissionsBoundary" not in role, "helper role has a permissions boundary")
    require(attached == {"AttachedPolicies": [], "IsTruncated": False}, "helper has attached policies or pagination")
    require(inline == {"PolicyNames": [POLICY_NAME], "IsTruncated": False}, "helper inline policy set drifted")
    require(role_policy.get("RoleName") == ROLE_NAME and role_policy.get("PolicyName") == POLICY_NAME and role_policy.get("PolicyDocument") == expected_policy(secret_arn), "helper inline policy drifted")

    receipt = {
        "schema": SCHEMA,
        "governanceSha": args.governance_sha,
        "deploymentSha": GOVERNANCE.DEPLOYMENT_SHA,
        "applyEvidenceManifestSha256": GOVERNANCE.APPLY_EVIDENCE_MANIFEST_SHA256,
        "governanceReceiptSha256": sha256(args.governance_receipt),
        "ecrEvidenceSha256": sha256(args.ecr_evidence),
        "stateLineage": GOVERNANCE.STATE_LINEAGE,
        "stateSerial": GOVERNANCE.STATE_SERIAL,
        "qualifiedArn": QUALIFIED_ARN,
        "version": "2",
        "revisionId": REVISION_ID,
        "codeSha256": CODE_SHA256,
        "codeSize": CODE_SIZE,
        "roleArn": ROLE_ARN,
        "policyName": POLICY_NAME,
        "secretArn": secret_arn,
        "candidateVersion": APP_VERSION,
        "candidateRevisionId": APP_REVISION_ID,
        "candidateImageDigest": IMAGE_DIGEST,
        "liveVersion": LIVE_VERSION,
        "liveRevisionId": LIVE_REVISION_ID,
    }
    validate_receipt(receipt, args.governance_sha, sha256(args.governance_receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    for name in ("function", "candidate", "live-alias", "concurrency", "role", "role-policy", "attached-policies", "inline-policies", "apply-evidence-manifest", "governance-receipt", "ecr-evidence", "receipt"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--governance-sha", required=True)
    args = parser.parse_args()
    actual = audit(args)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = load(args.receipt)
        validate_receipt(expected, args.governance_sha, sha256(args.governance_receipt))
        require(actual == expected, "helper/app/alias/ECR differ from v2 runtime receipt")
    print(f"candidate-preflight v2 runtime audit {args.mode}: PASS")


if __name__ == "__main__":
    main()
