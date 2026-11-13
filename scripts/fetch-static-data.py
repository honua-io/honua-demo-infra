#!/usr/bin/env python3
"""Fetch the static demo data listed in stacks/aws/static-data.lock.json.

Downloads each pinned release archive, refuses it unless its SHA-256 matches
the lock, extracts only the files the lock lists and refuses any whose size or
SHA-256 differs. static-data.tf uploads the files from
stacks/aws/.static-data/<prefix>/. A verified archive already on disk is reused.

Usage: scripts/fetch-static-data.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "stacks/aws/static-data.lock.json"
DATA_ROOT = ROOT / "stacks/aws/.static-data"
DOWNLOAD_TIMEOUT_SECONDS = 300


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require_relative(name: str) -> None:
    if name.startswith("/") or ".." in Path(name).parts:
        raise SystemExit(f"unsafe path in the lock: {name}")


def verified_archive(prefix: str, archive: dict) -> Path:
    require_relative(prefix)
    path = DATA_ROOT / prefix / "archive.tar.gz"
    if path.exists() and sha256_of(path) == archive["sha256"]:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    with urllib.request.urlopen(archive["url"], timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, partial.open("wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    if sha256_of(partial) != archive["sha256"]:
        partial.unlink()
        raise SystemExit(f"{prefix}: archive SHA-256 does not match the lock")
    partial.replace(path)
    return path


def archive_member(tar: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    for candidate in (name, f"./{name}"):
        try:
            member = tar.getmember(candidate)
        except KeyError:
            continue
        if member.isfile():
            return member
    raise SystemExit(f"{name} is not a regular file in the archive")


def extract_listed_files(prefix: str, archive: dict, archive_path: Path) -> None:
    target = DATA_ROOT / prefix / "files"
    with tarfile.open(archive_path, "r:gz") as tar:
        for name, expected in archive["files"].items():
            require_relative(f"{prefix}/{name}")
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".partial")
            partial.write_bytes(tar.extractfile(archive_member(tar, name)).read())
            if partial.stat().st_size != expected["bytes"] or sha256_of(partial) != expected["sha256"]:
                partial.unlink()
                raise SystemExit(f"{prefix}/{name}: size or SHA-256 does not match the lock")
            partial.replace(destination)


def main() -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for prefix, archive in lock["archives"].items():
        extract_listed_files(prefix, archive, verified_archive(prefix, archive))
        print(f"static data ready: {prefix} ({len(archive['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
