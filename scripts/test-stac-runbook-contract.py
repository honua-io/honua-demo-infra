#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "runbook" / "demo-honua-io-capability-runbook.md"
WORKFLOW = ROOT / ".github" / "workflows" / "live-canary.yml"
IAC = ROOT / "stacks" / "aws" / "stac-seed-gate.tf"
TFVARS = ROOT / "stacks" / "aws" / "demo.tfvars"
HANDLER = ROOT / "stacks" / "aws" / "postgis-bootstrap" / "handler.py"
RECOVERY_RUNBOOK = ROOT / "runbook" / "stac-live-recovery-3384.md"


class StacRunbookContractTests(unittest.TestCase):
    SERVER_HEAD = "1fc339a3692289e9bc4ec90ed1533c5eb22a995e"
    SEED_SHA256 = "de33f838030b7aeced93ea7f8084ad4b45b1d76e2ae53bbcbc8d3ffc7b202687"

    def test_managed_seed_is_separate_from_break_glass_sql(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("honua-demo-demo-stac-seed-manager", runbook)
        self.assertIn("apply-demo-stac-seed", runbook)
        self.assertIn("already-authorized break-glass", runbook)
        self.assertIn("effective database-administrator", runbook)
        self.assertIn("break-glass-sql", runbook)
        self.assertIn("SEED_ENV=Production", runbook)
        self.assertIn('test "$SEED_ENV" = Production', runbook)
        self.assertIn("--expected-source-sha256", runbook)

    def test_dispatch_consumes_query_only_receipt(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("id-token: write", workflow)
        self.assertIn("read-demo-stac-seed-receipt", workflow)
        self.assertIn("managed-seed-receipt.json", workflow)
        self.assertIn("receipt.metadataRevision !== receipt.currentRevision", workflow)
        self.assertIn("HONUA_DEMO_STAC_RECEIPT_ROLE_ARN", workflow)
        self.assertIn('EXPECTED_METADATA_ENVIRONMENT: Production', workflow)
        self.assertIn('receiptRole: "honua_demo_seed_receipt"', workflow)
        self.assertIn('HONUA_DEMO_REQUIRE_STAC_SEED_BINDING: "true"', workflow)
        receipt_segment = workflow[
            workflow.index("Require managed seed receipt binding") : workflow.index("Probe every published demo service family")
        ]
        self.assertNotIn("github.event_name == 'workflow_dispatch'", receipt_segment)
        self.assertNotIn("apply-demo-stac-seed", workflow)

    def test_iac_enforces_invocation_and_database_privilege_split(self) -> None:
        iac = IAC.read_text(encoding="utf-8")
        tfvars = TFVARS.read_text(encoding="utf-8")
        self.assertIn("read-query-only-db-secret", iac)
        self.assertIn("aws_secretsmanager_secret.stac_seed_receipt_connection.arn", iac)
        self.assertIn("repo:honua-io/honua-demo-infra:ref:refs/heads/trunk", iac)
        self.assertIn("Resource = [aws_lambda_function.stac_seed_receipt.arn]", iac)
        github_policy = iac[iac.index('resource "aws_iam_role_policy" "github_stac_seed_receipt"') :]
        self.assertNotIn("stac_seed_manager.arn", github_policy)
        self.assertIn('var.stac_seed_metadata_environment == "Production"', iac)
        self.assertIn('stac_seed_metadata_environment = "Production"', tfvars)

    def test_receipt_role_reconciliation_is_explicit(self) -> None:
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn('"receiptRole": _RECEIPT_ROLE', handler)
        self.assertIn('"receiptRoleReconciled": True', handler)

    def test_live_recovery_stops_before_unsafe_rebaseline(self) -> None:
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("222,124 retained change rows", runbook)
        self.assertIn("no complete relation-loss", runbook)
        self.assertIn("rebaseline contract", runbook)
        self.assertIn("Do not invoke the STAC seed manager yet", runbook)
        self.assertIn("Do not bypass the seed guard", runbook)
        self.assertIn("automated snapshots from", runbook)

    def test_lambdas_have_separate_functional_nat_and_database_egress(self) -> None:
        iac = IAC.read_text(encoding="utf-8")
        bootstrap = (ROOT / "stacks" / "aws" / "postgis-bootstrap.tf").read_text(encoding="utf-8")
        for resource, attachment in [
            ("stac_seed_manager", "aws_security_group.stac_seed_manager.id"),
            ("stac_seed_receipt", "aws_security_group.stac_seed_receipt.id"),
        ]:
            start = iac.index(f'resource "aws_security_group" "{resource}"')
            end = iac.index('\nresource "', start + 1)
            block = iac[start:end]
            self.assertIn('from_port   = 5432', block)
            self.assertIn('cidr_blocks = [local.vpc_cidr]', block)
            self.assertIn('from_port   = 443', block)
            self.assertIn('cidr_blocks = ["0.0.0.0/0"]', block)
            self.assertIn(f"security_group_ids = [{attachment}]", iac)

        self.assertNotIn(
            "security_group_ids = [aws_security_group.postgis_bootstrap.id]",
            iac,
            "managed/query-only functions must not share the arbitrary-SQL bootstrap SG",
        )
        self.assertIn('public Secrets Manager through the private-subnet NAT route', bootstrap)
        self.assertIn('cidr_blocks = ["0.0.0.0/0"]', bootstrap)

    def test_ci_runs_handler_contracts_and_real_terraform_validation(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "manifest-drift.yml").read_text(encoding="utf-8")
        validator = (ROOT / "scripts" / "validate-terraform-root.py").read_text(encoding="utf-8")

        self.assertIn("python3 scripts/test-stac-seed-handler.py", workflow)
        self.assertIn("python3 scripts/validate-terraform-root.py", workflow)
        self.assertIn('["terraform", "init", "-backend=false", "-input=false", "-no-color"]', validator)
        self.assertIn('["terraform", "validate", "-no-color"]', validator)
        self.assertNotIn("HONUA_IAC_READ_TOKEN", workflow)

    def test_break_glass_seed_caller_uses_handler_operation_contract(self) -> None:
        script = (ROOT / "stacks" / "aws" / "scripts" / "seed-test-service.sh").read_text(encoding="utf-8")
        manifest = (ROOT / "stacks" / "aws" / "SEED_MANIFEST.md").read_text(encoding="utf-8")

        self.assertIn('"operation": "break-glass-sql"', script)
        self.assertIn('"operation":"break-glass-sql"', manifest)

    def test_seed_indexes_pin_exact_server_and_digest(self) -> None:
        for path in [ROOT / "README.md", ROOT / "runbook" / "README.md"]:
            document = path.read_text(encoding="utf-8")
            self.assertIn(self.SERVER_HEAD, document, str(path))
            self.assertIn(self.SEED_SHA256, document, str(path))


if __name__ == "__main__":
    unittest.main()
