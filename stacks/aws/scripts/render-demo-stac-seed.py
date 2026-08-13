#!/usr/bin/env python3
"""Validate a pinned demo STAC seed and emit the allowlisted manager event."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
SAFE_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def render(source: str, environment: str, schema: str) -> tuple[str, str, str]:
    if not SAFE_ENVIRONMENT.fullmatch(environment):
        raise ValueError("environment must contain only letters, digits, dot, underscore, or hyphen")
    if schema != "honua":
        raise ValueError("schema must be the migration-owned honua schema")
    begin = source.find("\nBEGIN;")
    commit = source.rfind("\nCOMMIT;")
    if begin < 0 or commit <= begin or source[commit + len("\nCOMMIT;") :].strip():
        raise ValueError("seed must contain one outer transaction boundary")
    rendered = source[begin + len("\nBEGIN;") : commit]
    rendered = rendered.replace(':"schema"', '"honua"')
    rendered = rendered.replace(":'schema'", "'honua'")
    rendered = rendered.replace(":'env'", "'" + environment.replace("'", "''") + "'")
    if re.search(r"(?m)^\\", rendered) or re.search(r":[\"']", rendered):
        raise ValueError("seed still contains psql-only directives or substitutions")
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    execution_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return rendered, source_sha256, execution_sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-file", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--schema", default="honua")
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--server-commit", required=True)
    args = parser.parse_args()

    if not SAFE_DIGEST.fullmatch(args.expected_source_sha256):
        parser.error("--expected-source-sha256 must be a lowercase SHA-256 digest")
    if not SAFE_COMMIT.fullmatch(args.server_commit):
        parser.error("--server-commit must be an exact lowercase 40-character commit")
    _, source_sha256, execution_sha256 = render(
        args.seed_file.read_text(encoding="utf-8"), args.environment, args.schema
    )
    if source_sha256 != args.expected_source_sha256:
        raise ValueError("seed file does not match --expected-source-sha256")

    print(
        f"validated source={source_sha256} execution={execution_sha256} "
        f"server={args.server_commit} environment={args.environment}",
        file=sys.stderr,
    )
    # The manager rejects every caller-controlled seed value. Its deployed
    # environment independently pins and verifies the URL, commit, source digest,
    # metadata environment, and exact rendered execution digest.
    print(json.dumps({"operation": "apply-demo-stac-seed"}, separators=(",", ":")))


if __name__ == "__main__":
    main()
