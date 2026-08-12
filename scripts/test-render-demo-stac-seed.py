#!/usr/bin/env python3

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "stacks" / "aws" / "scripts" / "render-demo-stac-seed.py"


class RenderDemoStacSeedTests(unittest.TestCase):
    def invoke(self, seed: str, *, schema: str = "honua") -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
            handle.write(seed)
            seed_path = Path(handle.name)
        try:
            return subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--seed-file",
                    str(seed_path),
                    "--environment",
                    "default",
                    "--schema",
                    schema,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            seed_path.unlink()

    def test_renders_transaction_without_psql_tokens(self) -> None:
        result = self.invoke(
            "\\set schema honua\n\\set env default\n\\set seed_sha256 required\nBEGIN;\n"
            "SET search_path TO :\"schema\", public;\n"
            "SELECT set_config('honua.seed_schema', :'schema', false);\n"
            "SELECT set_config('honua.seed_env', :'env', false);\n"
            "SELECT set_config('honua.seed_sha256', :'seed_sha256', false);\nCOMMIT;\n"
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        statement = payload["statements"][0]
        self.assertNotIn("\\set", statement)
        self.assertNotIn(":'", statement)
        self.assertIn('SET search_path TO "honua", public;', statement)
        self.assertIn("set_config('honua.seed_schema', 'honua', false)", statement)
        self.assertRegex(payload["seedSourceSha256"], r"^[0-9a-f]{64}$")
        self.assertIn(payload["seedSourceSha256"], statement)
        self.assertIn("honua.demo_seed_revisions", payload["query"])

    def test_digest_is_derived_from_source_bytes(self) -> None:
        source = "\\set seed_sha256 required\nBEGIN;\nSELECT :'seed_sha256';\nCOMMIT;\n"
        result = self.invoke(source)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
            payload["seedSourceSha256"],
        )

    def test_rejects_seed_without_transactional_digest_marker(self) -> None:
        result = self.invoke("BEGIN;\nSELECT 1;\nCOMMIT;\n")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("transactional source-digest marker", result.stderr)

    def test_rejects_invalid_schema(self) -> None:
        result = self.invoke(
            "\\set seed_sha256 required\nBEGIN;\nSELECT :'seed_sha256';\nCOMMIT;\n",
            schema="honua; DROP SCHEMA public",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("PostgreSQL identifier", result.stderr)

    def test_requires_environment(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
            handle.write("\\set seed_sha256 required\nBEGIN;\nSELECT :'seed_sha256';\nCOMMIT;\n")
            seed_path = Path(handle.name)
        try:
            result = subprocess.run(
                [sys.executable, str(RENDERER), "--seed-file", str(seed_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            seed_path.unlink()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--environment", result.stderr)


if __name__ == "__main__":
    unittest.main()
