#!/usr/bin/env python3
"""Create or verify the immutable candidate-preflight saved-plan receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


SCHEMA = "honua-candidate-preflight-plan-receipt-v2"
SOURCE_HASHES = {
    "handler.py": "589d341be3d489d5a7abbce5dd816254121ae4c5ef327a35555ae0a9efe27140",
    "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
}
ARCHIVE_SHA256 = "b4715ea1256a9bf139088b2764d45d2859ed734d063fb4a0fe532bb68a61e299"
HISTORICAL_SCHEMA = "honua-candidate-preflight-plan-receipt-v1"
HISTORICAL_SOURCE_HASHES = {
    "handler.py": "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb",
    "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
}
HISTORICAL_ARCHIVE_SHA256 = "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0"
TERRAFORM_VERSION = "1.15.8"
PLAN_FORMAT_VERSION = "1.2"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MERGED_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_SHA = "3a00dfd36c298def8f8f49757dd56595d29097cb"


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


def validate_checkout_binding(deployment_sha: str, checkout_sha: str) -> None:
    require(MERGED_SHA_PATTERN.fullmatch(checkout_sha) is not None, "checkout SHA is not an exact Git commit")
    if checkout_sha != deployment_sha:
        require(deployment_sha == DEPLOYMENT_SHA, "cross-checkout verification is not bound to the immutable deployment")


def validate_receipt(receipt: dict, merged_sha: str) -> None:
    require(isinstance(receipt, dict), "plan receipt must be a JSON object")
    require(
        set(receipt) == {"schema", "mergedSha", "terraformVersion", "planFormatVersion", "artifacts", "sourceSha256"},
        "plan receipt keyset drifted",
    )
    historical = merged_sha == DEPLOYMENT_SHA
    expected_schema = HISTORICAL_SCHEMA if historical else SCHEMA
    expected_source_hashes = HISTORICAL_SOURCE_HASHES if historical else SOURCE_HASHES
    expected_archive_sha256 = HISTORICAL_ARCHIVE_SHA256 if historical else ARCHIVE_SHA256
    require(receipt["schema"] == expected_schema, "plan receipt schema drifted")
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
    require(artifacts["archiveSha256"] == expected_archive_sha256, "plan receipt ZIP hash drifted")
    require(receipt["sourceSha256"] == expected_source_hashes, "plan receipt source hash set drifted")


def build_receipt(args) -> dict:
    checkout_sha = args.checkout_sha or args.merged_sha
    validate_checkout_binding(args.merged_sha, checkout_sha)
    require_clean_sha(checkout_sha)
    show = json.loads(args.show.read_text(encoding="utf-8"))
    historical = args.merged_sha == DEPLOYMENT_SHA
    expected_schema = HISTORICAL_SCHEMA if historical else SCHEMA
    expected_source_hashes = HISTORICAL_SOURCE_HASHES if historical else SOURCE_HASHES
    expected_archive_sha256 = HISTORICAL_ARCHIVE_SHA256 if historical else ARCHIVE_SHA256
    require(isinstance(show, dict), "Terraform show JSON must be an object")
    require(show.get("format_version") == PLAN_FORMAT_VERSION, "Terraform plan JSON format drifted")
    require(show.get("terraform_version") == TERRAFORM_VERSION, "Terraform version drifted")
    require(
        show.get("applyable") is True and show.get("complete") is True and show.get("errored") is False,
        "show JSON is not a complete applyable plan",
    )
    require(sha256(args.archive) == expected_archive_sha256, "candidate-preflight ZIP differs from the reviewed hash")
    receipt = {
        "schema": expected_schema,
        "mergedSha": args.merged_sha,
        "terraformVersion": show.get("terraform_version"),
        "planFormatVersion": show.get("format_version"),
        "artifacts": {
            "savedPlanSha256": sha256(args.plan),
            "showJsonSha256": sha256(args.show),
            "archiveSha256": sha256(args.archive),
        },
        "sourceSha256": expected_source_hashes,
    }
    validate_receipt(receipt, args.merged_sha)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--merged-sha", required=True)
    parser.add_argument("--checkout-sha")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--show", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "create":
        require(args.checkout_sha in (None, args.merged_sha), "plan receipt creation must run at the deployment SHA")
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
