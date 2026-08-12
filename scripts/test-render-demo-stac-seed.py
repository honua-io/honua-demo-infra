#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "stacks" / "aws" / "scripts" / "render-demo-stac-seed.py"
COMMIT = "a" * 40


class RenderDemoStacSeedTests(unittest.TestCase):
    def invoke(
        self,
        seed: str,
        *,
        schema: str = "honua",
        environment: str | None = "default",
        digest: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
            handle.write(seed)
            seed_path = Path(handle.name)
        try:
            command = [sys.executable, str(RENDERER), "--seed-file", str(seed_path)]
            if environment is not None:
                command.extend(["--environment", environment])
            command.extend([
                "--schema", schema,
                "--expected-source-sha256", digest or hashlib.sha256(seed.encode()).hexdigest(),
                "--server-commit", COMMIT,
            ])
            return subprocess.run(command, check=False, capture_output=True, text=True)
        finally:
            seed_path.unlink()

    def test_emits_only_allowlisted_operation_after_local_validation(self) -> None:
        source = (
            "\\set schema honua\n\\set env default\nBEGIN;\n"
            "SET search_path TO :\"schema\", public;\n"
            "SELECT set_config('honua.seed_schema', :'schema', false);\n"
            "SELECT set_config('honua.seed_env', :'env', false);\nCOMMIT;\n"
        )
        result = self.invoke(source)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"operation": "apply-demo-stac-seed"}, json.loads(result.stdout))
        self.assertIn(hashlib.sha256(source.encode()).hexdigest(), result.stderr)
        self.assertRegex(result.stderr, r"execution=[0-9a-f]{64}")

    def test_rejects_source_digest_mismatch(self) -> None:
        result = self.invoke("-- psql seed\nBEGIN;\nSELECT 1;\nCOMMIT;\n", digest="f" * 64)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match", result.stderr)

    def test_rejects_custom_schema(self) -> None:
        result = self.invoke("BEGIN;\nSELECT 1;\nCOMMIT;\n", schema="tenant")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("migration-owned honua", result.stderr)

    def test_requires_environment(self) -> None:
        result = self.invoke("BEGIN;\nSELECT 1;\nCOMMIT;\n", environment=None)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--environment", result.stderr)


if __name__ == "__main__":
    unittest.main()
