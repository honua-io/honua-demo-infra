#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
IAC = ROOT / "stacks" / "aws-candidate-preflight" / "main.tf"
IAC_VERSIONS = ROOT / "stacks" / "aws-candidate-preflight" / "versions.tf"
HANDLER = ROOT / "stacks" / "aws" / "candidate-preflight" / "handler.py"
MANIFEST = ROOT / "stacks" / "aws" / "candidate-preflight" / "classification.v1.json"
RUNBOOK = ROOT / "runbook" / "candidate-preflight-v1.md"
MAIN = ROOT / "stacks" / "aws" / "main.tf"
INTERFACE = ROOT / "stacks" / "aws" / "validation" / "honua-module-interface"


class CandidatePreflightContractTests(unittest.TestCase):
    MODULE_COMMIT = "7abdb07c85c7a389a9c3cc97583e534f6c636a3b"
    SERVER_COMMIT = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
    DIGEST = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"

    def test_module_pin_and_typed_output_contract_move_together(self):
        main = MAIN.read_text(encoding="utf-8")
        contract = json.loads((INTERFACE / "interface-contract.json").read_text(encoding="utf-8"))
        outputs = (INTERFACE / "outputs.tf").read_text(encoding="utf-8")
        self.assertIn(f"ref={self.MODULE_COMMIT}", main)
        self.assertIn(f"ref={self.MODULE_COMMIT}", contract["source"])
        self.assertIn('output "admin_password_secret_arn"', outputs)

    def test_helper_root_is_state_isolated_and_uses_metadata_only_secret_discovery(self):
        iac = IAC.read_text(encoding="utf-8")
        versions = IAC_VERSIONS.read_text(encoding="utf-8")
        self.assertIn('data "aws_secretsmanager_secret" "admin_password"', iac)
        self.assertIn('name = "honua-demo-demo/admin-password"', iac)
        self.assertIn("data.aws_secretsmanager_secret.admin_password.arn", iac)
        self.assertNotIn("terraform_remote_state", iac)
        self.assertNotIn("secret_string", iac.lower())
        self.assertNotIn("module.honua", iac)
        self.assertIn('key          = "demo/aws-demo/candidate-preflight.tfstate"', versions)

    def test_iam_is_qualified_only_and_has_no_mutation_or_network_permissions(self):
        iac = IAC.read_text(encoding="utf-8")
        self.assertIn("Resource = [local.admin_password_secret_arn]", iac)
        self.assertIn('Resource = ["${local.candidate_preflight_app_function_arn}:${local.candidate_preflight_candidate_version}"]', iac)
        self.assertIn('Resource = ["${local.candidate_preflight_app_function_arn}:${local.candidate_preflight_live_alias_name}"]', iac)
        self.assertIn('Resource = ["${local.candidate_preflight_log_group_arn}:*"]', iac)
        self.assertNotIn("vpc_config", iac)
        self.assertNotIn("aws_security_group", iac)
        self.assertNotRegex(iac, r'resource\s+"aws_lambda_invocation"')
        for forbidden in (
            "lambda:UpdateFunction",
            "lambda:PublishVersion",
            "lambda:UpdateAlias",
            "ssm:",
            "states:",
            "ec2:",
            "rds:",
        ):
            self.assertNotIn(forbidden, iac)

        invoke = re.search(r'Sid\s+=\s+"InvokeExactCandidate"(?P<body>.*?)\n\s+\},', iac, re.DOTALL)
        self.assertIsNotNone(invoke)
        self.assertIn('"lambda:InvokeFunction"', invoke.group("body"))
        self.assertIn(":${local.candidate_preflight_candidate_version}", invoke.group("body"))

    def test_lambda_is_python_313_synchronous_bounded_and_sg_free(self):
        iac = IAC.read_text(encoding="utf-8")
        self.assertIn('runtime                        = "python3.13"', iac)
        self.assertIn('architectures                  = ["arm64"]', iac)
        self.assertIn("reserved_concurrent_executions = 1", iac)
        self.assertIn("publish                        = true", iac)
        self.assertIn("timeout                        = 120", iac)
        self.assertNotIn("AWSLambdaBasicExecutionRole", iac)
        self.assertIn("candidate_preflight_role_arn", iac)
        self.assertIn("candidate_preflight_handler_sha256", iac)
        self.assertIn("candidate_preflight_classification_sha256", iac)
        self.assertNotIn("source_dir", iac)

    def test_production_root_has_no_redirect_or_account_escape_hatch(self):
        iac = IAC.read_text(encoding="utf-8")
        versions = IAC_VERSIONS.read_text(encoding="utf-8")
        self.assertIn('data "aws_secretsmanager_secret" "admin_password"', iac)
        self.assertIn('name = "honua-demo-demo/admin-password"', iac)
        self.assertIn('region              = "us-west-2"', versions)
        self.assertIn('allowed_account_ids = ["585192672263"]', versions)
        self.assertNotIn("var.", iac + versions)
        self.assertNotIn("skip_requesting_account_id", iac + versions)

    def test_manifest_is_exact_expand_set_tied_to_candidate(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(self.SERVER_COMMIT, manifest["candidate"]["sourceCommit"])
        self.assertEqual(self.DIGEST, manifest["candidate"]["imageDigest"])
        scripts = manifest["scripts"]
        self.assertEqual(14, len(scripts))
        self.assertEqual(list(range(92, 106)), [int(entry["name"].split(".")[-2][:3]) for entry in scripts])
        self.assertEqual({"Expand"}, {entry["phase"] for entry in scripts})

    def test_handler_contract_has_fixed_event_and_sanitized_invoke(self):
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn('REQUEST = {"operation": OPERATION}', handler)
        self.assertIn('LogType="None"', handler)
        self.assertIn('Qualifier=IMMUTABLE["candidateVersion"]', handler)
        self.assertIn('headers["X-API-Key"] = admin_password', handler)
        self.assertIn('lifecycle_field="lifecycleStatus"', handler)
        self.assertIn('lifecycle_field="status"', handler)
        self.assertIn('value.get("isReady") is not True', handler)
        self.assertIn('value.get("isFailed") is not False', handler)
        self.assertIn('variables.get("HONUA_GIT_SHA") != IMMUTABLE["sourceCommit"]', handler)
        self.assertIn('"sourceCommit": variables["HONUA_GIT_SHA"]', handler)
        self.assertNotRegex(handler, r"\bprint\s*\(")
        for path in (
            "/healthz/live",
            "/healthz/ready",
            "/api/v1/admin/deploy/preflight",
            "/api/v1/admin/observability/migrations",
        ):
            self.assertIn(path, handler)

    def test_runbook_keeps_execution_manual_and_non_mutating(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("operator-invoked", runbook)
        self.assertIn("candidate-preflight-v1", runbook)
        self.assertIn("--log-type None", runbook)
        self.assertIn("does not move `live`", runbook)
        self.assertIn("does not run migrations", runbook)
        self.assertIn("Do not invoke", runbook)
        self.assertIn("assert-candidate-preflight-plan.py", runbook)
        self.assertIn("-refresh=false", runbook)
        self.assertIn("git diff --exit-code", runbook)
        self.assertIn("candidate_preflight_qualified_arn", runbook)
        self.assertIn("candidate-preflight-plan-receipt.py", runbook)
        self.assertIn("assert-candidate-preflight-runtime.py", runbook)
        self.assertIn("assert-candidate-preflight-invocation.py", runbook)
        self.assertIn("invocation-metadata.json", runbook)
        self.assertIn("invocation-payload.json", runbook)
        self.assertNotIn("candidate_preflight_function_name", runbook)
        self.assertNotIn("terraform apply -auto-approve", runbook)

    def test_runbook_init_is_readonly_and_negative_mutation_is_rejected(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")

        def require_readonly_init(document: str) -> None:
            commands = [line for line in document.splitlines() if line.startswith("terraform -chdir=stacks/aws-candidate-preflight init")]
            self.assertEqual(
                ["terraform -chdir=stacks/aws-candidate-preflight init -input=false -lockfile=readonly"],
                commands,
            )

        require_readonly_init(runbook)
        with self.assertRaises(AssertionError):
            require_readonly_init(runbook.replace(" -lockfile=readonly", ""))


if __name__ == "__main__":
    unittest.main()
