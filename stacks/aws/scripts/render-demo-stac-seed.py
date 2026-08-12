#!/usr/bin/env python3
"""Render the psql-authored demo STAC seed for the in-VPC query Lambda."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render(source: str, environment: str, schema: str) -> tuple[str, str]:
    if not SAFE_ENVIRONMENT.fullmatch(environment):
        raise ValueError("environment must contain only letters, digits, dot, underscore, or hyphen")
    if not SAFE_IDENTIFIER.fullmatch(schema):
        raise ValueError("schema must be a PostgreSQL identifier")
    if ":'seed_sha256'" not in source:
        raise ValueError("seed does not contain the transactional source-digest marker")

    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()

    transaction = source.find("\nBEGIN;")
    if transaction < 0:
        raise ValueError("seed has no BEGIN transaction boundary")

    rendered = source[transaction + 1 :]
    rendered = rendered.replace(':"schema"', f'"{schema}"')
    rendered = rendered.replace(":'schema'", sql_literal(schema))
    rendered = rendered.replace(":'env'", sql_literal(environment))
    rendered = rendered.replace(":'seed_sha256'", sql_literal(source_sha256))

    if re.search(r"(?m)^\\", rendered) or re.search(r":[\"']", rendered):
        raise ValueError("seed still contains psql-only directives or substitutions")
    return rendered, source_sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-file", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--schema", default="honua")
    args = parser.parse_args()

    statement, source_sha256 = render(
        args.seed_file.read_text(encoding="utf-8"), args.environment, args.schema
    )
    marker_query = (
        "SELECT marker.seed_id, marker.source_sha256, marker.metadata_environment, "
        "marker.metadata_revision::text, current.revision::text "
        "FROM honua.demo_seed_revisions AS marker "
        "JOIN honua.metadata_v2_current AS current "
        "ON current.environment = marker.metadata_environment "
        "WHERE marker.seed_id = 'demo-stac-imagery-v1'"
    )
    print(json.dumps({
        "statements": [statement],
        "query": marker_query,
        "seedSourceSha256": source_sha256,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
