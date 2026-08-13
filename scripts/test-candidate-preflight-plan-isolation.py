#!/usr/bin/env python3

from __future__ import annotations

import copy
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
            raise RuntimeError("could not replace copied S3 backend")
        versions, replacements = re.subn(
            r'(?ms)^provider "aws" \{.*?^\}',
            '''provider "aws" {
  region                      = "us-west-2"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
}''',
            versions,
            count=1,
        )
        if replacements != 1:
            raise RuntimeError("could not replace copied AWS provider")
        versions_path.write_text(versions, encoding="utf-8")

        cls.state = cls.root / "primary.tfstate"
        cls.missing_state = cls.root / "missing-admin.tfstate"
        cls.write_state(cls.state, include_admin_output=True)
        cls.write_state(cls.missing_state, include_admin_output=False)
        main_path = cls.stack / "main.tf"
        main = main_path.read_text(encoding="utf-8")
        local_remote = f'''data "terraform_remote_state" "primary" {{
  backend   = "local"
  workspace = "default"
  config = {{
    path = "{cls.state.as_posix()}"
  }}
}}'''
        main, replacements = re.subn(
            r'(?ms)^data "terraform_remote_state" "primary" \{.*?^\}',
            local_remote,
            main,
            count=1,
        )
        if replacements != 1:
            raise RuntimeError("could not replace copied primary-state handoff")
        main_path.write_text(main, encoding="utf-8")

        cls.remote_contract = ("local", "default", {"path": cls.state.as_posix()})
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
        result = cls.plan("candidate.tfplan")
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
        show = subprocess.run(
            ["terraform", "show", "-json", "candidate.tfplan"],
            cwd=cls.stack,
            env=cls.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        cls.valid_plan = json.loads(show.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def write_state(path: Path, *, include_admin_output: bool) -> None:
        outputs = {
            "lambda_function_arn": {
                "value": "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua",
                "type": "string",
            },
            "lambda_function_name": {"value": "honua-demo-demo-honua", "type": "string"},
        }
        if include_admin_output:
            outputs["admin_password_secret_arn"] = {
                "value": "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd",
                "type": "string",
            }
        path.write_text(
            json.dumps(
                {
                    "version": 4,
                    "terraform_version": "1.15.8",
                    "serial": 1,
                    "lineage": "candidate-preflight-contract-fixture",
                    "outputs": outputs,
                    "resources": [],
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def plan(cls, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["terraform", "plan", "-refresh=false", "-input=false", "-no-color", "-out", name],
            cwd=cls.stack,
            env=cls.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_rejected(self, label: str, mutate) -> None:
        candidate = copy.deepcopy(self.valid_plan)
        mutate(candidate)
        with self.subTest(label=label), self.assertRaises(RuntimeError):
            ASSERTION_MODULE.assert_plan(candidate, STACK, self.remote_contract)

    def test_exact_candidate_only_plan_passes(self):
        ASSERTION_MODULE.assert_plan(self.valid_plan, STACK, self.remote_contract)

    def test_missing_authoritative_secret_output_fails_closed(self):
        main_path = self.stack / "main.tf"
        original = main_path.read_text(encoding="utf-8")
        main_path.write_text(original.replace(self.state.as_posix(), self.missing_state.as_posix()), encoding="utf-8")
        try:
            result = self.plan("missing.tfplan")
        finally:
            main_path.write_text(original, encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("admin_password_secret_arn", result.stdout + result.stderr)

    def test_plan_json_bypasses_all_fail_closed(self):
        managed = "aws_lambda_function.candidate_preflight"
        policy = "aws_iam_role_policy.candidate_preflight"
        remote = "data.terraform_remote_state.primary"

        def change(address: str):
            return next(item for item in self.valid_plan["resource_changes"] if item["address"] == address)

        def config(address: str):
            return next(item for item in self.valid_plan["configuration"]["root_module"]["resources"] if item["address"] == address)

        mutations = {
            "errored": lambda plan: plan.update(errored=True),
            "incomplete": lambda plan: plan.update(complete=False),
            "resource drift": lambda plan: plan.update(resource_drift=[{"address": managed}]),
            "deferred": lambda plan: plan.update(deferred_changes=[{"reason": "fixture"}]),
            "child module": lambda plan: plan["configuration"]["root_module"].update(module_calls={"bad": {}}),
            "extra config resource": lambda plan: plan["configuration"]["root_module"]["resources"].append({"address": "aws_s3_bucket.bad", "mode": "managed"}),
            "missing config data": lambda plan: plan["configuration"]["root_module"]["resources"].remove(next(item for item in plan["configuration"]["root_module"]["resources"] if item["address"] == remote)),
            "remote backend": lambda plan: next(item for item in plan["configuration"]["root_module"]["resources"] if item["address"] == remote)["expressions"]["backend"].update(constant_value="s3"),
            "remote workspace": lambda plan: next(item for item in plan["configuration"]["root_module"]["resources"] if item["address"] == remote)["expressions"]["workspace"].update(constant_value="other"),
            "remote config": lambda plan: next(item for item in plan["configuration"]["root_module"]["resources"] if item["address"] == remote)["expressions"]["config"]["constant_value"].update(path="wrong"),
            "extra resource change": lambda plan: plan["resource_changes"].append({"address": "aws_db_instance.bad", "change": {"actions": ["create"]}}),
            "missing resource change": lambda plan: plan["resource_changes"].pop(),
            "resource update": lambda plan: next(item for item in plan["resource_changes"] if item["address"] == managed)["change"].update(actions=["update"]),
            "action reason": lambda plan: next(item for item in plan["resource_changes"] if item["address"] == managed).update(action_reason="replace_because_tainted"),
            "extra output": lambda plan: plan["output_changes"].update(bad={"actions": ["create"]}),
            "output unknown": lambda plan: plan["output_changes"]["candidate_preflight_function_name"].update(after_unknown=True),
            "output sensitive": lambda plan: plan["output_changes"]["candidate_preflight_function_name"].update(after_sensitive=True),
            "output value": lambda plan: plan["output_changes"]["candidate_preflight_function_name"].update(after="wrong"),
            "IAM widened": lambda plan: json_policy_mutation(plan, policy, "Action", ["lambda:*"]),
            "IAM resource widened": lambda plan: json_policy_mutation(plan, policy, "Resource", ["*"]),
            "environment changed": lambda plan: next(item for item in plan["resource_changes"] if item["address"] == managed)["change"]["after"]["environment"][0]["variables"].update(EXPECTED_LIVE_VERSION="40"),
            "secret identity changed": lambda plan: next(item for item in plan["resource_changes"] if item["address"] == managed)["change"]["after"]["environment"][0]["variables"].update(ADMIN_PASSWORD_SECRET_ARN="arn:aws:secretsmanager:us-west-2:585192672263:secret:other-Ab12Cd"),
            "VPC attached": lambda plan: next(item for item in plan["resource_changes"] if item["address"] == managed)["change"]["after"].update(vpc_config=[{"subnet_ids": ["subnet-bad"]}]),
        }

        def json_policy_mutation(plan, address, field, value):
            item = next(item for item in plan["resource_changes"] if item["address"] == address)
            document = json.loads(item["change"]["after"]["policy"])
            document["Statement"][0][field] = value
            item["change"]["after"]["policy"] = json.dumps(document)

        for label, mutation in mutations.items():
            self.assert_rejected(label, mutation)

    def test_production_source_escape_hatches_all_fail_closed(self):
        mutations = {
            "helper backend key": ('demo/aws-demo/candidate-preflight.tfstate', 'demo/aws-demo/terraform.tfstate'),
            "helper backend bucket": ('honua-tfstate-585192672263', 'honua-tfstate-other'),
            "provider region": ('region              = "us-west-2"', 'region              = "us-east-1"'),
            "provider account": ('allowed_account_ids = ["585192672263"]', 'allowed_account_ids = ["000000000000"]'),
            "primary key": ('key          = "demo/aws-demo/terraform.tfstate"', 'key          = "other.tfstate"'),
            "primary workspace": ('workspace = "default"', 'workspace = "other"'),
            "skip guard": ('provider "aws" {', 'provider "aws" {\n  skip_requesting_account_id = true'),
            "ignore changes": ('resource "aws_lambda_function" "candidate_preflight" {', 'resource "aws_lambda_function" "candidate_preflight" {\n  lifecycle { ignore_changes = all }'),
        }
        for label, (old, new) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="candidate-source-negative-") as temporary:
                copied = Path(temporary) / "stack"
                shutil.copytree(STACK, copied)
                target = copied / ("versions.tf" if old in (copied / "versions.tf").read_text(encoding="utf-8") else "main.tf")
                content = target.read_text(encoding="utf-8")
                self.assertIn(old, content)
                target.write_text(content.replace(old, new, 1), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    ASSERTION_MODULE.assert_production_source(copied)


if __name__ == "__main__":
    unittest.main()
