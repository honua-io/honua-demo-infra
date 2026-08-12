#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "runbook" / "demo-honua-io-capability-runbook.md"
WORKFLOW = ROOT / ".github" / "workflows" / "live-canary.yml"
IAC = ROOT / "stacks" / "aws" / "stac-seed-gate.tf"


class StacRunbookContractTests(unittest.TestCase):
    def test_managed_seed_is_separate_from_break_glass_sql(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("honua-demo-demo-stac-seed-manager", runbook)
        self.assertIn("apply-demo-stac-seed", runbook)
        self.assertIn("already-authorized break-glass", runbook)
        self.assertIn("effective database-administrator", runbook)
        self.assertIn("break-glass-sql", runbook)
        self.assertIn(': "${SEED_ENV:?', runbook)
        self.assertIn("--expected-source-sha256", runbook)

    def test_dispatch_consumes_query_only_receipt(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("id-token: write", workflow)
        self.assertIn("read-demo-stac-seed-receipt", workflow)
        self.assertIn("managed-seed-receipt.json", workflow)
        self.assertIn("receipt.metadataRevision !== receipt.currentRevision", workflow)
        self.assertIn("HONUA_DEMO_STAC_RECEIPT_ROLE_ARN", workflow)
        self.assertNotIn("apply-demo-stac-seed", workflow)

    def test_iac_enforces_invocation_and_database_privilege_split(self) -> None:
        iac = IAC.read_text(encoding="utf-8")
        self.assertIn("read-query-only-db-secret", iac)
        self.assertIn("aws_secretsmanager_secret.stac_seed_receipt_connection.arn", iac)
        self.assertIn("repo:honua-io/honua-demo-infra:ref:refs/heads/trunk", iac)
        self.assertIn("Resource = [aws_lambda_function.stac_seed_receipt.arn]", iac)
        github_policy = iac[iac.index('resource "aws_iam_role_policy" "github_stac_seed_receipt"') :]
        self.assertNotIn("stac_seed_manager.arn", github_policy)

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


if __name__ == "__main__":
    unittest.main()
