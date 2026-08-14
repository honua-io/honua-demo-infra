#!/usr/bin/env python3
"""Validate one exact qualified migration invocation and create/verify its receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "stacks/aws-db-migration-runner/runner/migration-manifest.v1.json").read_text(encoding="utf-8"))
NAMES = [entry["name"] for entry in MANIFEST["scripts"]]
SHA = re.compile(r"^[0-9a-f]{64}$")


def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "artifact must be a JSON object")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_payload() -> dict:
    return {
        "schemaVersion": "honua-db-migration-result-v1", "operation": "apply-092-105", "status": "passed",
        "sourceCommit": MANIFEST["sourceCommit"], "candidateImageDigest": MANIFEST["candidateImageDigest"],
        "preflightReceiptSha256": MANIFEST["preflightReceiptSha256"],
        "migration": {"phase":"Expand", "beforeVersion":91, "afterVersion":105, "appliedScriptCount":14,
          "appliedScripts":NAMES, "pendingScripts":[], "executedButNotDiscoveredScripts":[],
          "pendingScriptsSha256":MANIFEST["pendingScriptsSha256"], "journalContinuous":True},
    }


def build(metadata_path: Path, payload_path: Path, snapshot_path: Path, runtime_path: Path) -> dict:
    metadata, payload, snapshot, runtime = map(load, (metadata_path, payload_path, snapshot_path, runtime_path))
    version = runtime["version"]
    require(metadata == {"StatusCode": 200, "ExecutedVersion": version}, "qualified invocation metadata drifted or contains FunctionError")
    require(payload == expected_payload(), "migration result drifted")
    require(snapshot["Status"] == "available" and snapshot["SnapshotType"] == "manual", "snapshot is not an available manual recovery point")
    require(snapshot["DBInstanceIdentifier"] == "honua-demo-demo-postgres" and snapshot["DbiResourceId"] == "db-WNTITZLSHMDLINGEGSB6TEZQYI", "snapshot source drifted")
    require(snapshot["Engine"] == "postgres" and snapshot["EngineVersion"] == "15.17" and snapshot["Encrypted"] is True, "snapshot engine/encryption drifted")
    receipt = {"schema":"honua-db-migration-invocation-receipt-v1", "runnerQualifiedArn":runtime["qualifiedArn"],
      "runnerVersion":version, "sourceCommit":MANIFEST["sourceCommit"], "candidateImageDigest":MANIFEST["candidateImageDigest"],
      "preflightReceiptSha256":MANIFEST["preflightReceiptSha256"], "pendingScriptsSha256":MANIFEST["pendingScriptsSha256"],
      "snapshotIdentifier":snapshot["DBSnapshotIdentifier"], "snapshotArn":snapshot["DBSnapshotArn"],
      "metadataSha256":sha(metadata_path), "payloadSha256":sha(payload_path), "snapshotSha256":sha(snapshot_path), "runtimeSha256":sha(runtime_path)}
    require(all(SHA.fullmatch(receipt[key]) for key in ("preflightReceiptSha256","pendingScriptsSha256","metadataSha256","payloadSha256","snapshotSha256","runtimeSha256")), "receipt digest invalid")
    return receipt


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("mode",choices=("create","verify"))
    for name in ("metadata","payload","snapshot","runtime","receipt"): parser.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args(); actual=build(args.metadata,args.payload,args.snapshot,args.runtime)
    if args.mode=="create": args.receipt.write_text(json.dumps(actual,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    else: require(load(args.receipt)==actual,"migration invocation receipt drifted")
    print(f"db migration invocation result {args.mode}: PASS")


if __name__=="__main__": main()
