#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "assert-candidate-preflight-v3-invocation.py"
spec = importlib.util.spec_from_file_location("v3_invocation_test", PATH)
MODULE = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(MODULE)


def payload():
    return {
        "schemaVersion": "honua-candidate-preflight-result-v1", "operation": "candidate-preflight-v1", "status": "passed",
        "candidate": {"functionName": "honua-demo-demo-honua", "version": "40", "revisionId": MODULE.RUNTIME.APP_REVISION_ID,
            "imageDigest": MODULE.RUNTIME.IMAGE_DIGEST, "artifactReference": f"{MODULE.RUNTIME.ACCOUNT}.dkr.ecr.{MODULE.RUNTIME.REGION}.amazonaws.com/honua-server@{MODULE.RUNTIME.IMAGE_DIGEST}",
            "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad", "provenance": "classification-manifest+resolved-image"},
        "liveAlias": {"name": "live", "version": "39", "revisionId": MODULE.RUNTIME.LIVE_REVISION_ID},
        "migration": {"phase": "Expand", "pendingScriptCount": 14, "pendingScriptsSha256": MODULE.PENDING_DIGEST},
        "checks": MODULE.CHECKS,
    }


class InvocationTests(unittest.TestCase):
    def test_exact_success_passes(self):
        MODULE.assert_invocation({"StatusCode": 200, "ExecutedVersion": "3"}, payload())
        self.assertEqual("e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7", MODULE.PENDING_DIGEST)

    def test_transport_failure_versions_contract_and_digest_fail(self):
        cases = {
            "helper :1": ({"StatusCode": 200, "ExecutedVersion": "1"}, lambda p: None),
            "helper :2": ({"StatusCode": 200, "ExecutedVersion": "2"}, lambda p: None),
            "helper :4": ({"StatusCode": 200, "ExecutedVersion": "4"}, lambda p: None),
            "function error": ({"StatusCode": 200, "ExecutedVersion": "3", "FunctionError": "Unhandled"}, lambda p: None),
            "failed result": ({"StatusCode": 200, "ExecutedVersion": "3"}, lambda p: p.update(status="failed", failure="internal-failure")),
            "contract": ({"StatusCode": 200, "ExecutedVersion": "3"}, lambda p: p["migration"].update(phase="Contract")),
            "pending digest": ({"StatusCode": 200, "ExecutedVersion": "3"}, lambda p: p["migration"].update(pendingScriptsSha256="0" * 64)),
            "checks": ({"StatusCode": 200, "ExecutedVersion": "3"}, lambda p: p.update(checks=list(reversed(p["checks"])))),
        }
        for label, (metadata, mutate) in cases.items():
            value = copy.deepcopy(payload()); mutate(value)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                MODULE.assert_invocation(metadata, value)


if __name__ == "__main__":
    unittest.main()
