#!/usr/bin/env python3
"""Bind invocation controls to a clean governance commit and exact deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "honua-candidate-preflight-governance-receipt-v1"
DEPLOYMENT_SHA = "3a00dfd36c298def8f8f49757dd56595d29097cb"
HISTORICAL_DEPLOYMENT_RECEIPT_SHA256 = "20082f457f4b44ed52db6bb5b634d8559c88c8d3dde0af7201099e789b222a29"
HISTORICAL_DEPLOYMENT_RECEIPT = {
    "schema": "honua-candidate-preflight-deployment-receipt-v1",
    "mergedSha": DEPLOYMENT_SHA,
    "planReceiptSha256": "25655190912b1e9ae2b17b1b4cea4d29ecd76b783e67f13eddbc170b15c34128",
    "ecrEvidenceSha256": "886f2f1a2c2b16c4af93f7b6a86e5ff526294fce304841bb4ad2c18f4ac9dc7e",
    "qualifiedArn": "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:1",
    "version": "1",
    "revisionId": "23959775-30a4-4654-a3dd-1e430915e1b1",
    "codeSha256": "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA=",
    "roleArn": "arn:aws:iam::585192672263:role/honua-demo-demo-candidate-preflight-role",
    "policyName": "credential-safe-candidate-preflight-v1",
    "secretArn": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-PPUA8y",
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_PATHS = (
    "runbook/candidate-preflight-v1.md",
    "scripts/assert-candidate-preflight-ecr.py",
    "scripts/assert-candidate-preflight-invocation.py",
    "scripts/assert-candidate-preflight-runtime.py",
    "scripts/candidate-preflight-governance-receipt.py",
    "scripts/candidate-preflight-invoke.sh",
    "scripts/candidate-preflight-plan-receipt.py",
    "scripts/materialize-candidate-preflight-archive.py",
)
DEPLOYMENT_PATHS = (
    "stacks/aws-candidate-preflight",
    "stacks/aws/candidate-preflight",
)
OPERATOR_CONTRACT = {
    "archiveMaterialization": {
        "archiveRelativePath": "stacks/aws-candidate-preflight/candidate-preflight.zip",
        "archiveSha256": "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0",
        "deploymentRootBasename": "candidate-preflight-plan-3a00dfd3",
        "memberSha256": {
            "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
            "handler.py": "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb",
        },
        "orderedMembers": ["classification.v1.json", "handler.py"],
        "repositoryOrigin": "https://github.com/honua-io/honua-demo-infra.git",
    },
    "awsMaxAttempts": 1,
    "iamTerminalPagination": True,
    "invokeLogType": "None",
    "payloadSha256": hashlib.sha256(b'{"operation":"candidate-preflight-v1"}').hexdigest(),
    "qualifiedHelperOnly": True,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git(arguments: list[str]) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: lf_sha256(ROOT / name) for name in CONTROL_PATHS}


def validate_historical_deployment_receipt(path: Path) -> None:
    require(path.is_file(), "historical deployment receipt is missing")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == HISTORICAL_DEPLOYMENT_RECEIPT_SHA256, "historical deployment receipt hash drifted")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("historical deployment receipt is malformed") from exc
    require(receipt == HISTORICAL_DEPLOYMENT_RECEIPT, "historical deployment receipt pins drifted")


def validate_operator_source() -> None:
    operator = (ROOT / "scripts" / "candidate-preflight-invoke.sh").read_text(encoding="utf-8")
    require(operator.count("aws lambda invoke \\") == 1, "operator must contain exactly one qualified invoke command")
    require("AWS_MAX_ATTEMPTS=1" in operator, "operator one-attempt guard is missing")
    require(operator.count("--log-type None") == 1, "operator log boundary drifted")
    require(operator.count("--payload '{\"operation\":\"candidate-preflight-v1\"}'") == 1, "operator payload boundary drifted")
    require(operator.count("python scripts/materialize-candidate-preflight-archive.py \\") == 1, "operator archive materialization boundary drifted")
    require(operator.count('--deployment-root "$DEPLOYMENT_ROOT"') == 1, "operator deployment-root boundary drifted")


def validate_receipt(receipt: dict, governance_sha: str, deployment_sha: str) -> None:
    require(isinstance(receipt, dict), "governance receipt must be an object")
    require(
        set(receipt) == {"schema", "governanceSha", "deploymentSha", "historicalDeploymentReceiptSha256", "sourceSha256", "operatorContract"},
        "governance receipt keyset drifted",
    )
    require(receipt["schema"] == SCHEMA, "governance receipt schema drifted")
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    require(deployment_sha == DEPLOYMENT_SHA, "deployment SHA is not the immutable reviewed deployment")
    require(governance_sha != deployment_sha, "governance and deployment provenance must remain distinct")
    require(receipt["governanceSha"] == governance_sha, "governance SHA binding drifted")
    require(receipt["deploymentSha"] == deployment_sha, "deployment SHA binding drifted")
    require(receipt["historicalDeploymentReceiptSha256"] == HISTORICAL_DEPLOYMENT_RECEIPT_SHA256, "historical deployment receipt binding drifted")
    require(receipt["sourceSha256"] == source_hashes(), "governance control source hashes drifted")
    require(
        all(SHA256_PATTERN.fullmatch(value) is not None for value in receipt["sourceSha256"].values()),
        "governance receipt contains an invalid source hash",
    )
    require(receipt["operatorContract"] == OPERATOR_CONTRACT, "governance operator contract drifted")


def require_clean_binding(governance_sha: str, deployment_sha: str) -> None:
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    require(deployment_sha == DEPLOYMENT_SHA, "deployment SHA is not the immutable reviewed deployment")
    require(git(["rev-parse", "HEAD"]) == governance_sha, "checkout HEAD differs from governance SHA")
    require(not git(["status", "--porcelain"]), "governance checkout is not clean")
    require(
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", deployment_sha, governance_sha],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0,
        "deployment commit is not an ancestor of governance",
    )
    require(
        subprocess.run(
            ["git", "diff", "--quiet", deployment_sha, governance_sha, "--", *DEPLOYMENT_PATHS],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0,
        "deployment Terraform or helper source differs under governance",
    )
    validate_operator_source()


def build_receipt(governance_sha: str, deployment_sha: str, historical_deployment_receipt: Path) -> dict:
    require_clean_binding(governance_sha, deployment_sha)
    validate_historical_deployment_receipt(historical_deployment_receipt)
    receipt = {
        "schema": SCHEMA,
        "governanceSha": governance_sha,
        "deploymentSha": deployment_sha,
        "historicalDeploymentReceiptSha256": HISTORICAL_DEPLOYMENT_RECEIPT_SHA256,
        "sourceSha256": source_hashes(),
        "operatorContract": OPERATOR_CONTRACT,
    }
    validate_receipt(receipt, governance_sha, deployment_sha)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--governance-sha", required=True)
    parser.add_argument("--deployment-sha", required=True)
    parser.add_argument("--historical-deployment-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(args.governance_sha, args.deployment_sha, args.historical_deployment_receipt)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = json.loads(args.receipt.read_text(encoding="utf-8"))
        validate_receipt(expected, args.governance_sha, args.deployment_sha)
        require(actual == expected, "governance receipt differs from the exact checkout and controls")
    print(f"candidate-preflight governance receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
