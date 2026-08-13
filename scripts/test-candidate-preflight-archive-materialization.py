#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize-candidate-preflight-archive.py"


def load_module():
    spec = importlib.util.spec_from_file_location("candidate_archive_materializer", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load archive materializer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATERIALIZER = load_module()


class Fixture:
    def __init__(self, temporary: str, ignored: bool = True):
        self.base = Path(temporary)
        self.governance = self.base / "governance"
        self.governance.mkdir()

        def git(root: Path, *args: str) -> str:
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

        git(self.governance, "init", "-q")
        git(self.governance, "config", "user.name", "Archive Materializer Test")
        git(self.governance, "config", "user.email", "archive-materializer@example.invalid")
        git(self.governance, "remote", "add", "origin", MATERIALIZER.REPOSITORY_ORIGIN)
        (self.governance / ".gitignore").write_text(
            "stacks/aws-candidate-preflight/candidate-preflight.zip\n" if ignored else "unrelated\n",
            encoding="utf-8",
        )
        for relative in ("stacks/aws-candidate-preflight", "stacks/aws/candidate-preflight"):
            path = self.governance / relative / "fixture.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("immutable deployment\n", encoding="utf-8")
        git(self.governance, "add", ".")
        git(self.governance, "commit", "-q", "-m", "deployment")
        self.deployment_sha = git(self.governance, "rev-parse", "HEAD")
        control = self.governance / "runbook.txt"
        control.write_text("governance\n", encoding="utf-8")
        git(self.governance, "add", ".")
        git(self.governance, "commit", "-q", "-m", "governance")
        self.governance_sha = git(self.governance, "rev-parse", "HEAD")
        self.deployment = self.base / MATERIALIZER.DEPLOYMENT_ROOT_BASENAME
        git(self.governance, "worktree", "add", "-q", "--detach", str(self.deployment), self.deployment_sha)
        self.source = self.deployment / MATERIALIZER.ARCHIVE_RELATIVE_PATH
        self.destination = self.governance / MATERIALIZER.ARCHIVE_RELATIVE_PATH
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.members = (("classification.v1.json", b"classification\n"), ("handler.py", b"handler\n"))
        self.write_archive(self.source, self.members)
        self.archive_sha = MATERIALIZER.sha256(self.source)
        self.member_contract = tuple((name, hashlib.sha256(value).hexdigest()) for name, value in self.members)
        self.governance_receipt = self.base / "governance.json"
        self.plan_receipt = self.base / "plan.json"
        self.receipt = self.base / "materialization.json"

    @staticmethod
    def write_archive(path: Path, members: tuple[tuple[str, bytes], ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)

    def write_receipts(self) -> None:
        own = "scripts/materialize-candidate-preflight-archive.py"
        self.governance_receipt.write_text(
            json.dumps(
                {
                    "schema": MATERIALIZER.GOVERNANCE_SCHEMA,
                    "governanceSha": self.governance_sha,
                    "deploymentSha": self.deployment_sha,
                    "sourceSha256": {own: MATERIALIZER.lf_sha256(SCRIPT)},
                    "operatorContract": {
                        "archiveMaterialization": {
                            "archiveRelativePath": MATERIALIZER.ARCHIVE_RELATIVE_PATH.as_posix(),
                            "archiveSha256": self.archive_sha,
                            "deploymentRootBasename": MATERIALIZER.DEPLOYMENT_ROOT_BASENAME,
                            "memberSha256": dict(self.member_contract),
                            "orderedMembers": [name for name, _ in self.member_contract],
                            "repositoryOrigin": MATERIALIZER.REPOSITORY_ORIGIN,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.plan_receipt.write_text(
            json.dumps(
                {
                    "schema": MATERIALIZER.PLAN_SCHEMA,
                    "mergedSha": self.deployment_sha,
                    "artifacts": {"archiveSha256": self.archive_sha},
                    "sourceSha256": dict(self.member_contract),
                }
            ),
            encoding="utf-8",
        )

    @contextlib.contextmanager
    def patched(self):
        originals = (
            MATERIALIZER.ROOT,
            MATERIALIZER.DEPLOYMENT_SHA,
            MATERIALIZER.ARCHIVE_SHA256,
            MATERIALIZER.MEMBERS,
        )
        MATERIALIZER.ROOT = self.governance
        MATERIALIZER.DEPLOYMENT_SHA = self.deployment_sha
        MATERIALIZER.ARCHIVE_SHA256 = self.archive_sha
        MATERIALIZER.MEMBERS = self.member_contract
        self.write_receipts()
        try:
            yield
        finally:
            MATERIALIZER.ROOT, MATERIALIZER.DEPLOYMENT_SHA, MATERIALIZER.ARCHIVE_SHA256, MATERIALIZER.MEMBERS = originals

    def run(self, deployment_root: str | None = None):
        return MATERIALIZER.materialize(
            self.governance_sha,
            self.deployment_sha,
            deployment_root or str(self.deployment),
            self.governance_receipt,
            self.plan_receipt,
            self.receipt,
        )


class ArchiveMaterializationTests(unittest.TestCase):
    def test_actual_materialization_is_byte_identical_and_keeps_checkout_clean(self):
        with tempfile.TemporaryDirectory(prefix="candidate-archive-") as temporary:
            fixture = Fixture(temporary)
            with fixture.patched():
                receipt = fixture.run()
                self.assertEqual(fixture.source.read_bytes(), fixture.destination.read_bytes())
                self.assertTrue(receipt["byteIdentical"])
                self.assertEqual("", MATERIALIZER.git_text(fixture.governance, "status", "--porcelain=v1"))
                fixture.run()
                self.assertEqual("", MATERIALIZER.git_text(fixture.governance, "status", "--porcelain=v1"))

    def test_missing_wrong_head_dirty_wrong_hash_and_existing_destination_fail(self):
        cases = ("missing", "head", "dirty", "hash", "destination")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="candidate-archive-") as temporary:
                fixture = Fixture(temporary)
                with fixture.patched():
                    if case == "missing":
                        fixture.source.unlink()
                    elif case == "head":
                        subprocess.run(["git", "-C", str(fixture.deployment), "checkout", "-q", fixture.governance_sha], check=True)
                    elif case == "dirty":
                        (fixture.deployment / "dirty.txt").write_text("dirty", encoding="utf-8")
                    elif case == "hash":
                        fixture.source.write_bytes(fixture.source.read_bytes() + b"drift")
                    else:
                        fixture.destination.parent.mkdir(parents=True, exist_ok=True)
                        fixture.destination.write_bytes(b"wrong")
                    with self.assertRaises(RuntimeError):
                        fixture.run()

    def test_governance_head_receipts_origin_common_repo_and_ignore_are_required(self):
        cases = ("governance-head", "governance-receipt", "plan-receipt", "origin", "common-repo", "ignore")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="candidate-archive-") as temporary:
                fixture = Fixture(temporary, ignored=case != "ignore")
                with fixture.patched():
                    if case == "governance-head":
                        fixture.governance_sha = "0" * 40
                    elif case == "governance-receipt":
                        document = json.loads(fixture.governance_receipt.read_text(encoding="utf-8"))
                        document["operatorContract"]["archiveMaterialization"]["archiveSha256"] = "0" * 64
                        fixture.governance_receipt.write_text(json.dumps(document), encoding="utf-8")
                    elif case == "plan-receipt":
                        document = json.loads(fixture.plan_receipt.read_text(encoding="utf-8"))
                        document["sourceSha256"]["handler.py"] = "0" * 64
                        fixture.plan_receipt.write_text(json.dumps(document), encoding="utf-8")
                    elif case == "origin":
                        subprocess.run(["git", "-C", str(fixture.deployment), "remote", "set-url", "origin", "https://example.invalid/wrong.git"], check=True)
                    elif case == "common-repo":
                        other = fixture.base / MATERIALIZER.DEPLOYMENT_ROOT_BASENAME
                        subprocess.run(["git", "-C", str(fixture.governance), "worktree", "remove", "--force", str(fixture.deployment)], check=True)
                        other.mkdir()
                        subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
                        subprocess.run(["git", "-C", str(other), "remote", "add", "origin", MATERIALIZER.REPOSITORY_ORIGIN], check=True)
                        fixture.deployment = other
                    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                        fixture.run()

    def test_member_hash_order_and_wsl_paths_fail(self):
        with tempfile.TemporaryDirectory(prefix="candidate-archive-") as temporary:
            fixture = Fixture(temporary)
            wrong_member = (("classification.v1.json", b"wrong\n"), fixture.members[1])
            fixture.write_archive(fixture.source, wrong_member)
            changed_hash = MATERIALIZER.sha256(fixture.source)
            with self.assertRaises(RuntimeError):
                MATERIALIZER.validate_archive(fixture.source, changed_hash, fixture.member_contract)
            fixture.write_archive(fixture.source, tuple(reversed(fixture.members)))
            reversed_hash = MATERIALIZER.sha256(fixture.source)
            with self.assertRaises(RuntimeError):
                MATERIALIZER.validate_archive(fixture.source, reversed_hash, fixture.member_contract)
            for path in ("/mnt/c/repo/candidate-preflight-plan-3a00dfd3", "\\\\wsl$\\Ubuntu\\repo", "//wsl.localhost/Ubuntu/repo"):
                with self.subTest(path=path), self.assertRaises(RuntimeError):
                    MATERIALIZER.reject_wsl_path(path)


if __name__ == "__main__":
    unittest.main()
