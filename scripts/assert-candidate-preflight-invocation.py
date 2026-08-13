#!/usr/bin/env python3
"""Validate AWS invoke metadata and the exact candidate-preflight result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


SCHEMA = "honua-candidate-preflight-result-v1"
OPERATION = "candidate-preflight-v1"
CLASSIFICATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "stacks"
    / "aws"
    / "candidate-preflight"
    / "classification.v1.json"
)
CLASSIFICATION = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))
PENDING_NAMES = [entry["name"] for entry in CLASSIFICATION["scripts"]]
PENDING_DIGEST = hashlib.sha256("\n".join(PENDING_NAMES).encode("utf-8")).hexdigest()
CHECKS = [
    "candidate-config",
    "live-alias-pre",
    "liveness",
    "readiness",
    "deploy-preflight-diagnostics",
    "migration-observability",
    "non-contract-pending-set",
    "live-alias-post",
    "candidate-config-post",
]
VERSION_PATTERN = re.compile(r"^[1-9][0-9]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
INVOCATION_RECEIPT_SCHEMA = "honua-candidate-preflight-invocation-receipt-v2"
DEPLOYMENT_RECEIPT_SCHEMA = "honua-candidate-preflight-deployment-receipt-v2"
DEPLOYMENT_SHA = "3a00dfd36c298def8f8f49757dd56595d29097cb"
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_RECEIPT_KEYS = {
    "schema", "governanceSha", "deploymentSha", "governanceReceiptSha256",
    "planReceiptSha256", "ecrEvidenceSha256", "qualifiedArn", "version",
    "revisionId", "codeSha256", "roleArn", "policyName", "secretArn",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_document(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invoke metadata or payload is missing/malformed") from exc
    require(isinstance(document, dict), "invoke metadata or payload must be a JSON object")
    return document


def assert_invocation(metadata: dict, payload: dict, expected_version: str) -> None:
    require(
        CLASSIFICATION.get("schemaVersion") == "honua-candidate-preflight-classification-v1"
        and len(PENDING_NAMES) == 14
        and all(entry.get("phase") == "Expand" for entry in CLASSIFICATION["scripts"]),
        "canonical classification manifest drifted",
    )
    require(VERSION_PATTERN.fullmatch(expected_version) is not None, "expected helper version is not immutable and numeric")
    require(set(metadata) == {"StatusCode", "ExecutedVersion"}, "invoke metadata schema drifted or contains FunctionError")
    require(metadata["StatusCode"] == 200, "invoke transport status is not 200")
    require(metadata["ExecutedVersion"] == expected_version, "invoke executed the wrong helper version")
    require(
        payload
        == {
            "schemaVersion": SCHEMA,
            "operation": OPERATION,
            "status": "passed",
            "candidate": {
                "functionName": "honua-demo-demo-honua",
                "version": "40",
                "revisionId": "0326e209-4231-4acd-9bb4-d3cb89402db0",
                "imageDigest": "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
                "artifactReference": "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
                "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
                "provenance": "classification-manifest+resolved-image",
            },
            "liveAlias": {
                "name": "live",
                "version": "39",
                "revisionId": "4f73dd76-0294-44d3-8362-c6f8606f034e",
            },
            "migration": {
                "phase": "Expand",
                "pendingScriptCount": 14,
                "pendingScriptsSha256": PENDING_DIGEST,
            },
            "checks": CHECKS,
        },
        "candidate-preflight payload schema, pins, migration contract, or checks drifted",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_receipt(
    metadata_path: Path,
    payload_path: Path,
    deployment_path: Path,
    evidence_path: Path,
    expected_version: str,
) -> dict:
    metadata = load_document(metadata_path)
    payload = load_document(payload_path)
    deployment = load_document(deployment_path)
    evidence = load_document(evidence_path)
    assert_invocation(metadata, payload, expected_version)
    require(
        set(deployment) == DEPLOYMENT_RECEIPT_KEYS and deployment.get("schema") == DEPLOYMENT_RECEIPT_SCHEMA,
        "deployment receipt schema drifted",
    )
    governance_sha = deployment.get("governanceSha", "")
    deployment_sha = deployment.get("deploymentSha", "")
    require(GIT_SHA_PATTERN.fullmatch(governance_sha) is not None, "deployment receipt governance SHA is invalid")
    require(deployment_sha == DEPLOYMENT_SHA, "deployment receipt is not bound to the immutable deployment")
    require(governance_sha != deployment_sha, "deployment and governance provenance are not distinct")
    require(SHA256_PATTERN.fullmatch(deployment.get("governanceReceiptSha256", "")) is not None, "deployment receipt governance hash is invalid")
    require(
        evidence.get("schema") == "honua-candidate-preflight-ecr-evidence-v1",
        "ECR evidence schema drifted",
    )
    evidence_hash = sha256(evidence_path)
    require(
        deployment.get("ecrEvidenceSha256") == evidence_hash,
        "deployment receipt is not bound to the exact ECR evidence",
    )
    return {
        "schema": INVOCATION_RECEIPT_SCHEMA,
        "governanceSha": governance_sha,
        "deploymentSha": deployment_sha,
        "governanceReceiptSha256": deployment["governanceReceiptSha256"],
        "deploymentReceiptSha256": sha256(deployment_path),
        "ecrEvidenceSha256": evidence_hash,
        "invocationMetadataSha256": sha256(metadata_path),
        "invocationPayloadSha256": sha256(payload_path),
        "executedVersion": expected_version,
        "candidateImageDigest": CLASSIFICATION["candidate"]["imageDigest"],
        "candidateSourceCommit": CLASSIFICATION["candidate"]["sourceCommit"],
    }


def validate_receipt(receipt: dict) -> None:
    require(
        set(receipt)
        == {
            "schema",
            "governanceSha",
            "deploymentSha",
            "governanceReceiptSha256",
            "deploymentReceiptSha256",
            "ecrEvidenceSha256",
            "invocationMetadataSha256",
            "invocationPayloadSha256",
            "executedVersion",
            "candidateImageDigest",
            "candidateSourceCommit",
        },
        "invocation receipt keyset drifted",
    )
    require(receipt["schema"] == INVOCATION_RECEIPT_SCHEMA, "invocation receipt schema drifted")
    require(GIT_SHA_PATTERN.fullmatch(receipt["governanceSha"]) is not None, "invocation receipt governance SHA is invalid")
    require(receipt["deploymentSha"] == DEPLOYMENT_SHA, "invocation receipt deployment SHA drifted")
    require(receipt["governanceSha"] != receipt["deploymentSha"], "invocation receipt provenance is not distinct")
    for key in (
        "governanceReceiptSha256",
        "deploymentReceiptSha256",
        "ecrEvidenceSha256",
        "invocationMetadataSha256",
        "invocationPayloadSha256",
    ):
        require(bool(SHA256_PATTERN.fullmatch(receipt[key])), f"invocation receipt {key} is invalid")
    require(bool(VERSION_PATTERN.fullmatch(receipt["executedVersion"])), "invocation receipt version is invalid")
    require(receipt["candidateImageDigest"] == CLASSIFICATION["candidate"]["imageDigest"], "invocation receipt image drifted")
    require(receipt["candidateSourceCommit"] == CLASSIFICATION["candidate"]["sourceCommit"], "invocation receipt source drifted")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--deployment-receipt", type=Path, required=True)
    parser.add_argument("--ecr-evidence", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(
        args.metadata,
        args.payload,
        args.deployment_receipt,
        args.ecr_evidence,
        args.expected_version,
    )
    validate_receipt(actual)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = load_document(args.receipt)
        validate_receipt(expected)
        require(actual == expected, "invocation artifacts, deployment receipt, or ECR evidence drifted")
    print(f"candidate-preflight invocation result {args.mode}: PASS")


if __name__ == "__main__":
    main()
