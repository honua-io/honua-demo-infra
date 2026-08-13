#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "candidate-preflight-governance-receipt.py"


def load_module():
    spec = importlib.util.spec_from_file_location("candidate_governance_receipt", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load governance receipt")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECEIPT = load_module()
GOVERNANCE_SHA = "a" * 40


def valid_receipt() -> dict:
    return {
        "schema": RECEIPT.SCHEMA,
        "governanceSha": GOVERNANCE_SHA,
        "deploymentSha": RECEIPT.DEPLOYMENT_SHA,
        "historicalDeploymentReceiptSha256": RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT_SHA256,
        "sourceSha256": RECEIPT.source_hashes(),
        "operatorContract": RECEIPT.OPERATOR_CONTRACT,
    }


class CandidatePreflightGovernanceReceiptTests(unittest.TestCase):
    def test_exact_governance_deployment_binding_passes(self):
        RECEIPT.validate_receipt(valid_receipt(), GOVERNANCE_SHA, RECEIPT.DEPLOYMENT_SHA)

    def test_every_cross_binding_and_control_mutation_fails(self):
        mutations = {
            "extra": lambda r: r.update(extra=True),
            "schema": lambda r: r.update(schema="wrong"),
            "governance": lambda r: r.update(governanceSha="b" * 40),
            "deployment": lambda r: r.update(deploymentSha="b" * 40),
            "historical deployment": lambda r: r.update(historicalDeploymentReceiptSha256="0" * 64),
            "source extra": lambda r: r["sourceSha256"].update(extra="0" * 64),
            "source hash": lambda r: r["sourceSha256"].update(**{RECEIPT.CONTROL_PATHS[0]: "0" * 64}),
            "retries": lambda r: r["operatorContract"].update(awsMaxAttempts=2),
            "pagination": lambda r: r["operatorContract"].update(iamTerminalPagination=False),
            "unqualified": lambda r: r["operatorContract"].update(qualifiedHelperOnly=False),
            "logs": lambda r: r["operatorContract"].update(invokeLogType="Tail"),
            "payload": lambda r: r["operatorContract"].update(payloadSha256="0" * 64),
        }
        for label, mutation in mutations.items():
            receipt = copy.deepcopy(valid_receipt())
            mutation(receipt)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                RECEIPT.validate_receipt(receipt, GOVERNANCE_SHA, RECEIPT.DEPLOYMENT_SHA)

    def test_arbitrary_or_same_deployment_binding_fails(self):
        with self.assertRaises(RuntimeError):
            RECEIPT.validate_receipt(valid_receipt(), GOVERNANCE_SHA, "b" * 40)
        same = copy.deepcopy(valid_receipt())
        same["governanceSha"] = RECEIPT.DEPLOYMENT_SHA
        with self.assertRaises(RuntimeError):
            RECEIPT.validate_receipt(same, RECEIPT.DEPLOYMENT_SHA, RECEIPT.DEPLOYMENT_SHA)

    def test_historical_receipt_exact_hash_and_pins_are_required(self):
        with tempfile.TemporaryDirectory(prefix="candidate-historical-receipt-") as temporary:
            path = Path(temporary) / "deployment-receipt.json"
            source = Path.home() / ".honua-runtime-proof" / "candidate-preflight-3a00dfd36c298def8f8f49757dd56595d29097cb-plan" / "postapply-deployment-receipt.json"
            if not source.is_file():
                self.skipTest("sealed historical receipt is intentionally local-only")
            shutil.copyfile(source, path)
            RECEIPT.validate_historical_deployment_receipt(path)
            for key, value in (("version", "2"), ("revisionId", "wrong"), ("qualifiedArn", RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT["qualifiedArn"].rsplit(":", 1)[0] + ":2")):
                changed = dict(RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT)
                changed[key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    RECEIPT.validate_historical_deployment_receipt(path)

    def test_real_git_binding_rejects_wrong_head_dirty_nonancestor_drift_and_second_invoke(self):
        with tempfile.TemporaryDirectory(prefix="candidate-governance-git-") as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()

            def git(*arguments: str) -> str:
                return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()

            git("init", "-q")
            git("config", "user.name", "Candidate Governance Test")
            git("config", "user.email", "candidate-governance@example.invalid")
            git("config", "gc.auto", "0")
            git("config", "gc.autoDetach", "false")
            git("config", "maintenance.auto", "false")
            for relative in RECEIPT.CONTROL_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative.endswith("candidate-preflight-invoke.sh"):
                    path.write_text(
                        "AWS_MAX_ATTEMPTS=1\naws lambda invoke \\\n+  --log-type None \\\n+  --payload '{\"operation\":\"candidate-preflight-v1\"}'\n",
                        encoding="utf-8",
                    )
                else:
                    path.write_text(relative + "\n", encoding="utf-8")
            for relative in RECEIPT.DEPLOYMENT_PATHS:
                path = root / relative / "fixture.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("immutable deployment\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "deployment")
            deployment = git("rev-parse", "HEAD")
            (root / "runbook/candidate-preflight-v1.md").write_text("governed\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "governance")
            governance = git("rev-parse", "HEAD")
            historical = Path(temporary) / "historical.json"
            historical_document = {"fixture": "exact"}
            historical.write_text(json.dumps(historical_document), encoding="utf-8")

            originals = (
                RECEIPT.ROOT,
                RECEIPT.DEPLOYMENT_SHA,
                RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT,
                RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT_SHA256,
            )
            try:
                RECEIPT.ROOT = root
                RECEIPT.DEPLOYMENT_SHA = deployment
                RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT = historical_document
                RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT_SHA256 = RECEIPT.lf_sha256(historical)
                receipt = RECEIPT.build_receipt(governance, deployment, historical)
                self.assertEqual(governance, receipt["governanceSha"])

                with self.assertRaises(RuntimeError):
                    RECEIPT.build_receipt("0" * 40, deployment, historical)

                dirty = root / "runbook/candidate-preflight-v1.md"
                dirty.write_text("dirty\n", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    RECEIPT.build_receipt(governance, deployment, historical)
                git("checkout", "--", "runbook/candidate-preflight-v1.md")

                tree = git("write-tree")
                unrelated = subprocess.check_output(
                    ["git", "commit-tree", tree, "-m", "unrelated"],
                    cwd=root,
                    text=True,
                ).strip()
                RECEIPT.DEPLOYMENT_SHA = unrelated
                with self.assertRaises(RuntimeError):
                    RECEIPT.build_receipt(governance, unrelated, historical)
                RECEIPT.DEPLOYMENT_SHA = deployment

                deployment_file = root / RECEIPT.DEPLOYMENT_PATHS[0] / "fixture.txt"
                deployment_file.write_text("drifted deployment\n", encoding="utf-8")
                git("add", ".")
                git("commit", "-q", "-m", "deployment drift")
                drift_head = git("rev-parse", "HEAD")
                with self.assertRaises(RuntimeError):
                    RECEIPT.build_receipt(drift_head, deployment, historical)

                git("checkout", "-q", governance)
                operator = root / "scripts/candidate-preflight-invoke.sh"
                operator.write_text(operator.read_text(encoding="utf-8") + "aws lambda invoke \\\n+  --function-name second\n", encoding="utf-8")
                git("add", ".")
                git("commit", "-q", "-m", "second invoke")
                second_head = git("rev-parse", "HEAD")
                with self.assertRaises(RuntimeError):
                    RECEIPT.build_receipt(second_head, deployment, historical)
                self.assertEqual([], list((root / ".git").rglob("*.lock")))
                subprocess.run(
                    ["git", "maintenance", "run", "--auto"],
                    cwd=root,
                    check=True,
                    timeout=10,
                )
                time.sleep(0.05)
                self.assertEqual([], list((root / ".git").rglob("*.lock")))
            finally:
                (
                    RECEIPT.ROOT,
                    RECEIPT.DEPLOYMENT_SHA,
                    RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT,
                    RECEIPT.HISTORICAL_DEPLOYMENT_RECEIPT_SHA256,
                ) = originals


if __name__ == "__main__":
    unittest.main()
