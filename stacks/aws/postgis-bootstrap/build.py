#!/usr/bin/env python3
"""Build the PostGIS bootstrap package directory from hash-locked inputs.

The directory this writes (postgis-bootstrap/build/) is what
`data.archive_file.postgis_bootstrap` zips. Three Lambdas deploy that zip
(postgis_bootstrap, stac_seed_manager, stac_seed_receipt), so the bytes must be
the same on every host that plans or applies: an operator workstation, the PR
plan job, the apply job and the nightly drift plan. The old build ran
`pip install` with only pg8000 pinned, so the transitive set drifted on every
PyPI release (scramp 1.5.0 broke the bootstrap on 2026-10-27), and pip's
bytecode and file modes changed the hash on every host. Every plan therefore
showed a code-hash update on all three functions.

This build installs nothing. It downloads each wheel and the RDS CA bundle
named in requirements.lock.json, refuses any whose sha256 differs, unpacks the
wheels (pure-Python, py3-none-any) and copies handler.py. Every file is
written 0644 with normalised paths, so the tree is a function of the lock and
handler.py alone.

Usage:
  python3 build.py                 # build into ./build next to this script
  python3 build.py --cache DIR     # reuse downloads across builds
  python3 build.py --check         # build twice into temp dirs; fail unless identical
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
LOCK = HERE / "requirements.lock.json"
HANDLER = HERE / "handler.py"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, expected: str, target: Path) -> bytes:
    if target.exists() and sha256(target.read_bytes()) == expected:
        return target.read_bytes()
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    actual = sha256(data)
    if actual != expected:
        raise SystemExit(
            f"locked input hash mismatch for {target.name}: expected {expected}, got {actual}. "
            "Update requirements.lock.json in a reviewed PR if the change is intended."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SystemExit(f"refusing wheel member outside the package root: {name}")
    return path


def write_file(root: Path, relative: PurePosixPath, data: bytes) -> None:
    target = root.joinpath(*relative.parts)
    if target.exists():
        raise SystemExit(f"two inputs write the same path: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    os.chmod(target, 0o644)


def build(out: Path, cache: Path) -> str:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    handler = HANDLER.read_bytes()
    if b"\r" in handler:
        raise SystemExit("handler.py contains CR bytes; commit it with LF line endings")

    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    for wheel in lock["wheels"]:
        if not wheel["filename"].endswith("-none-any.whl"):
            raise SystemExit(f"{wheel['filename']} is not a pure-Python wheel")
        data = fetch(wheel["url"], wheel["sha256"], cache / wheel["filename"])
        with zipfile.ZipFile(cache / wheel["filename"]) as archive:
            for info in sorted(archive.infolist(), key=lambda i: i.filename):
                if info.is_dir():
                    continue
                write_file(out, safe_member(info.filename), archive.read(info))
        del data

    write_file(out, PurePosixPath("handler.py"), handler)

    ca = lock["rds_ca_bundle"]
    ca_bytes = fetch(ca["url"], ca["sha256"], cache / ca["filename"])
    if b"BEGIN CERTIFICATE" not in ca_bytes:
        raise SystemExit("the locked RDS CA bundle is not a PEM certificate file")
    write_file(out, PurePosixPath(ca["filename"]), ca_bytes)

    return tree_digest(out)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode() + b"\0" + sha256(path.read_bytes()).encode() + b"\n")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=HERE / "build")
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="build twice and require identical trees")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        cache = args.cache or Path(scratch) / "cache"
        if args.check:
            first = build(Path(scratch) / "a", cache)
            second = build(Path(scratch) / "b", cache)
            if first != second:
                print(f"non-deterministic build: {first} != {second}", file=sys.stderr)
                return 1
        digest = build(args.out, cache)
    print(f"postgis-bootstrap build tree sha256 {digest} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
