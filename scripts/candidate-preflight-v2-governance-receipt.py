#!/usr/bin/env python3
"""Bind the one helper-v2 invocation to reviewed source and sealed apply proof."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "honua-candidate-preflight-v2-governance-receipt-v1"
DEPLOYMENT_SHA = "5b5a67b75e8db85cf1669e4114be4c659139e191"
APPLY_EVIDENCE_MANIFEST_SHA256 = "1209eea62baba49cee4c198040ed59f0c4a69e7ea081da41a5efac255d18fd5a"
STATE_LINEAGE = "7e7947a0-303e-b5f3-6e03-6fa7fb4ef6a2"
STATE_SERIAL = 3
HELPER_ARN = "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:2"
HELPER_REVISION_ID = "073109fb-d4c7-470e-8f4c-9a4a334a6218"
HELPER_CODE_SHA256 = "tHFeoSVqm/E5CIsnZNRdKFntc00GP7Sg/lMrtoph4pk="
HELPER_CODE_SIZE = 5732
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONTROL_PATHS = (
    "runbook/candidate-preflight-v2.md",
    "scripts/assert-candidate-preflight-v2-invocation.py",
    "scripts/assert-candidate-preflight-v2-runtime.py",
    "scripts/candidate-preflight-v2-governance-receipt.py",
    "scripts/candidate-preflight-v2-invoke.sh",
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
    "evidenceDirectoryTemplate": "$HOME/.honua-runtime-proof/candidate-preflight-v2-invocation-$GOVERNANCE_SHA",
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
        raise RuntimeError("v2 apply evidence manifest is missing or malformed") from exc
    require(isinstance(value, dict), "v2 apply evidence manifest must be an object")
    return value


def git(arguments: list[str]) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: lf_sha256(ROOT / name) for name in CONTROL_PATHS}


def canonical_evidence_dir(governance_sha: str) -> Path:
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    return (Path.home() / ".honua-runtime-proof" / f"candidate-preflight-v2-invocation-{governance_sha}").resolve()


def validate_apply_manifest(path: Path) -> dict:
    require(path.is_file(), "sealed v2 apply evidence manifest is missing")
    require(sha256(path) == APPLY_EVIDENCE_MANIFEST_SHA256, "sealed v2 apply evidence manifest hash drifted")
    manifest = load(path)
    require(manifest.get("schema") == "honua-candidate-preflight-helper-v2-final-evidence.v1", "v2 apply evidence schema drifted")
    require(manifest.get("mergeSha") == DEPLOYMENT_SHA, "v2 apply evidence deployment SHA drifted")
    require(manifest.get("applyExitCode") == 0 and manifest.get("applyAttempts") == 1, "v2 apply was not the exact single successful apply")
    require(manifest.get("postapplyAssertionPassed") is True, "v2 post-apply assertion did not pass")
    require(manifest.get("stateLineage") == STATE_LINEAGE and manifest.get("stateSerial") == STATE_SERIAL, "v2 isolated state lineage or serial drifted")
    require(
        manifest.get("helperV2") == {"version": "2", "codeSha256": HELPER_CODE_SHA256, "codeSize": HELPER_CODE_SIZE},
        "v2 helper apply pins drifted",
    )
    require(
        manifest.get("helperV1") == {"version": "1", "codeSha256": "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA=", "codeSize": 5646},
        "sealed v1 helper evidence drifted",
    )
    require(manifest.get("appVersion") == "40" and manifest.get("liveVersion") == "39", "application or live alias apply pins drifted")
    require(manifest.get("helperInvoked") is False, "v2 helper was already invoked during apply")
    require(manifest.get("secretValueRead") is False and manifest.get("databaseAccessed") is False and manifest.get("aliasMutated") is False, "v2 apply crossed its safety boundary")
    return manifest


def validate_operator_source() -> None:
    operator = (ROOT / "scripts" / "candidate-preflight-v2-invoke.sh").read_text(encoding="utf-8")
    require(operator.count("aws lambda invoke \\") == 1, "v2 operator must contain exactly one invoke command")
    require("export AWS_MAX_ATTEMPTS=1" in operator, "v2 one-attempt guard is missing")
    require(operator.count("--log-type None") == 1, "v2 invocation log boundary drifted")
    require(operator.count("--payload '{\"operation\":\"candidate-preflight-v1\"}'") == 1, "v2 invocation payload drifted")
    require(f'readonly QUALIFIED_ARN="{HELPER_ARN}"' in operator, "v2 operator is not pinned to the qualified helper")
    require("terraform " not in operator, "v2 operator must not read ambiguous Terraform current outputs")
    require("invocation-attempt-v2.json" in operator, "v2 terminal attempt marker is missing")
    require('readonly EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-v2-invocation-$GOVERNANCE_SHA"' in operator, "v2 canonical evidence directory is missing")


def require_clean_binding(governance_sha: str) -> None:
    require(SHA_PATTERN.fullmatch(governance_sha) is not None, "governance SHA is invalid")
    require(git(["rev-parse", "HEAD"]) == governance_sha, "checkout HEAD differs from governance SHA")
    require(not git(["status", "--porcelain"]), "governance checkout is not clean")
    require(
        subprocess.run(["git", "merge-base", "--is-ancestor", DEPLOYMENT_SHA, governance_sha], cwd=ROOT, check=False).returncode == 0,
        "v2 deployment commit is not an ancestor of governance",
    )
    require(
        subprocess.run(["git", "diff", "--quiet", DEPLOYMENT_SHA, governance_sha, "--", *DEPLOYMENT_PATHS], cwd=ROOT, check=False).returncode == 0,
        "v2 deployment Terraform or helper source differs under governance",
    )
    validate_operator_source()


def validate_receipt(receipt: dict, governance_sha: str, apply_manifest_path: Path) -> None:
    require(
        set(receipt) == {"schema", "governanceSha", "deploymentSha", "applyEvidenceManifestSha256", "evidenceDirectory", "stateLineage", "stateSerial", "sourceSha256", "operatorContract"},
        "v2 governance receipt keyset drifted",
    )
    require(receipt["schema"] == SCHEMA, "v2 governance receipt schema drifted")
    require(receipt["governanceSha"] == governance_sha and SHA_PATTERN.fullmatch(governance_sha) is not None, "v2 governance SHA binding drifted")
    require(receipt["deploymentSha"] == DEPLOYMENT_SHA and governance_sha != DEPLOYMENT_SHA, "v2 deployment/governance provenance is not distinct")
    require(receipt["applyEvidenceManifestSha256"] == sha256(apply_manifest_path) == APPLY_EVIDENCE_MANIFEST_SHA256, "v2 apply evidence binding drifted")
    require(receipt["evidenceDirectory"] == str(canonical_evidence_dir(governance_sha)), "v2 canonical evidence directory binding drifted")
    require(receipt["stateLineage"] == STATE_LINEAGE and receipt["stateSerial"] == STATE_SERIAL, "v2 state binding drifted")
    require(receipt["sourceSha256"] == source_hashes(), "v2 governance source hashes drifted")
    require(all(SHA256_PATTERN.fullmatch(value) for value in receipt["sourceSha256"].values()), "v2 governance source hash is invalid")
    require(receipt["operatorContract"] == OPERATOR_CONTRACT, "v2 operator contract drifted")


def build_receipt(governance_sha: str, apply_manifest_path: Path, evidence_dir: Path) -> dict:
    require_clean_binding(governance_sha)
    validate_apply_manifest(apply_manifest_path)
    require(evidence_dir.resolve() == canonical_evidence_dir(governance_sha), "caller selected a noncanonical v2 evidence directory")
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
        require(actual == expected, "v2 governance receipt differs from exact controls")
    print(f"candidate-preflight v2 governance receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
