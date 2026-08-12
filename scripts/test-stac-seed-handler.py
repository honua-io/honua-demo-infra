#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "stacks" / "aws" / "postgis-bootstrap" / "handler.py"


class FakeConnection:
    def __init__(self, receipt_rows=None):
        self.calls = []
        self.receipt_rows = receipt_rows
        self.closed = False

    def run(self, statement, **parameters):
        self.calls.append((statement, parameters))
        if "SELECT revision FROM honua.metadata_v2_current" in statement:
            return [[7]]
        if "SELECT marker.seed_id" in statement:
            return self.receipt_rows
        return []

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


def load_handler():
    boto3 = types.ModuleType("boto3")
    pg8000 = types.ModuleType("pg8000")
    pg8000_native = types.ModuleType("pg8000.native")
    pg8000.native = pg8000_native
    with patch.dict(sys.modules, {"boto3": boto3, "pg8000": pg8000, "pg8000.native": pg8000_native}):
        spec = importlib.util.spec_from_file_location("stac_seed_handler", HANDLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class StacSeedHandlerTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler()
        self.source = (
            b"\\set schema honua\n\\set env default\nBEGIN;\n"
            b"SET search_path TO :\"schema\", public;\n"
            b"SELECT set_config('honua.seed_env', :'env', false);\nCOMMIT;\n"
        )
        self.commit = "a" * 40
        self.url = (
            "https://raw.githubusercontent.com/honua-io/honua-server/"
            f"{self.commit}/tests/seed/demo-stac-imagery-v1.sql"
        )

    def test_managed_seed_hashes_executed_bytes_and_writes_attestation_transactionally(self):
        connection = FakeConnection()
        response = FakeResponse(self.source)
        environment = {
            "STAC_SEED_SOURCE_URL": self.url,
            "STAC_SEED_SOURCE_SHA256": hashlib.sha256(self.source).hexdigest(),
            "STAC_SEED_SERVER_COMMIT": self.commit,
            "STAC_SEED_METADATA_ENVIRONMENT": "default",
            "RECEIPT_DB_SECRET_ARN": "receipt",
            "DB_SECRET_ARN": "admin",
        }
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(self.handler.urllib.request, "urlopen", return_value=response), \
             patch.object(self.handler, "_read_secret", return_value={
                 "Username": "honua_demo_seed_receipt", "Password": "receipt-password"
             }), \
             patch.object(self.handler, "_connect", return_value=connection):
            result = self.handler._managed_seed({"operation": "apply-demo-stac-seed"})

        executed = connection.calls[1][0].encode()
        self.assertEqual(hashlib.sha256(executed).hexdigest(), result["executionSha256"])
        self.assertEqual("BEGIN", connection.calls[0][0])
        self.assertEqual("COMMIT", connection.calls[-1][0])
        self.assertNotIn("BEGIN;", connection.calls[1][0])
        self.assertNotIn("COMMIT;", connection.calls[1][0])
        all_sql = "\n".join(call[0] for call in connection.calls)
        self.assertIn("INSERT INTO honua.demo_seed_revisions", all_sql)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE", all_sql)
        self.assertIn("GRANT SELECT ON honua.demo_seed_revisions", all_sql)

    def test_managed_seed_rejects_caller_controlled_fields(self):
        with self.assertRaisesRegex(ValueError, "allowlisted operation"):
            self.handler._managed_seed({"operation": "apply-demo-stac-seed", "source": "SELECT 1"})

    def test_receipt_accepts_only_fixed_query_operation(self):
        with self.assertRaisesRegex(ValueError, "fixed query"):
            self.handler._receipt({"operation": "read-demo-stac-seed-receipt", "query": "DELETE"})

        row = [["demo-stac-imagery-v1", self.commit, self.url, "b" * 64, "c" * 64, "default", 7, 7]]
        connection = FakeConnection(receipt_rows=row)
        with patch.dict(os.environ, {"DB_SECRET_ARN": "receipt"}, clear=True), \
             patch.object(self.handler, "_connect", return_value=connection):
            receipt = self.handler._receipt({"operation": "read-demo-stac-seed-receipt"})
        self.assertEqual(7, receipt["currentRevision"])
        self.assertEqual(1, len(connection.calls), "receipt surface must execute one repository-owned query")


if __name__ == "__main__":
    unittest.main()
