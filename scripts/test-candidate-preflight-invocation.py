#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-candidate-preflight-invocation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("invocation_assertion", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load invocation assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSERTION = load_module()


def valid_payload():
    return {
        "schemaVersion": ASSERTION.SCHEMA,
        "operation": ASSERTION.OPERATION,
        "status": "passed",
        "candidate": {
            "functionName": "honua-demo-demo-honua",
            "version": "40",
            "revisionId": "0326e209-4231-4acd-9bb4-d3cb89402db0",
            "imageDigest": "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
            "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
        },
        "liveAlias": {"name": "live", "version": "39", "revisionId": "4f73dd76-0294-44d3-8362-c6f8606f034e"},
        "migration": {"phase": "Expand", "pendingScriptCount": 14, "pendingScriptsSha256": ASSERTION.PENDING_DIGEST},
        "checks": ASSERTION.CHECKS,
    }


class InvocationAssertionTests(unittest.TestCase):
    def test_exact_invocation_passes(self):
        ASSERTION.assert_invocation({"StatusCode": 200, "ExecutedVersion": "1"}, valid_payload(), "1")

    def test_every_transport_and_semantic_failure_is_rejected(self):
        mutations = {
            "status code": lambda m, p: m.update(StatusCode=202),
            "function error": lambda m, p: m.update(FunctionError="Unhandled"),
            "wrong version": lambda m, p: m.update(ExecutedVersion="2"),
            "failed payload": lambda m, p: p.update(status="failed", failure="internal-failure"),
            "wrong operation": lambda m, p: p.update(operation="other"),
            "candidate": lambda m, p: p["candidate"].update(sourceCommit="wrong"),
            "live alias": lambda m, p: p["liveAlias"].update(version="40"),
            "phase": lambda m, p: p["migration"].update(phase="Contract"),
            "count": lambda m, p: p["migration"].update(pendingScriptCount=13),
            "digest": lambda m, p: p["migration"].update(pendingScriptsSha256="wrong"),
            "checks": lambda m, p: p["checks"].append("extra"),
            "extra payload": lambda m, p: p.update(extra=True),
        }
        for label, mutation in mutations.items():
            metadata = {"StatusCode": 200, "ExecutedVersion": "1"}
            payload = copy.deepcopy(valid_payload())
            mutation(metadata, payload)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                ASSERTION.assert_invocation(metadata, payload, "1")

    def test_missing_or_malformed_documents_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="candidate-invocation-") as temporary:
            root = Path(temporary)
            malformed = root / "malformed.json"
            malformed.write_text('{"StatusCode":', encoding="utf-8")
            for path in (malformed, root / "missing.json"):
                with self.subTest(path=path.name), self.assertRaises(RuntimeError):
                    ASSERTION.load_document(path)

    def test_non_object_documents_and_mutable_version_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="candidate-invocation-") as temporary:
            document = Path(temporary) / "array.json"
            document.write_text("[]", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                ASSERTION.load_document(document)
        with self.assertRaises(RuntimeError):
            ASSERTION.assert_invocation({"StatusCode": 200, "ExecutedVersion": "$LATEST"}, valid_payload(), "$LATEST")


if __name__ == "__main__":
    unittest.main()
