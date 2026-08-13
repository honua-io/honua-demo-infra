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
)
DEPLOYMENT_PATHS = (
    "stacks/aws-candidate-preflight",
    "stacks/aws/candidate-preflight",
)
OPERATOR_CONTRACT = {
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


def validate_receipt(receipt: dict, governance_sha: str, deployment_sha: str) -> None:
    require(isinstance(receipt, dict), "governance receipt must be an object")
    require(
        set(receipt) == {"schema", "governanceSha", "deploymentSha", "sourceSha256", "operatorContract"},
        "governance receipt keyset drifted",
    )
    require(receipt["schema"] == SCHEMA, "governance receipt schema drifted")
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    require(deployment_sha == DEPLOYMENT_SHA, "deployment SHA is not the immutable reviewed deployment")
    require(governance_sha != deployment_sha, "governance and deployment provenance must remain distinct")
    require(receipt["governanceSha"] == governance_sha, "governance SHA binding drifted")
    require(receipt["deploymentSha"] == deployment_sha, "deployment SHA binding drifted")
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


def build_receipt(governance_sha: str, deployment_sha: str) -> dict:
    require_clean_binding(governance_sha, deployment_sha)
    receipt = {
        "schema": SCHEMA,
        "governanceSha": governance_sha,
        "deploymentSha": deployment_sha,
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
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(args.governance_sha, args.deployment_sha)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = json.loads(args.receipt.read_text(encoding="utf-8"))
        validate_receipt(expected, args.governance_sha, args.deployment_sha)
        require(actual == expected, "governance receipt differs from the exact checkout and controls")
    print(f"candidate-preflight governance receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
