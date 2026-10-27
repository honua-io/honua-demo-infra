#!/usr/bin/env python3
"""Contract tests for .github/workflows/terraform.yml and drift-plan.yml (no AWS)."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def step(document: dict, job: str, prefix: str) -> dict:
    return next(s for s in document["jobs"][job]["steps"] if s.get("name", "").startswith(prefix))


def run(script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], env={"PATH": os.environ["PATH"], **env}, capture_output=True, text=True)


class ApplyWorkflow(unittest.TestCase):
    wf = load("terraform.yml")

    def test_apply_runs_only_on_trunk_in_the_protected_environment(self):
        job = self.wf["jobs"]["apply"]
        self.assertEqual(job["environment"], "demo")
        self.assertIn("github.ref == 'refs/heads/trunk'", job["if"])
        self.assertIn("github.event_name != 'pull_request'", job["if"])
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_apply_applies_only_the_saved_plan_and_checks_convergence(self):
        apply = step(self.wf, "apply", "terraform apply")["run"]
        self.assertIn('"$RUNNER_TEMP/plan/tfplan"', apply)
        self.assertNotIn("-auto-approve", apply)
        self.assertIn("after/status", step(self.wf, "apply", "Converged")["run"])

    def test_plan_precedes_apply_and_runs_the_prebuild_and_env_budget(self):
        script = (ROOT / "scripts" / "stack-plan.sh").read_text(encoding="utf-8")
        prebuild = script.index("get prebuild |")
        plan = script.index('plan "${args[@]}"')
        budget = script.index('python3 "$repo/scripts/check-lambda-env-budget.py"')
        self.assertLess(prebuild, plan)
        self.assertLess(plan, budget)
        self.assertIn("stack-plan.sh aws", step(self.wf, "apply", "Prebuild, plan")["run"])

    def test_pull_requests_plan_with_the_plan_only_environment(self):
        job = self.wf["jobs"]["plan"]
        self.assertEqual(job["environment"], "demo-plan")
        self.assertIn("pull_request", job["if"])
        self.assertIn("--no-lock", step(self.wf, "plan", "Prebuild, plan")["run"])

    def test_apply_refuses_without_role_or_password(self):
        script = step(self.wf, "apply", "Require the apply role")["run"]
        good = "arn:aws:iam::123456789012:role/honua-demo-infra-deploy-gha"
        self.assertEqual(run(script, {"ROLE_ARN": "", "ADMIN_PASSWORD": "x"}).returncode, 1)
        self.assertEqual(run(script, {"ROLE_ARN": good, "ADMIN_PASSWORD": ""}).returncode, 1)
        self.assertEqual(run(script, {"ROLE_ARN": good, "ADMIN_PASSWORD": "x"}).returncode, 0)

    def test_plan_text_is_never_printed(self):
        script = (ROOT / "scripts" / "stack-plan.sh").read_text(encoding="utf-8")
        self.assertIn('>"$out/plan.txt"', script)
        self.assertNotIn('cat "$out/plan.txt"', script)


class DriftWorkflow(unittest.TestCase):
    wf = load("drift-plan.yml")

    def test_schedule_is_on_and_the_plan_job_cannot_skip(self):
        self.assertTrue(self.wf[True]["schedule"])  # PyYAML reads `on:` as True
        self.assertNotIn("if", self.wf["jobs"]["plan"])
        self.assertEqual(self.wf["jobs"]["plan"]["environment"], "demo-drift")

    def test_unset_role_is_red(self):
        script = step(self.wf, "plan", "Require the drift role")["run"]
        self.assertEqual(run(script, {"ROLE_ARN": ""}).returncode, 1)
        self.assertEqual(run(script, {"ROLE_ARN": "arn:aws:iam::123456789012:role/x"}).returncode, 0)


class RollbackIsOneLine(unittest.TestCase):
    def test_the_stack_passes_lambda_alias_version_to_the_module(self):
        main = (ROOT / "stacks" / "aws" / "main.tf").read_text(encoding="utf-8")
        self.assertIn("lambda_alias_version = var.lambda_alias_version", main)
        tfvars = (ROOT / "stacks" / "aws" / "demo.tfvars").read_text(encoding="utf-8")
        self.assertIn('# lambda_alias_version = "52"', tfvars)
        self.assertTrue((ROOT / "runbook" / "rollback-by-pr.md").exists())


if __name__ == "__main__":
    unittest.main()
