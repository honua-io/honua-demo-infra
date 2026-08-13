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
        versions, count = re.subn(r'(?ms)^  backend "s3" \{.*?^  \}', '  backend "local" {}', versions, count=1)
        if count != 1:
            raise RuntimeError("could not replace copied S3 backend")
        versions, count = re.subn(
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
        if count != 1:
            raise RuntimeError("could not replace copied AWS provider")
        versions_path.write_text(versions, encoding="utf-8")

        fixture = cls.stack / "admin-secret-metadata-fixture.txt"
        fixture.write_text("metadata-only fixture", encoding="utf-8")
        main_path = cls.stack / "main.tf"
        main = main_path.read_text(encoding="utf-8")
        main, count = re.subn(
            r'(?ms)^data "aws_secretsmanager_secret" "admin_password" \{.*?^\}\n\n(?=check "default_workspace_only")',
            '''data "archive_file" "admin_password" {
  type        = "zip"
  source_file = "${path.module}/admin-secret-metadata-fixture.txt"
  output_path = "${path.module}/admin-secret-metadata-fixture.zip"
}

''',
            main,
            count=1,
        )
        if count != 1:
            raise RuntimeError("could not replace copied metadata lookup")
        main = main.replace(
            "data.aws_secretsmanager_secret.admin_password.arn",
            '"arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd"',
        )
        main = main.replace(
            'data.aws_secretsmanager_secret.admin_password.name == "honua-demo-demo/admin-password"',
            'local.candidate_preflight_app_function_name == "honua-demo-demo-honua"',
        )
        main = main.replace(
            'data.aws_secretsmanager_secret.admin_password.description == "Admin API password for Honua."',
            'local.candidate_preflight_app_function_name == "honua-demo-demo-honua"',
        )
        main_path.write_text(main, encoding="utf-8")

        cls.secret_data_address = "data.archive_file.admin_password"
        cls.environment = os.environ.copy()
        cls.environment.update(
            {
                "AWS_ACCESS_KEY_ID": "offline-plan",
                "AWS_SECRET_ACCESS_KEY": "offline-plan",
                "AWS_EC2_METADATA_DISABLED": "true",
                "TF_IN_AUTOMATION": "1",
            }
        )
        subprocess.run(["terraform", "init", "-input=false", "-no-color"], cwd=cls.stack, env=cls.environment, check=True)
        result = subprocess.run(
            ["terraform", "plan", "-refresh=false", "-input=false", "-no-color", "-out", "candidate.tfplan"],
            cwd=cls.stack,
            env=cls.environment,
            capture_output=True,
            text=True,
            check=False,
        )
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
        cls.provider_contract = copy.deepcopy(cls.valid_plan["configuration"]["provider_config"])
        cls.check_contract = {
            item["address"]["to_display"] for item in cls.valid_plan["checks"]
        }

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def assert_plan(self, plan):
        ASSERTION_MODULE.assert_plan(
            plan,
            STACK,
            self.secret_data_address,
            self.provider_contract,
            self.check_contract,
        )

    def assert_rejected(self, label: str, mutate) -> None:
        candidate = copy.deepcopy(self.valid_plan)
        mutate(candidate)
        with self.subTest(label=label), self.assertRaises(RuntimeError):
            self.assert_plan(candidate)

    def test_exact_candidate_only_plan_passes(self):
        self.assert_plan(self.valid_plan)

    def test_sensitive_primary_state_sentinel_cannot_enter_plan(self):
        serialized = json.dumps(self.valid_plan)
        self.assertNotIn("SENSITIVE_PRIMARY_STATE_SENTINEL", serialized)
        candidate = copy.deepcopy(self.valid_plan)
        candidate["planned_values"]["outputs"]["sentinel_sensitive"] = {
            "value": "SENSITIVE_PRIMARY_STATE_SENTINEL",
            "sensitive": True,
        }
        with self.assertRaises(RuntimeError):
            self.assert_plan(candidate)

    def test_each_reviewed_source_mutation_prevents_plan(self):
        for index, filename in enumerate(("handler.py", "classification.v1.json")):
            source = self.source / filename
            original = source.read_text(encoding="utf-8")
            source.write_text(original + "\n", encoding="utf-8")
            try:
                result = subprocess.run(
                    ["terraform", "plan", "-refresh=false", "-input=false", "-no-color", "-out", f"mutated-{index}.tfplan"],
                    cwd=self.stack,
                    env=self.environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                source.write_text(original, encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotEqual(0, result.returncode)
                self.assertIn("reviewed source", result.stdout + result.stderr)
                self.assertIn("hash.", result.stdout + result.stderr)

    def test_plan_json_bypasses_all_fail_closed(self):
        function_address = "aws_lambda_function.candidate_preflight"
        policy_address = "aws_iam_role_policy.candidate_preflight"

        def resource(plan, address):
            return next(item for item in plan["resource_changes"] if item["address"] == address)

        def mutate_policy(plan, field, value):
            item = resource(plan, policy_address)
            document = json.loads(item["change"]["after"]["policy"])
            document["Statement"][0][field] = value
            item["change"]["after"]["policy"] = json.dumps(document)

        mutations = {
            "errored": lambda p: p.update(errored=True),
            "incomplete": lambda p: p.update(complete=False),
            "not applyable": lambda p: p.update(applyable=False),
            "unsupported format": lambda p: p.update(format_version="2.0"),
            "resource drift": lambda p: p.update(resource_drift=[{"address": function_address}]),
            "deferred": lambda p: p.update(deferred_changes=[{"reason": "fixture"}]),
            "actions": lambda p: p.update(actions=[{"address": "bad"}]),
            "action invocations": lambda p: p.update(action_invocations=[{"address": "bad"}]),
            "action triggers": lambda p: p.update(action_triggers=[{"address": "bad"}]),
            "unknown safety check": lambda p: p["checks"][0].update(status="unknown"),
            "replaced safety check": lambda p: p["checks"][0]["address"].update(to_display="check.other"),
            "aliased provider": lambda p: p["configuration"]["provider_config"].update(**{"aws.other": {"name": "aws", "full_name": "registry.terraform.io/hashicorp/aws"}}),
            "provider region": lambda p: p["configuration"]["provider_config"]["aws"]["expressions"]["region"].update(constant_value="us-east-1"),
            "resource provider alias": lambda p: next(item for item in p["configuration"]["root_module"]["resources"] if item["address"] == function_address).update(provider_config_key="aws.other"),
            "child module": lambda p: p["configuration"]["root_module"].update(module_calls={"bad": {}}),
            "extra config resource": lambda p: p["configuration"]["root_module"]["resources"].append({"address": "aws_s3_bucket.bad", "mode": "managed"}),
            "missing config data": lambda p: p["configuration"]["root_module"]["resources"].pop(),
            "extra resource change": lambda p: p["resource_changes"].append({"address": "aws_db_instance.bad", "change": {"actions": ["create"]}}),
            "missing resource change": lambda p: p["resource_changes"].pop(),
            "resource update": lambda p: resource(p, function_address)["change"].update(actions=["update"]),
            "action reason": lambda p: resource(p, function_address).update(action_reason="replace_because_tainted"),
            "extra output": lambda p: p["output_changes"].update(bad={"actions": ["create"]}),
            "output becomes known": lambda p: p["output_changes"]["candidate_preflight_qualified_arn"].update(after="arn:unqualified", after_unknown=False),
            "output sensitive": lambda p: p["output_changes"]["candidate_preflight_qualified_arn"].update(after_sensitive=True),
            "unqualified output": lambda p: p["output_changes"].update(candidate_preflight_function_name={"actions": ["create"], "after": "unqualified", "after_unknown": False, "after_sensitive": False}),
            "IAM widened": lambda p: mutate_policy(p, "Action", ["lambda:*"]),
            "IAM resource widened": lambda p: mutate_policy(p, "Resource", ["*"]),
            "environment changed": lambda p: resource(p, function_address)["change"]["after"]["environment"][0]["variables"].update(EXPECTED_LIVE_VERSION="40"),
            "secret changed": lambda p: resource(p, function_address)["change"]["after"]["environment"][0]["variables"].update(ADMIN_PASSWORD_SECRET_ARN="arn:aws:secretsmanager:us-west-2:585192672263:secret:other-Ab12Cd"),
            "VPC": lambda p: resource(p, function_address)["change"]["after"].update(vpc_config=[{"subnet_ids": ["subnet-bad"]}]),
            "layer": lambda p: resource(p, function_address)["change"]["after"].update(layers=["arn:bad"]),
            "filesystem": lambda p: resource(p, function_address)["change"]["after"].update(file_system_config=[{"arn": "arn:bad"}]),
            "dead letter": lambda p: resource(p, function_address)["change"]["after"].update(dead_letter_config=[{"target_arn": "arn:bad"}]),
            "archive hash": lambda p: resource(p, function_address)["change"]["after"].update(source_code_hash="wrong"),
            "archive filename": lambda p: resource(p, function_address)["change"]["after"].update(filename="other.zip"),
            "role ARN": lambda p: resource(p, function_address)["change"]["after"].update(role="arn:aws:iam::585192672263:role/other"),
            "publish disabled": lambda p: resource(p, function_address)["change"]["after"].update(publish=False),
            "IAM expression disconnected": lambda p: next(item for item in p["configuration"]["root_module"]["resources"] if item["address"] == policy_address)["expressions"]["policy"].update(references=[]),
            "environment expression disconnected": lambda p: next(item for item in p["configuration"]["root_module"]["resources"] if item["address"] == function_address)["expressions"]["environment"][0]["variables"].update(references=[]),
            "role expression disconnected": lambda p: next(item for item in p["configuration"]["root_module"]["resources"] if item["address"] == function_address)["expressions"]["role"].update(references=[]),
            "archive expression disconnected": lambda p: next(item for item in p["configuration"]["root_module"]["resources"] if item["address"] == function_address)["expressions"]["source_code_hash"].update(references=[]),
        }
        for label, mutation in mutations.items():
            self.assert_rejected(label, mutation)

    def test_production_source_escape_hatches_all_fail_closed(self):
        mutations = {
            "helper backend key": ('demo/aws-demo/candidate-preflight.tfstate', 'demo/aws-demo/terraform.tfstate'),
            "helper backend bucket": ('honua-tfstate-585192672263', 'honua-tfstate-other'),
            "provider region": ('region              = "us-west-2"', 'region              = "us-east-1"'),
            "provider account": ('allowed_account_ids = ["585192672263"]', 'allowed_account_ids = ["000000000000"]'),
            "metadata version": ('data "aws_secretsmanager_secret" "admin_password"', 'data "aws_secretsmanager_secret_version" "admin_password"'),
            "metadata name": ('name = "honua-demo-demo/admin-password"', 'name = "other"'),
            "metadata tag map normalization": ('self.tags == tomap(local.common_tags)', 'self.tags == local.common_tags'),
            "skip guard": ('provider "aws" {', 'provider "aws" {\n  skip_requesting_account_id = true'),
            "ignore changes": ('resource "aws_lambda_function" "candidate_preflight" {', 'resource "aws_lambda_function" "candidate_preflight" {\n  lifecycle { ignore_changes = all }'),
            "source directory": ('data "archive_file" "candidate_preflight" {', 'data "archive_file" "candidate_preflight" {\n  source_dir = "extra"'),
            "extra archive source": ('source {', 'source {\n    filename = "extra.py"\n  }\n  source {'),
            "handler hash": (ASSERTION_MODULE.HANDLER_SHA256, "0" * 64),
            "classification hash": (ASSERTION_MODULE.CLASSIFICATION_SHA256, "1" * 64),
            "hardcoded secret suffix": (
                "admin_password_secret_arn = data.aws_secretsmanager_secret.admin_password.arn",
                'admin_password_secret_arn = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd"',
            ),
            "disconnected secret expression": (
                "admin_password_secret_arn = data.aws_secretsmanager_secret.admin_password.arn",
                "admin_password_secret_arn = local.candidate_preflight_app_function_arn",
            ),
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
