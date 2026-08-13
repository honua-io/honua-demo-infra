#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "candidate-preflight-plan-receipt.py"


def load_module():
    spec = importlib.util.spec_from_file_location("plan_receipt", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load plan receipt validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECEIPT = load_module()
MERGED_SHA = "a" * 40


def valid_receipt():
    return {
        "schema": RECEIPT.SCHEMA,
        "mergedSha": MERGED_SHA,
        "terraformVersion": RECEIPT.TERRAFORM_VERSION,
        "planFormatVersion": RECEIPT.PLAN_FORMAT_VERSION,
        "artifacts": {
            "savedPlanSha256": "1" * 64,
            "showJsonSha256": "2" * 64,
            "archiveSha256": RECEIPT.ARCHIVE_SHA256,
        },
        "sourceSha256": RECEIPT.SOURCE_HASHES,
    }


def historical_receipt():
    value = valid_receipt()
    value["schema"] = RECEIPT.HISTORICAL_SCHEMA
    value["mergedSha"] = RECEIPT.DEPLOYMENT_SHA
    value["artifacts"]["archiveSha256"] = RECEIPT.HISTORICAL_ARCHIVE_SHA256
    value["sourceSha256"] = RECEIPT.HISTORICAL_SOURCE_HASHES
    return value


class CandidatePreflightPlanReceiptTests(unittest.TestCase):
    def test_exact_receipt_passes(self):
        RECEIPT.validate_receipt(valid_receipt(), MERGED_SHA)
        RECEIPT.validate_receipt(historical_receipt(), RECEIPT.DEPLOYMENT_SHA)

    def test_cross_checkout_is_only_allowed_for_exact_deployment(self):
        RECEIPT.validate_checkout_binding(RECEIPT.DEPLOYMENT_SHA, "b" * 40)
        RECEIPT.validate_checkout_binding(MERGED_SHA, MERGED_SHA)
        with self.assertRaises(RuntimeError):
            RECEIPT.validate_checkout_binding(MERGED_SHA, "b" * 40)

    def test_schema_hash_and_keyset_mutations_fail_closed(self):
        mutations = {
            "root extra": lambda r: r.update(extra=True),
            "schema": lambda r: r.update(schema="wrong"),
            "merged SHA": lambda r: r.update(mergedSha="b" * 40),
            "Terraform version": lambda r: r.update(terraformVersion="1.15.9"),
            "plan format": lambda r: r.update(planFormatVersion="1.3"),
            "artifact extra": lambda r: r["artifacts"].update(extra="0" * 64),
            "saved plan hash": lambda r: r["artifacts"].update(savedPlanSha256="short"),
            "show hash": lambda r: r["artifacts"].update(showJsonSha256="G" * 64),
            "ZIP hash": lambda r: r["artifacts"].update(archiveSha256="0" * 64),
            "source extra": lambda r: r["sourceSha256"].update(**{"sensitive-output": "0" * 64}),
            "handler hash": lambda r: r["sourceSha256"].update(**{"handler.py": "0" * 64}),
            "classification hash": lambda r: r["sourceSha256"].update(**{"classification.v1.json": "0" * 64}),
        }
        for label, mutation in mutations.items():
            receipt = copy.deepcopy(valid_receipt())
            mutation(receipt)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                RECEIPT.validate_receipt(receipt, MERGED_SHA)

        historical = historical_receipt()
        historical["artifacts"]["archiveSha256"] = RECEIPT.ARCHIVE_SHA256
        with self.assertRaises(RuntimeError):
            RECEIPT.validate_receipt(historical, RECEIPT.DEPLOYMENT_SHA)


if __name__ == "__main__":
    unittest.main()
