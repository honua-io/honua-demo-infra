#!/usr/bin/env python3
"""Validate the exact successful helper-v2 result and bind its receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION = json.loads((ROOT / "stacks" / "aws" / "candidate-preflight" / "classification.v1.json").read_text(encoding="utf-8"))
PENDING_NAMES = [entry["name"] for entry in CLASSIFICATION["scripts"]]
PENDING_DIGEST = hashlib.sha256("\n".join(PENDING_NAMES).encode("utf-8")).hexdigest()
CHECKS = ["candidate-config", "live-alias-pre", "liveness", "readiness", "deploy-preflight-diagnostics", "migration-observability", "non-contract-pending-set", "live-alias-post", "candidate-config-post"]
SCHEMA = "honua-candidate-preflight-v2-invocation-receipt-v1"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME = load_module("candidate_preflight_v2_runtime", ROOT / "scripts" / "assert-candidate-preflight-v2-runtime.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("v2 invocation artifact is missing or malformed") from exc
    require(isinstance(value, dict), "v2 invocation artifact must be an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_invocation(metadata: dict, payload: dict) -> None:
    require(len(PENDING_NAMES) == 14 and all(entry.get("phase") == "Expand" for entry in CLASSIFICATION["scripts"]), "canonical migration classification drifted")
    require(not any(entry.get("phase") == "Contract" for entry in CLASSIFICATION["scripts"]), "classification contains Contract migration")
    require(metadata == {"StatusCode": 200, "ExecutedVersion": "2"}, "v2 invoke metadata drifted or contains FunctionError")
    expected = {
        "schemaVersion": "honua-candidate-preflight-result-v1",
        "operation": "candidate-preflight-v1",
        "status": "passed",
        "candidate": {
            "functionName": "honua-demo-demo-honua",
            "version": "40",
            "revisionId": RUNTIME.APP_REVISION_ID,
            "imageDigest": RUNTIME.IMAGE_DIGEST,
            "artifactReference": f"{RUNTIME.ACCOUNT}.dkr.ecr.{RUNTIME.REGION}.amazonaws.com/honua-server@{RUNTIME.IMAGE_DIGEST}",
            "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
            "provenance": "classification-manifest+resolved-image",
        },
        "liveAlias": {"name": "live", "version": "39", "revisionId": RUNTIME.LIVE_REVISION_ID},
        "migration": {"phase": "Expand", "pendingScriptCount": 14, "pendingScriptsSha256": PENDING_DIGEST},
        "checks": CHECKS,
    }
    require(payload == expected, "v2 payload, pending 092-105 set, digest, or checks drifted")


def validate_receipt(receipt: dict) -> None:
    keys = {"schema", "governanceSha", "deploymentSha", "applyEvidenceManifestSha256", "runtimeReceiptSha256", "ecrEvidenceSha256", "invocationMetadataSha256", "invocationPayloadSha256", "executedVersion", "pendingScriptsSha256", "candidateImageDigest", "candidateSourceCommit"}
    require(set(receipt) == keys, "v2 invocation receipt keyset drifted")
    require(receipt["schema"] == SCHEMA and receipt["executedVersion"] == "2", "v2 invocation receipt identity drifted")
    require(receipt["deploymentSha"] == RUNTIME.GOVERNANCE.DEPLOYMENT_SHA and receipt["applyEvidenceManifestSha256"] == RUNTIME.GOVERNANCE.APPLY_EVIDENCE_MANIFEST_SHA256, "v2 invocation apply binding drifted")
    require(receipt["pendingScriptsSha256"] == PENDING_DIGEST, "v2 invocation pending digest drifted")
    require(receipt["candidateImageDigest"] == RUNTIME.IMAGE_DIGEST and receipt["candidateSourceCommit"] == "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad", "v2 invocation candidate provenance drifted")
    for key in ("runtimeReceiptSha256", "ecrEvidenceSha256", "invocationMetadataSha256", "invocationPayloadSha256"):
        require(RUNTIME.SHA256_PATTERN.fullmatch(receipt[key]) is not None, f"v2 invocation {key} invalid")


def build_receipt(metadata_path: Path, payload_path: Path, runtime_path: Path, evidence_path: Path) -> dict:
    metadata, payload, runtime, evidence = map(load, (metadata_path, payload_path, runtime_path, evidence_path))
    assert_invocation(metadata, payload)
    RUNTIME.validate_receipt(runtime, runtime["governanceSha"], runtime["governanceReceiptSha256"])
    require(runtime["ecrEvidenceSha256"] == sha256(evidence_path), "v2 invocation ECR binding drifted")
    RUNTIME.validate_ecr(evidence)
    receipt = {
        "schema": SCHEMA,
        "governanceSha": runtime["governanceSha"],
        "deploymentSha": runtime["deploymentSha"],
        "applyEvidenceManifestSha256": runtime["applyEvidenceManifestSha256"],
        "runtimeReceiptSha256": sha256(runtime_path),
        "ecrEvidenceSha256": sha256(evidence_path),
        "invocationMetadataSha256": sha256(metadata_path),
        "invocationPayloadSha256": sha256(payload_path),
        "executedVersion": "2",
        "pendingScriptsSha256": PENDING_DIGEST,
        "candidateImageDigest": RUNTIME.IMAGE_DIGEST,
        "candidateSourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
    }
    validate_receipt(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--ecr-evidence", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(args.metadata, args.payload, args.runtime_receipt, args.ecr_evidence)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = load(args.receipt)
        validate_receipt(expected)
        require(actual == expected, "v2 invocation artifacts or runtime receipt drifted")
    print(f"candidate-preflight v2 invocation result {args.mode}: PASS")


if __name__ == "__main__":
    main()
