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
PATH = ROOT / "scripts" / "candidate-preflight-v2-governance-receipt.py"
spec = importlib.util.spec_from_file_location("v2_governance_test", PATH)
MODULE = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(MODULE)


def manifest():
    return {
        "schema": "honua-candidate-preflight-helper-v2-final-evidence.v1",
        "mergeSha": MODULE.DEPLOYMENT_SHA,
        "applyExitCode": 0,
        "applyAttempts": 1,
        "postapplyAssertionPassed": True,
        "stateLineage": MODULE.STATE_LINEAGE,
        "stateSerial": 3,
        "helperV2": {"version": "2", "codeSha256": MODULE.HELPER_CODE_SHA256, "codeSize": 5732},
        "helperV1": {"version": "1", "codeSha256": "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA=", "codeSize": 5646},
        "appVersion": "40", "liveVersion": "39", "helperInvoked": False,
        "secretValueRead": False, "databaseAccessed": False, "aliasMutated": False,
    }


class GovernanceTests(unittest.TestCase):
    def write(self, root, value):
        path = root / "final-evidence-manifest.json"
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def test_exact_apply_receipt_and_source_binding_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), manifest())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(MODULE, "APPLY_EVIDENCE_MANIFEST_SHA256", digest), mock.patch.object(MODULE, "require_clean_binding"):
                receipt = MODULE.build_receipt("a" * 40, path)
                self.assertEqual(digest, receipt["applyEvidenceManifestSha256"])
                self.assertEqual(3, receipt["stateSerial"])

    def test_hostile_apply_receipt_variants_fail_closed(self):
        mutations = {
            "serial": lambda m: m.update(stateSerial=4),
            "lineage": lambda m: m.update(stateLineage="wrong"),
            "helper revision evidence": lambda m: m["helperV2"].update(codeSha256="wrong"),
            "helper size": lambda m: m["helperV2"].update(codeSize=5731),
            "second apply": lambda m: m.update(applyAttempts=2),
            "already invoked": lambda m: m.update(helperInvoked=True),
            "candidate": lambda m: m.update(appVersion="39"),
            "live": lambda m: m.update(liveVersion="40"),
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
