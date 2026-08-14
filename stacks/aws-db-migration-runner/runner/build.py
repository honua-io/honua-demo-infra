#!/usr/bin/env python3
"""Build the deterministic runner directory from hash-locked public inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(entry: dict, cache: Path) -> Path:
    target = cache / entry["filename"]
    if not target.exists():
        with urllib.request.urlopen(entry["url"], timeout=60) as response:
            target.write_bytes(response.read())
    if digest(target.read_bytes()) != entry["sha256"]:
        raise RuntimeError(f"locked input hash mismatch: {entry['filename']}")
    return target


def validate_repo_inputs(source: Path) -> None:
    migrations = source / "migrations"
    migration_inputs = sorted(migrations.iterdir(), key=lambda path: path.name.encode("utf-8"))
    if not migration_inputs or any(not path.is_file() or path.suffix != ".sql" for path in migration_inputs):
        raise RuntimeError("migration input set contains a non-SQL file")
    inputs = [source / "requirements.lock.json", source / "handler.py", source / "migration-manifest.v1.json", *migration_inputs]
    for path in inputs:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"required repository input missing: {path.name}") from exc
        if b"\r" in data:
            raise RuntimeError(f"CR byte rejected in repository input: {path.name}")


def deterministic_zip(stage: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        members = [(path.relative_to(stage).as_posix(), path) for path in stage.rglob("*") if path.is_file()]
        for relative, path in sorted(members, key=lambda item: item[0].encode("utf-8")):
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_STORED,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    validate_repo_inputs(source)
    lock = json.loads((source / "requirements.lock.json").read_text(encoding="utf-8"))
    if set(lock) != {"schema", "wheels", "rdsCa"} or lock["schema"] != "honua-python-wheel-lock-v1":
        raise RuntimeError("dependency lock schema drifted")

    with tempfile.TemporaryDirectory(prefix="honua-db-migration-build-") as temp_name:
        temp = Path(temp_name)
        stage = temp / "stage"
        cache = temp / "cache"
        stage.mkdir()
        cache.mkdir()
        for entry in lock["wheels"]:
            wheel = fetch(entry, cache)
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    if member.is_dir() or member.filename.startswith(("/", "\\")) or ".." in Path(member.filename).parts:
                        continue
                    archive.extract(member, stage)
        ca = fetch(lock["rdsCa"], cache)
        shutil.copyfile(ca, stage / "rds-global-bundle.pem")
        for name in ("handler.py", "migration-manifest.v1.json"):
            shutil.copyfile(source / name, stage / name)
        migrations = stage / "migrations"
        shutil.copytree(source / "migrations", migrations)

        deterministic_zip(stage, output)


if __name__ == "__main__":
    main()
