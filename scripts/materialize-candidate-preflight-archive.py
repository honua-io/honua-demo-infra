#!/usr/bin/env python3
"""Materialize the exact reviewed candidate-preflight archive, fail closed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


SOURCE_PATH = Path(__file__).resolve()
ROOT = SOURCE_PATH.parents[1]
DEPLOYMENT_SHA = "3a00dfd36c298def8f8f49757dd56595d29097cb"
DEPLOYMENT_ROOT_BASENAME = "candidate-preflight-plan-3a00dfd3"
REPOSITORY_ORIGIN = "https://github.com/honua-io/honua-demo-infra.git"
ARCHIVE_RELATIVE_PATH = Path("stacks/aws-candidate-preflight/candidate-preflight.zip")
ARCHIVE_SHA256 = "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0"
MEMBERS = (
    ("classification.v1.json", "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579"),
    ("handler.py", "cbf0863771f962c05e39b282dacda2294f88063ca01effa603ff425937f3a5cb"),
)
SCHEMA = "honua-candidate-preflight-archive-materialization-v1"
GOVERNANCE_SCHEMA = "honua-candidate-preflight-governance-receipt-v1"
PLAN_SCHEMA = "honua-candidate-preflight-plan-receipt-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lf_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def git_text(root: Path, *arguments: str) -> str:
    return git(root, *arguments).stdout.strip()


def canonical_git_dir(root: Path) -> Path:
    value = Path(git_text(root, "rev-parse", "--git-common-dir"))
    if not value.is_absolute():
        value = root / value
    return value.resolve()


def load_document(path: Path, label: str) -> dict:
    require(path.is_file() and not path.is_symlink(), f"{label} is missing or is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is malformed") from exc
    require(isinstance(document, dict), f"{label} must be an object")
    return document


def reject_wsl_path(raw: str) -> None:
    normalized = raw.replace("\\", "/").lower()
    require(
        not normalized.startswith(("/mnt/", "//wsl$/", "//wsl.localhost/")),
        "WSL paths are unsupported for Windows linked worktrees; use Git for Windows Bash",
    )


def require_clean_exact_worktree(root: Path, expected_sha: str, label: str) -> None:
    require(root.is_absolute() and root.is_dir(), f"{label} root must be an existing absolute directory")
    require(Path(git_text(root, "rev-parse", "--show-toplevel")).resolve() == root.resolve(), f"{label} root is not the exact Git worktree root")
    require(git_text(root, "rev-parse", "HEAD") == expected_sha, f"{label} HEAD drifted")
    require(not git_text(root, "status", "--porcelain=v1"), f"{label} worktree is dirty")
    require(git_text(root, "remote", "get-url", "origin") == REPOSITORY_ORIGIN, f"{label} repository origin drifted")


def validate_archive(
    path: Path,
    expected_archive_sha256: str | None = None,
    expected_members: tuple[tuple[str, str], ...] | None = None,
) -> list[dict[str, object]]:
    expected_archive_sha256 = expected_archive_sha256 or ARCHIVE_SHA256
    expected_members = expected_members or MEMBERS
    require(path.is_file() and not path.is_symlink(), "reviewed archive is missing or is not a regular file")
    require(sha256(path) == expected_archive_sha256, "reviewed archive SHA-256 drifted")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            require(
                tuple(entry.filename for entry in entries) == tuple(name for name, _ in expected_members),
                "reviewed archive ordered member set drifted",
            )
            require(all(not entry.is_dir() for entry in entries), "reviewed archive contains a directory")
            result = []
            for entry, (name, expected_hash) in zip(entries, expected_members, strict=True):
                content = archive.read(entry)
                require(sha256_bytes(content) == expected_hash, f"reviewed archive member hash drifted: {name}")
                result.append({"name": name, "bytes": len(content), "sha256": expected_hash})
            return result
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError("reviewed archive is malformed") from exc


def validate_receipts(
    governance_receipt_path: Path,
    plan_receipt_path: Path,
    governance_sha: str,
    deployment_sha: str,
) -> None:
    governance = load_document(governance_receipt_path, "governance receipt")
    plan = load_document(plan_receipt_path, "plan receipt")
    require(governance.get("schema") == GOVERNANCE_SCHEMA, "governance receipt schema drifted")
    require(governance.get("governanceSha") == governance_sha, "governance receipt SHA binding drifted")
    require(governance.get("deploymentSha") == deployment_sha, "governance receipt deployment binding drifted")
    source_hashes = governance.get("sourceSha256")
    require(isinstance(source_hashes, dict), "governance receipt source hashes are missing")
    own_relative = "scripts/materialize-candidate-preflight-archive.py"
    require(source_hashes.get(own_relative) == lf_sha256(SOURCE_PATH), "materializer source is not governance-bound")
    contract = governance.get("operatorContract")
    require(isinstance(contract, dict), "governance operator contract is missing")
    require(
        contract.get("archiveMaterialization")
        == {
            "archiveRelativePath": ARCHIVE_RELATIVE_PATH.as_posix(),
            "archiveSha256": ARCHIVE_SHA256,
            "deploymentRootBasename": DEPLOYMENT_ROOT_BASENAME,
            "memberSha256": {name: digest for name, digest in MEMBERS},
            "orderedMembers": [name for name, _ in MEMBERS],
            "repositoryOrigin": REPOSITORY_ORIGIN,
        },
        "governance archive-materialization contract drifted",
    )
    require(plan.get("schema") == PLAN_SCHEMA, "plan receipt schema drifted")
    require(plan.get("mergedSha") == deployment_sha, "plan receipt deployment binding drifted")
    artifacts = plan.get("artifacts")
    require(isinstance(artifacts, dict) and artifacts.get("archiveSha256") == ARCHIVE_SHA256, "plan receipt archive binding drifted")
    require(plan.get("sourceSha256") == {name: digest for name, digest in MEMBERS}, "plan receipt member binding drifted")


def materialize(
    governance_sha: str,
    deployment_sha: str,
    deployment_root_raw: str,
    governance_receipt_path: Path,
    plan_receipt_path: Path,
    receipt_path: Path,
) -> dict:
    require(deployment_sha == DEPLOYMENT_SHA, "deployment SHA is not the immutable reviewed deployment")
    require(governance_sha != deployment_sha, "governance and deployment SHAs must remain distinct")
    reject_wsl_path(deployment_root_raw)
    deployment_root = Path(deployment_root_raw)
    require(deployment_root.is_absolute(), "deployment worktree root must be absolute")
    deployment_root = deployment_root.resolve()
    require(deployment_root.name == DEPLOYMENT_ROOT_BASENAME, "deployment worktree basename drifted")
    governance_root = ROOT.resolve()
    require_clean_exact_worktree(deployment_root, deployment_sha, "deployment")
    require_clean_exact_worktree(governance_root, governance_sha, "governance")
    require(canonical_git_dir(deployment_root) == canonical_git_dir(governance_root), "deployment and governance roots are not worktrees of the same repository")
    require(
        git(governance_root, "diff", "--quiet", deployment_sha, governance_sha, "--", "stacks/aws-candidate-preflight", "stacks/aws/candidate-preflight", check=False).returncode == 0,
        "reviewed deployment source differs under governance",
    )
    validate_receipts(governance_receipt_path, plan_receipt_path, governance_sha, deployment_sha)

    source = deployment_root / ARCHIVE_RELATIVE_PATH
    destination = governance_root / ARCHIVE_RELATIVE_PATH
    members = validate_archive(source)
    ignored = git(governance_root, "check-ignore", "--quiet", "--", ARCHIVE_RELATIVE_PATH.as_posix(), check=False)
    require(ignored.returncode == 0, "archive destination is not ignored")
    require(destination.parent.resolve() == (governance_root / ARCHIVE_RELATIVE_PATH.parent).resolve(), "archive destination escaped the governance root")
    created = False
    try:
        if destination.exists() or destination.is_symlink():
            validate_archive(destination)
            require(source.read_bytes() == destination.read_bytes(), "existing destination differs from the reviewed archive")
        else:
            with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
                created = True
                shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
            require(source.read_bytes() == destination.read_bytes(), "materialized archive differs byte-for-byte")
        destination_members = validate_archive(destination)
        require(destination_members == members, "materialized archive member evidence drifted")
        require(not git_text(governance_root, "status", "--porcelain=v1"), "archive materialization dirtied the governance checkout")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise

    receipt = {
        "schema": SCHEMA,
        "governanceSha": governance_sha,
        "deploymentSha": deployment_sha,
        "repositoryOrigin": REPOSITORY_ORIGIN,
        "deploymentRootBasename": deployment_root.name,
        "archiveRelativePath": ARCHIVE_RELATIVE_PATH.as_posix(),
        "archiveSha256": ARCHIVE_SHA256,
        "archiveBytes": destination.stat().st_size,
        "members": members,
        "byteIdentical": True,
        "destinationIgnored": True,
        "worktreesClean": True,
        "governanceReceiptSha256": sha256(governance_receipt_path),
        "planReceiptSha256": sha256(plan_receipt_path),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("candidate-preflight archive materialization: PASS")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--governance-sha", required=True)
    parser.add_argument("--deployment-sha", required=True)
    parser.add_argument("--deployment-root", required=True)
    parser.add_argument("--governance-receipt", type=Path, required=True)
    parser.add_argument("--plan-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        materialize(
            args.governance_sha,
            args.deployment_sha,
            args.deployment_root,
            args.governance_receipt,
            args.plan_receipt,
            args.receipt,
        )
    except RuntimeError as exc:
        print(f"candidate-preflight archive materialization: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
