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
                "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    metadata = load_document(args.metadata)
    payload = load_document(args.payload)
    assert_invocation(metadata, payload, args.expected_version)
    print("candidate-preflight invocation result: PASS")


if __name__ == "__main__":
    main()
