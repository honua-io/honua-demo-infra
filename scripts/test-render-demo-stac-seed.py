#!/usr/bin/env python3

from __future__ import annotations

import json
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
            "\\set schema honua\n\\set env default\nBEGIN;\n"
            "SET search_path TO :\"schema\", public;\n"
            "SELECT set_config('honua.seed_schema', :'schema', false);\n"
            "SELECT set_config('honua.seed_env', :'env', false);\nCOMMIT;\n"
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        statement = payload["statements"][0]
        self.assertNotIn("\\set", statement)
        self.assertNotIn(":'", statement)
        self.assertIn('SET search_path TO "honua", public;', statement)
        self.assertIn("set_config('honua.seed_schema', 'honua', false)", statement)

    def test_rejects_invalid_schema(self) -> None:
        result = self.invoke("BEGIN;\nSELECT 1;\nCOMMIT;\n", schema="honua; DROP SCHEMA public")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("PostgreSQL identifier", result.stderr)


if __name__ == "__main__":
    unittest.main()
