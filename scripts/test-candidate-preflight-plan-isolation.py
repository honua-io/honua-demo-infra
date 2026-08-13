#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "stacks" / "aws-candidate-preflight"
SOURCE = ROOT / "stacks" / "aws" / "candidate-preflight"
ASSERTION = ROOT / "scripts" / "assert-candidate-preflight-plan.py"


def load_assertion_module():
    spec = importlib.util.spec_from_file_location("candidate_plan_assertion", ASSERTION)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load candidate plan assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSERTION_MODULE = load_assertion_module()


class CandidatePreflightPlanIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="candidate-preflight-plan-")
        cls.root = Path(cls.temporary.name)
        cls.stack = cls.root / "stacks" / "aws-candidate-preflight"
        cls.source = cls.root / "stacks" / "aws" / "candidate-preflight"
        shutil.copytree(STACK, cls.stack)
        shutil.copytree(SOURCE, cls.source)
        versions_path = cls.stack / "versions.tf"
        versions = versions_path.read_text(encoding="utf-8")
        versions, replacements = re.subn(
            r'(?ms)^  backend "s3" \{.*?^  \}',
            '  backend "local" {}',
            versions,
            count=1,
        )
        if replacements != 1:
            raise RuntimeError("could not replace the copied S3 backend for offline planning")
        versions_path.write_text(versions, encoding="utf-8")
        cls.environment = os.environ.copy()
        cls.environment.update(
            {
                "AWS_ACCESS_KEY_ID": "offline-plan",
                "AWS_SECRET_ACCESS_KEY": "offline-plan",
                "AWS_EC2_METADATA_DISABLED": "true",
                "TF_IN_AUTOMATION": "1",
            }
        )
        subprocess.run(
            ["terraform", "init", "-input=false", "-no-color"],
            cwd=cls.stack,
            env=cls.environment,
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def write_state(self, *, include_admin_output: bool = True) -> Path:
        outputs = {
            "lambda_function_arn": {
                "value": "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua",
                "type": "string",
            },
            "lambda_function_name": {
                "value": "honua-demo-demo-honua",
                "type": "string",
            },
        }
        if include_admin_output:
            outputs["admin_password_secret_arn"] = {
                "value": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Fixture1",
                "type": "string",
            }
        state = {
            "version": 4,
            "terraform_version": "1.15.6",
            "serial": 1,
            "lineage": "candidate-preflight-contract-fixture",
            "outputs": outputs,
            "resources": [],
        }
        path = self.root / ("primary.tfstate" if include_admin_output else "missing-admin.tfstate")
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def plan(self, state: Path, name: str) -> subprocess.CompletedProcess[str]:
        config = json.dumps({"path": state.as_posix()}, separators=(",", ":"))
        return subprocess.run(
            [
                "terraform",
                "plan",
                "-refresh=false",
                "-input=false",
                "-no-color",
                "-out",
                name,
                "-var=offline_plan=true",
                "-var=primary_state_backend=local",
                f"-var=primary_state_config={config}",
            ],
            cwd=self.stack,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exact_candidate_only_plan_passes_allowlist(self):
        result = self.plan(self.write_state(), "candidate.tfplan")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        show = subprocess.run(
            ["terraform", "show", "-json", "candidate.tfplan"],
            cwd=self.stack,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        plan = json.loads(show.stdout)
        ASSERTION_MODULE.assert_plan(plan)
        addresses = {change["address"] for change in plan["resource_changes"]}
        for forbidden in (
            "module.honua",
            "aws_lambda_alias",
            "aws_db_",
            "aws_rds_",
            "aws_secretsmanager_secret_version",
            "seed",
            "bootstrap",
            "aws_cloudfront_",
        ):
            self.assertFalse(any(forbidden in address for address in addresses), forbidden)

    def test_missing_authoritative_secret_output_fails_closed(self):
        result = self.plan(self.write_state(include_admin_output=False), "missing.tfplan")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("admin_password_secret_arn", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
