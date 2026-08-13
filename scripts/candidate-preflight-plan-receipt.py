#!/usr/bin/env python3
"""Create or verify the immutable candidate-preflight saved-plan receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


SCHEMA = "honua-candidate-preflight-plan-receipt-v1"
SOURCE_HASHES = {
    "handler.py": "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb",
    "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
}
ARCHIVE_SHA256 = "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0"
TERRAFORM_VERSION = "1.15.8"
PLAN_FORMAT_VERSION = "1.2"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MERGED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(command: list[str]) -> str:
    return subprocess.check_output(["git", *command], text=True).strip()


def require_clean_sha(expected: str) -> None:
    require(MERGED_SHA_PATTERN.fullmatch(expected) is not None, "merged SHA is not an exact Git commit")
    if git(["rev-parse", "HEAD"]) != expected:
        raise RuntimeError("checkout HEAD differs from the reviewed merged SHA")
    if git(["status", "--porcelain"]):
        raise RuntimeError("checkout is not clean")


def validate_receipt(receipt: dict, merged_sha: str) -> None:
    require(isinstance(receipt, dict), "plan receipt must be a JSON object")
    require(
        set(receipt) == {"schema", "mergedSha", "terraformVersion", "planFormatVersion", "artifacts", "sourceSha256"},
        "plan receipt keyset drifted",
    )
    require(receipt["schema"] == SCHEMA, "plan receipt schema drifted")
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


def build_receipt(args) -> dict:
    require_clean_sha(args.merged_sha)
    show = json.loads(args.show.read_text(encoding="utf-8"))
    require(isinstance(show, dict), "Terraform show JSON must be an object")
    require(show.get("format_version") == PLAN_FORMAT_VERSION, "Terraform plan JSON format drifted")
    require(show.get("terraform_version") == TERRAFORM_VERSION, "Terraform version drifted")
    require(
        show.get("applyable") is True and show.get("complete") is True and show.get("errored") is False,
        "show JSON is not a complete applyable plan",
    )
    require(sha256(args.archive) == ARCHIVE_SHA256, "candidate-preflight ZIP differs from the reviewed hash")
    receipt = {
        "schema": SCHEMA,
        "mergedSha": args.merged_sha,
        "terraformVersion": show.get("terraform_version"),
        "planFormatVersion": show.get("format_version"),
        "artifacts": {
            "savedPlanSha256": sha256(args.plan),
            "showJsonSha256": sha256(args.show),
            "archiveSha256": sha256(args.archive),
        },
        "sourceSha256": SOURCE_HASHES,
    }
    validate_receipt(receipt, args.merged_sha)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--merged-sha", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--show", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    actual = build_receipt(args)
    if args.mode == "create":
        args.receipt.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        expected = json.loads(args.receipt.read_text(encoding="utf-8"))
        validate_receipt(expected, args.merged_sha)
        if actual != expected:
            raise RuntimeError("saved plan, show JSON, archive, checkout, or receipt binding drifted")
    print(f"candidate-preflight plan receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
