#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "assert-candidate-preflight-v2-runtime.py"
spec = importlib.util.spec_from_file_location("v2_runtime_test", PATH)
MODULE = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(MODULE)


def receipt():
    return {
        "schema": MODULE.SCHEMA, "governanceSha": "a" * 40, "deploymentSha": MODULE.GOVERNANCE.DEPLOYMENT_SHA,
        "applyEvidenceManifestSha256": MODULE.GOVERNANCE.APPLY_EVIDENCE_MANIFEST_SHA256,
        "governanceReceiptSha256": "1" * 64, "ecrEvidenceSha256": "2" * 64,
        "stateLineage": MODULE.GOVERNANCE.STATE_LINEAGE, "stateSerial": 3,
        "qualifiedArn": MODULE.QUALIFIED_ARN, "version": "2", "revisionId": MODULE.REVISION_ID,
        "codeSha256": MODULE.CODE_SHA256, "codeSize": MODULE.CODE_SIZE, "roleArn": MODULE.ROLE_ARN,
        "policyName": MODULE.POLICY_NAME, "secretArn": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd",
        "candidateVersion": "40", "candidateRevisionId": MODULE.APP_REVISION_ID, "candidateImageDigest": MODULE.IMAGE_DIGEST,
        "liveVersion": "39", "liveRevisionId": MODULE.LIVE_REVISION_ID,
    }


class RuntimeTests(unittest.TestCase):
    def test_exact_v2_receipt_passes(self):
        MODULE.validate_receipt(receipt(), "a" * 40, "1" * 64)

    def test_wrong_version_revision_hash_apply_and_app_pins_fail(self):
        mutations = {
            "helper :1": lambda r: r.update(version="1", qualifiedArn=MODULE.QUALIFIED_ARN[:-1] + "1"),
            "helper :3": lambda r: r.update(version="3", qualifiedArn=MODULE.QUALIFIED_ARN[:-1] + "3"),
            "revision": lambda r: r.update(revisionId="00000000-0000-0000-0000-000000000000"),
            "code hash": lambda r: r.update(codeSha256="wrong"),
            "code size": lambda r: r.update(codeSize=5731),
            "apply receipt": lambda r: r.update(applyEvidenceManifestSha256="0" * 64),
            "state serial": lambda r: r.update(stateSerial=4),
            "candidate": lambda r: r.update(candidateVersion="39"),
            "live": lambda r: r.update(liveVersion="40"),
            "ecr": lambda r: r.update(candidateImageDigest="sha256:" + "0" * 64),
        }
        for label, mutate in mutations.items():
            value = copy.deepcopy(receipt()); mutate(value)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                MODULE.validate_receipt(value, "a" * 40, "1" * 64)


if __name__ == "__main__":
    unittest.main()
