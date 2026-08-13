#!/usr/bin/env python3
"""Bind the one helper-v3 invocation to reviewed source and sealed apply proof."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "honua-candidate-preflight-v3-governance-receipt-v1"
DEPLOYMENT_SHA = "e6e292bbd5e5a2e7b6477e3595fa12efdd5ecd44"
APPLY_EVIDENCE_MANIFEST_SHA256 = "8e4e60edd3b2d066b13f1c8ae9cbb89cfb0c432d9cab72b04343d170e11f0828"
STATE_LINEAGE = "7e7947a0-303e-b5f3-6e03-6fa7fb4ef6a2"
STATE_SERIAL = 4
HELPER_ARN = "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3"
HELPER_REVISION_ID = "8114746a-223f-4358-a260-bd5699d7f992"
HELPER_CODE_SHA256 = "kp5S3LTvL8L2csu0yvP9zKwUv+0RP0Q++lgnTobCYgU="
HELPER_CODE_SIZE = 5715
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_PATHS = (
    "runbook/candidate-preflight-v3.md",
    "scripts/assert-candidate-preflight-v3-invocation.py",
    "scripts/assert-candidate-preflight-v3-runtime.py",
    "scripts/candidate-preflight-v3-governance-receipt.py",
    "scripts/candidate-preflight-v3-invoke.sh",
    "scripts/assert-candidate-preflight-ecr.py",
    "stacks/aws/candidate-preflight/classification.v1.json",
    "stacks/aws-candidate-preflight/main.tf",
)
DEPLOYMENT_PATHS = (
    "stacks/aws-candidate-preflight",
    "stacks/aws/candidate-preflight",
)
OPERATOR_CONTRACT = {
    "awsMaxAttempts": 1,
    "attemptMarkerBeforeInvoke": True,
    "evidenceDirectoryTemplate": "$HOME/.honua-runtime-proof/candidate-preflight-v3-invocation-$GOVERNANCE_SHA",
    "invokeLogType": "None",
    "payloadSha256": hashlib.sha256(b'{"operation":"candidate-preflight-v1"}').hexdigest(),
    "qualifiedArn": HELPER_ARN,
    "postAuditAlwaysAttempted": True,
    "terraformOutputLookup": False,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("v3 apply evidence manifest is missing or malformed") from exc
    require(isinstance(value, dict), "v3 apply evidence manifest must be an object")
    return value


def git(arguments: list[str]) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: lf_sha256(ROOT / name) for name in CONTROL_PATHS}


def canonical_evidence_dir(governance_sha: str) -> Path:
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    return (Path.home() / ".honua-runtime-proof" / f"candidate-preflight-v3-invocation-{governance_sha}").resolve()


def validate_apply_manifest(path: Path) -> dict:
    require(path.is_file(), "sealed v3 apply evidence manifest is missing")
    require(sha256(path) == APPLY_EVIDENCE_MANIFEST_SHA256, "sealed v3 apply evidence manifest hash drifted")
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        require(match is not None, "v3 checksum manifest line is malformed")
        digest, name = match.groups()
        require(name not in entries and name != path.name, "v3 checksum manifest contains a duplicate or recursive entry")
        artifact = path.parent / name
        require(artifact.is_file() and artifact.resolve().parent == path.parent.resolve(), "v3 checksum artifact is missing or escapes its directory")
        require(sha256(artifact) == digest, f"v3 checksum artifact drifted: {name}")
        entries[name] = digest
    require(entries.get("final-evidence.json") is not None, "v3 structured final evidence binding is missing")
    evidence = load(path.parent / "final-evidence.json")
    require(evidence.get("schema") == "honua-candidate-preflight-helper-v3-final-evidence.v1", "v3 apply evidence schema drifted")
    require(evidence.get("mergeSha") == DEPLOYMENT_SHA, "v3 apply evidence deployment SHA drifted")
    deployment = evidence.get("deployment", {})
    require(deployment.get("planAttempts") == 1 and deployment.get("applyAttempts") == 1 and deployment.get("applyExitCode") == 0, "v3 apply was not the exact single successful plan/apply")
    require((deployment.get("create"), deployment.get("update"), deployment.get("delete")) == (0, 1, 0), "v3 deployment action counts drifted")
    require(evidence.get("state") == {"lineage": STATE_LINEAGE, "beforeSerial": 3, "afterSerial": STATE_SERIAL}, "v3 isolated state lineage or serial drifted")
    require(evidence.get("helperV3") == {"version": "3", "revisionId": HELPER_REVISION_ID, "codeSha256": HELPER_CODE_SHA256, "codeSize": HELPER_CODE_SIZE, "activeSuccessful": True}, "v3 helper apply pins drifted")
    require(evidence.get("helperV2Immutable") is True and evidence.get("app40Unchanged") is True and evidence.get("live39Unchanged") is True and evidence.get("iamExact") is True, "v3 immutable runtime pins drifted")
    require(evidence.get("helperInvoked") is False, "v3 helper was already invoked during apply")
    require(evidence.get("secretValueRead") is False and evidence.get("databaseAccessed") is False and evidence.get("aliasMutated") is False, "v3 apply crossed its safety boundary")
    return evidence


def validate_operator_source() -> None:
    operator = (ROOT / "scripts" / "candidate-preflight-v3-invoke.sh").read_text(encoding="utf-8")
    require(operator.count("aws lambda invoke \\") == 1, "v3 operator must contain exactly one invoke command")
    require("export AWS_MAX_ATTEMPTS=1" in operator, "v3 one-attempt guard is missing")
    require(operator.count("--log-type None") == 1, "v3 invocation log boundary drifted")
    require(operator.count("--payload '{\"operation\":\"candidate-preflight-v1\"}'") == 1, "v3 invocation payload drifted")
    require(f'readonly QUALIFIED_ARN="{HELPER_ARN}"' in operator, "v3 operator is not pinned to the qualified helper")
    require("terraform " not in operator, "v3 operator must not read ambiguous Terraform current outputs")
    require("invocation-attempt-v3.json" in operator, "v3 terminal attempt marker is missing")
    require('readonly EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-v3-invocation-$GOVERNANCE_SHA"' in operator, "v3 canonical evidence directory is missing")


def require_clean_binding(governance_sha: str) -> None:
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    require(git(["rev-parse", "HEAD"]) == governance_sha, "checkout HEAD differs from governance SHA")
    require(not git(["status", "--porcelain"]), "governance checkout is not clean")
    require(
        subprocess.run(["git", "merge-base", "--is-ancestor", DEPLOYMENT_SHA, governance_sha], cwd=ROOT, check=False).returncode == 0,
        "v3 deployment commit is not an ancestor of governance",
    )
    require(
        subprocess.run(["git", "diff", "--quiet", DEPLOYMENT_SHA, governance_sha, "--", *DEPLOYMENT_PATHS], cwd=ROOT, check=False).returncode == 0,
        "v3 deployment Terraform or helper source differs under governance",
    )
    validate_operator_source()


def validate_receipt(receipt: dict, governance_sha: str, apply_manifest_path: Path) -> None:
    require(
        set(receipt) == {"schema", "governanceSha", "deploymentSha", "applyEvidenceManifestSha256", "evidenceDirectory", "stateLineage", "stateSerial", "sourceSha256", "operatorContract"},
        "v3 governance receipt keyset drifted",
    )
    require(receipt["schema"] == SCHEMA, "v3 governance receipt schema drifted")
    require(receipt["governanceSha"] == governance_sha and SHA_PATTERN.fullmatch(governance_sha) is not None, "v3 governance SHA binding drifted")
    require(receipt["deploymentSha"] == DEPLOYMENT_SHA and governance_sha != DEPLOYMENT_SHA, "v3 deployment/governance provenance is not distinct")
    require(receipt["applyEvidenceManifestSha256"] == sha256(apply_manifest_path) == APPLY_EVIDENCE_MANIFEST_SHA256, "v3 apply evidence binding drifted")
    require(receipt["evidenceDirectory"] == str(canonical_evidence_dir(governance_sha)), "v3 canonical evidence directory binding drifted")
    require(receipt["stateLineage"] == STATE_LINEAGE and receipt["stateSerial"] == STATE_SERIAL, "v3 state binding drifted")
    require(receipt["sourceSha256"] == source_hashes(), "v3 governance source hashes drifted")
    require(all(SHA256_PATTERN.fullmatch(value) for value in receipt["sourceSha256"].values()), "v3 governance source hash is invalid")
    require(receipt["operatorContract"] == OPERATOR_CONTRACT, "v3 operator contract drifted")


def build_receipt(governance_sha: str, apply_manifest_path: Path, evidence_dir: Path) -> dict:
    require_clean_binding(governance_sha)
    validate_apply_manifest(apply_manifest_path)
    require(evidence_dir.resolve() == canonical_evidence_dir(governance_sha), "caller selected a noncanonical v3 evidence directory")
    receipt = {
        "schema": SCHEMA,
        "governanceSha": governance_sha,
        "deploymentSha": DEPLOYMENT_SHA,
        "applyEvidenceManifestSha256": APPLY_EVIDENCE_MANIFEST_SHA256,
        "evidenceDirectory": str(canonical_evidence_dir(governance_sha)),
        "stateLineage": STATE_LINEAGE,
        "stateSerial": STATE_SERIAL,
        "sourceSha256": source_hashes(),
        "operatorContract": OPERATOR_CONTRACT,
    }
    validate_receipt(receipt, governance_sha, apply_manifest_path)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--governance-sha", required=True)
    parser.add_argument("--apply-evidence-manifest", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(args.governance_sha, args.apply_evidence_manifest, args.evidence_dir)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = load(args.receipt)
        validate_receipt(expected, args.governance_sha, args.apply_evidence_manifest)
        require(actual == expected, "v3 governance receipt differs from exact controls")
    print(f"candidate-preflight v3 governance receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
