#!/usr/bin/env python3
"""Render the psql-authored demo STAC seed for the in-VPC query Lambda."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SAFE_ENVIRONMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render(source: str, environment: str, schema: str) -> str:
    if not SAFE_ENVIRONMENT.fullmatch(environment):
        raise ValueError("environment must contain only letters, digits, dot, underscore, or hyphen")
    if not SAFE_IDENTIFIER.fullmatch(schema):
        raise ValueError("schema must be a PostgreSQL identifier")

    transaction = source.find("\nBEGIN;")
    if transaction < 0:
        raise ValueError("seed has no BEGIN transaction boundary")

    rendered = source[transaction + 1 :]
    rendered = rendered.replace(':"schema"', f'"{schema}"')
    rendered = rendered.replace(":'schema'", sql_literal(schema))
    rendered = rendered.replace(":'env'", sql_literal(environment))

    if re.search(r"(?m)^\\", rendered) or re.search(r":[\"']", rendered):
        raise ValueError("seed still contains psql-only directives or substitutions")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-file", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--schema", default="honua")
    args = parser.parse_args()

    statement = render(args.seed_file.read_text(encoding="utf-8"), args.environment, args.schema)
    print(json.dumps({"statements": [statement]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
