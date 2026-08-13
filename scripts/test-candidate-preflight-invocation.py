#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-candidate-preflight-invocation.py"
HANDLER_TEST_SCRIPT = ROOT / "scripts" / "test-candidate-preflight-handler.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load invocation assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSERTION = load_module("invocation_assertion", SCRIPT)
HANDLER_TEST = load_module("handler_test", HANDLER_TEST_SCRIPT)


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
            "artifactReference": "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
            "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
            "provenance": "classification-manifest+resolved-image",
        },
        "liveAlias": {"name": "live", "version": "39", "revisionId": "4f73dd76-0294-44d3-8362-c6f8606f034e"},
        "migration": {"phase": "Expand", "pendingScriptCount": 14, "pendingScriptsSha256": ASSERTION.PENDING_DIGEST},
        "checks": ASSERTION.CHECKS,
    }


class InvocationAssertionTests(unittest.TestCase):
    def test_exact_invocation_passes(self):
        ASSERTION.assert_invocation({"StatusCode": 200, "ExecutedVersion": "1"}, valid_payload(), "1")
        self.assertEqual(
            "e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7",
            ASSERTION.PENDING_DIGEST,
        )

    def test_actual_handler_success_result_passes_invocation_assertion(self):
        case = HANDLER_TEST.CandidatePreflightHandlerTests(methodName="test_success_is_sanitized_and_invokes_only_qualified_candidate")
        case.setUp()
        try:
            payload, *_ = case.execute()
        finally:
            case.doCleanups()
        ASSERTION.assert_invocation({"StatusCode": 200, "ExecutedVersion": "1"}, payload, "1")

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
            "pending order": lambda m, p: p["migration"].update(
                pendingScriptsSha256=hashlib.sha256(
                    "\n".join(reversed(ASSERTION.PENDING_NAMES)).encode("utf-8")
                ).hexdigest()
            ),
            "checks extra": lambda m, p: p["checks"].append("extra"),
            "checks order": lambda m, p: p.update(checks=list(reversed(p["checks"]))),
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

    def test_invocation_receipt_binds_deployment_and_ecr_proof(self):
        with tempfile.TemporaryDirectory(prefix="candidate-invocation-receipt-") as temporary:
            root = Path(temporary)
            metadata = root / "metadata.json"
            payload = root / "payload.json"
            evidence = root / "evidence.json"
            deployment = root / "deployment.json"
            metadata.write_text(json.dumps({"StatusCode": 200, "ExecutedVersion": "1"}), encoding="utf-8")
            payload.write_text(json.dumps(valid_payload()), encoding="utf-8")
            evidence.write_text(json.dumps({"schema": "honua-candidate-preflight-ecr-evidence-v1"}), encoding="utf-8")
            deployment.write_text(
                json.dumps(
                    {
                        "schema": ASSERTION.DEPLOYMENT_RECEIPT_SCHEMA,
                        "governanceSha": "a" * 40,
                        "deploymentSha": ASSERTION.DEPLOYMENT_SHA,
                        "governanceReceiptSha256": "3" * 64,
                        "planReceiptSha256": "1" * 64,
                        "ecrEvidenceSha256": ASSERTION.sha256(evidence),
                        "qualifiedArn": "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:1",
                        "version": "1",
                        "revisionId": "23959775-30a4-4654-a3dd-1e430915e1b1",
                        "codeSha256": "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA=",
                        "roleArn": "arn:aws:iam::585192672263:role/honua-demo-demo-candidate-preflight-role",
                        "policyName": "credential-safe-candidate-preflight-v1",
                        "secretArn": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd",
                    }
                ),
                encoding="utf-8",
            )
            receipt = ASSERTION.build_receipt(metadata, payload, deployment, evidence, "1")
            ASSERTION.validate_receipt(receipt)
            evidence.write_text(json.dumps({"schema": "honua-candidate-preflight-ecr-evidence-v1", "drift": True}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                ASSERTION.build_receipt(metadata, payload, deployment, evidence, "1")

    def test_invocation_receipt_rejects_cross_binding(self):
        with tempfile.TemporaryDirectory(prefix="candidate-invocation-binding-") as temporary:
            root = Path(temporary)
            metadata = root / "metadata.json"
            payload = root / "payload.json"
            evidence = root / "evidence.json"
            deployment = root / "deployment.json"
            metadata.write_text(json.dumps({"StatusCode": 200, "ExecutedVersion": "1"}), encoding="utf-8")
            payload.write_text(json.dumps(valid_payload()), encoding="utf-8")
            evidence.write_text(json.dumps({"schema": "honua-candidate-preflight-ecr-evidence-v1"}), encoding="utf-8")
            base = {
                "schema": ASSERTION.DEPLOYMENT_RECEIPT_SCHEMA,
                "governanceSha": "a" * 40,
                "deploymentSha": ASSERTION.DEPLOYMENT_SHA,
                "governanceReceiptSha256": "3" * 64,
                "planReceiptSha256": "1" * 64,
                "ecrEvidenceSha256": ASSERTION.sha256(evidence),
                "qualifiedArn": "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:1",
                "version": "1",
                "revisionId": "23959775-30a4-4654-a3dd-1e430915e1b1",
                "codeSha256": "TuvBWGYwUcJwz5ibvThVgeK3UkWw3dD3b7AakOfJnaA=",
                "roleArn": "arn:aws:iam::585192672263:role/honua-demo-demo-candidate-preflight-role",
                "policyName": "credential-safe-candidate-preflight-v1",
                "secretArn": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd",
            }
            mutations = {
                "wrong deployment": lambda r: r.update(deploymentSha="b" * 40),
                "same provenance": lambda r: r.update(governanceSha=ASSERTION.DEPLOYMENT_SHA),
                "wrong governance hash": lambda r: r.update(governanceReceiptSha256="bad"),
            }
            for label, mutation in mutations.items():
                value = copy.deepcopy(base)
                mutation(value)
                deployment.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(label=label), self.assertRaises(RuntimeError):
                    ASSERTION.build_receipt(metadata, payload, deployment, evidence, "1")


if __name__ == "__main__":
    unittest.main()
