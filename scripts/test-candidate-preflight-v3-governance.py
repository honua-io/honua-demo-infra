#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "candidate-preflight-v3-governance-receipt.py"
spec = importlib.util.spec_from_file_location("v3_governance_test", PATH)
MODULE = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(MODULE)


def manifest():
    return {
        "schema": "honua-candidate-preflight-helper-v3-final-evidence.v1",
        "mergeSha": MODULE.DEPLOYMENT_SHA,
        "deployment": {"planAttempts": 1, "applyAttempts": 1, "applyExitCode": 0, "create": 0, "update": 1, "delete": 0},
        "state": {"lineage": MODULE.STATE_LINEAGE, "beforeSerial": 3, "afterSerial": 4},
        "helperV3": {"version": "3", "revisionId": MODULE.HELPER_REVISION_ID, "codeSha256": MODULE.HELPER_CODE_SHA256, "codeSize": 5715, "activeSuccessful": True},
        "helperV2Immutable": True, "app40Unchanged": True, "live39Unchanged": True, "iamExact": True,
        "helperInvoked": False,
        "secretValueRead": False, "databaseAccessed": False, "aliasMutated": False,
    }


class GovernanceTests(unittest.TestCase):
    def write(self, root, value):
        evidence = root / "final-evidence.json"
        evidence.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path = root / "final-artifacts.sha256"
        path.write_text(f"{hashlib.sha256(evidence.read_bytes()).hexdigest()}  final-evidence.json\n", encoding="utf-8")
        return path

    def test_exact_apply_receipt_and_source_binding_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), manifest())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            evidence = Path(temporary) / ".honua-runtime-proof" / ("candidate-preflight-v3-invocation-" + "a" * 40)
            with mock.patch.object(MODULE, "APPLY_EVIDENCE_MANIFEST_SHA256", digest), mock.patch.object(MODULE, "require_clean_binding"), mock.patch.object(MODULE, "canonical_evidence_dir", return_value=evidence.resolve()):
                receipt = MODULE.build_receipt("a" * 40, path, evidence)
                self.assertEqual(digest, receipt["applyEvidenceManifestSha256"])
                self.assertEqual(4, receipt["stateSerial"])

    def test_alternate_evidence_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write(root, manifest())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            canonical = root / ".honua-runtime-proof" / ("candidate-preflight-v3-invocation-" + "a" * 40)
            with mock.patch.object(MODULE, "APPLY_EVIDENCE_MANIFEST_SHA256", digest), mock.patch.object(MODULE, "require_clean_binding"), mock.patch.object(MODULE, "canonical_evidence_dir", return_value=canonical.resolve()), self.assertRaises(RuntimeError):
                MODULE.build_receipt("a" * 40, path, root / "alternate")

    def test_hostile_apply_receipt_variants_fail_closed(self):
        mutations = {
            "serial": lambda m: m["state"].update(afterSerial=5),
            "lineage": lambda m: m["state"].update(lineage="wrong"),
            "helper revision evidence": lambda m: m["helperV3"].update(revisionId="wrong"),
            "helper size": lambda m: m["helperV3"].update(codeSize=5731),
            "second apply": lambda m: m["deployment"].update(applyAttempts=2),
            "already invoked": lambda m: m.update(helperInvoked=True),
            "candidate": lambda m: m.update(app40Unchanged=False),
            "live": lambda m: m.update(live39Unchanged=False),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, mutate in mutations.items():
                value = copy.deepcopy(manifest()); mutate(value)
                path = self.write(root, value)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.subTest(label=label), mock.patch.object(MODULE, "APPLY_EVIDENCE_MANIFEST_SHA256", digest), self.assertRaises(RuntimeError):
                    MODULE.validate_apply_manifest(path)

    def test_wrong_manifest_hash_and_operator_ambiguity_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), manifest())
            with self.assertRaises(RuntimeError):
                MODULE.validate_apply_manifest(path)
        MODULE.validate_operator_source()


if __name__ == "__main__":
    unittest.main()
