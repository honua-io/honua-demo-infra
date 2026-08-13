#!/usr/bin/env python3
"""Exercise exact provider-map tag equality with the real Terraform evaluator."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts" / "fixtures" / "candidate-preflight" / "tag-map"
EXPECTED = {
    "Environment": "demo",
    "ManagedBy": "terraform",
    "Project": "honua-server",
    "Purpose": "public-demo",
}


class CandidatePreflightTagMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="candidate-preflight-tag-map-")
        cls.stack = Path(cls.temporary.name) / "stack"
        shutil.copytree(FIXTURE, cls.stack)
        cls.environment = os.environ.copy()
        cls.environment["TF_IN_AUTOMATION"] = "1"
        result = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            cwd=cls.stack,
            env=cls.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def plan(self, label: str, tags: dict[str, str]) -> subprocess.CompletedProcess[str]:
        variables = self.stack / f"{label}.tfvars.json"
        variables.write_text(json.dumps({"provider_tags": tags}), encoding="utf-8")
        return subprocess.run(
            [
                "terraform",
                "plan",
                "-refresh=false",
                "-input=false",
                "-no-color",
                f"-var-file={variables.name}",
                f"-out={label}.tfplan",
            ],
            cwd=self.stack,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exact_provider_map_passes_only_after_type_normalization(self) -> None:
        result = self.plan("exact", EXPECTED)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        show = subprocess.run(
            ["terraform", "show", "-json", "exact.tfplan"],
            cwd=self.stack,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs = json.loads(show.stdout)["planned_values"]["outputs"]
        self.assertFalse(outputs["legacy_object_comparison"]["value"])
        self.assertTrue(outputs["exact_map_comparison"]["value"])

    def test_nonexact_provider_maps_fail_closed(self) -> None:
        cases = {
            "missing": {key: value for key, value in EXPECTED.items() if key != "Purpose"},
            "extra": {**EXPECTED, "Unexpected": "tag"},
            "wrong": {**EXPECTED, "Purpose": "other"},
        }
        for label, tags in cases.items():
            with self.subTest(label=label):
                result = self.plan(label, tags)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "provider-shaped tags must exactly match the expected map",
                    result.stdout + result.stderr,
                )


if __name__ == "__main__":
    unittest.main()
