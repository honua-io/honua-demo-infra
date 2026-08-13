#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
