#!/usr/bin/env python3
"""Create or verify the immutable candidate-preflight saved-plan receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


SCHEMA = "honua-candidate-preflight-plan-receipt-v1"
SOURCE_HASHES = {
    "handler.py": "589a67ec77eb49086a083ebf85f1a3143011831645bd885147755c026c65995f",
    "classification.v1.json": "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579",
}
ARCHIVE_SHA256 = "2a9ed89735cce462f7e2323803f0f1ea44cefec01004f5cfa04f902af056d216"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(command: list[str]) -> str:
    return subprocess.check_output(["git", *command], text=True).strip()


def require_clean_sha(expected: str) -> None:
    if git(["rev-parse", "HEAD"]) != expected:
        raise RuntimeError("checkout HEAD differs from the reviewed merged SHA")
    if git(["status", "--porcelain"]):
        raise RuntimeError("checkout is not clean")


def build_receipt(args) -> dict:
    require_clean_sha(args.merged_sha)
    show = json.loads(args.show.read_text(encoding="utf-8"))
    if show.get("format_version", "").split(".", 1)[0] != "1":
        raise RuntimeError("unsupported Terraform plan JSON format")
    if show.get("applyable") is not True or show.get("complete") is not True or show.get("errored") is True:
        raise RuntimeError("show JSON is not a complete applyable plan")
    if sha256(args.archive) != ARCHIVE_SHA256:
        raise RuntimeError("candidate-preflight ZIP differs from the reviewed hash")
    return {
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
        if actual != expected:
            raise RuntimeError("saved plan, show JSON, archive, checkout, or receipt binding drifted")
    print(f"candidate-preflight plan receipt {args.mode}: PASS")


if __name__ == "__main__":
    main()
