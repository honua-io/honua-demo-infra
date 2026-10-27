#!/usr/bin/env python3
"""Read stacks/drift-stacks.json: refuse an uncovered stack, print the CI matrix.

Usage:
  scripts/drift-stacks.py --check            # every stacks/<dir> with *.tf is listed, and nothing else
  scripts/drift-stacks.py --matrix           # JSON matrix for the drift and plan workflows
  scripts/drift-stacks.py --get STACK KEY    # one field (JSON-encoded unless a string)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACKS = ROOT / "stacks"
MANIFEST = STACKS / "drift-stacks.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def stack_dirs(root: Path = STACKS) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("*.tf")))


def uncovered(manifest: dict, root: Path = STACKS) -> list[str]:
    return [name for name in stack_dirs(root) if name not in manifest["stacks"]]


def unknown(manifest: dict, root: Path = STACKS) -> list[str]:
    present = set(stack_dirs(root))
    return sorted(name for name in manifest["stacks"] if name not in present)


def matrix(manifest: dict) -> list[dict]:
    return [
        {"stack": name, "deployed": bool(entry.get("deployed"))}
        for name, entry in sorted(manifest["stacks"].items())
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--matrix", action="store_true")
    group.add_argument("--get", nargs=2, metavar=("STACK", "KEY"))
    args = parser.parse_args()
    manifest = load_manifest()

    missing, extra = uncovered(manifest), unknown(manifest)
    if missing or extra:
        for name in missing:
            print(f"::error::stacks/{name} is a Terraform root but stacks/drift-stacks.json does not list it", file=sys.stderr)
        for name in extra:
            print(f"::error::stacks/drift-stacks.json lists {name}, which is not a Terraform root under stacks/", file=sys.stderr)
        return 1

    if args.check:
        print("drift-stacks.json covers: " + ", ".join(stack_dirs()))
    elif args.matrix:
        print(json.dumps(matrix(manifest)))
    else:
        value = manifest["stacks"][args.get[0]].get(args.get[1], "")
        print(value if isinstance(value, str) else json.dumps(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
