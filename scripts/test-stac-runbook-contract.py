#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "runbook" / "demo-honua-io-capability-runbook.md"
WORKFLOW = ROOT / ".github" / "workflows" / "live-canary.yml"


class StacRunbookContractTests(unittest.TestCase):
    def test_break_glass_gate_reads_durable_marker_before_dispatch(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        marker_query = "FROM honua.demo_seed_revisions"
        query_position = runbook.index(marker_query, runbook.index("Post-deploy semantic gate"))
        dispatch_position = runbook.index("gh workflow run live-canary.yml")

        self.assertLess(query_position, dispatch_position)
        self.assertIn("EXPECTED_SEED_SHA256", runbook)
        self.assertIn("JOIN honua.metadata_v2_current", runbook)
        self.assertIn(".rows[0][3] == .rows[0][4]", runbook)
        self.assertIn("demo-stac-deployment-gate-receipt.json", runbook)
        self.assertIn("already-authorized break-glass DBA", runbook)
        self.assertIn("arbitrary-SQL/database-admin", runbook)

    def test_public_dispatch_binds_seed_source_digest_without_claiming_db_access(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("manifest.sources?.stacSeedSha256", workflow)
        self.assertIn("HONUA_DEMO_EXPECTED_STAC_SEED_SHA256", workflow)
        self.assertNotIn("aws lambda invoke", workflow)


if __name__ == "__main__":
    unittest.main()
