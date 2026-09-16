#!/usr/bin/env python3

from pathlib import Path
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "runbook" / "demo-honua-io-capability-runbook.md"
WORKFLOW = ROOT / ".github" / "workflows" / "live-canary.yml"
IAC = ROOT / "stacks" / "aws" / "stac-seed-gate.tf"
TFVARS = ROOT / "stacks" / "aws" / "demo.tfvars"
HANDLER = ROOT / "stacks" / "aws" / "postgis-bootstrap" / "handler.py"
RECOVERY_RUNBOOK = ROOT / "runbook" / "stac-live-recovery-3384.md"
RESTORE_SCRIPT = ROOT / "scripts" / "stac-features-restore.py"


def load_restore():
    spec = importlib.util.spec_from_file_location("stac_features_restore", RESTORE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_owner_decision_is_restore_with_rebaseline_deferred(self) -> None:
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("## Owner decision: RESTORE", runbook)
        self.assertIn("Operator ruling A (2026-09-16)", runbook)
        self.assertIn("rebaseline path is **deferred to 2026.2**", runbook)
        self.assertIn("## Rebaseline (deferred to 2026.2)", runbook)
        self.assertNotIn("design and review a formal relation-loss rebaseline", runbook)
        for document in ["README.md", "demo-honua-io-capability-runbook.md"]:
            text = (ROOT / "runbook" / document).read_text(encoding="utf-8")
            self.assertNotIn("restore-versus-rebaseline decision", text, document)
            self.assertIn("RESTORE", text, document)

    def test_restore_path_orders_cutover_then_migrations_then_seed(self) -> None:
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")
        procedure = runbook[runbook.index("## Coordinator procedure") :]
        steps = [
            "prove pre-cutover",
            "aws rds copy-db-snapshot",
            "stac-features-restore.py cutover",
            "Phase 1 of\n   `db-recovery-and-migrations-092-105.md`",
            "Phase 2 of the same runbook",
            "prove post-migrations",
            "invoke `honua-demo-demo-stac-seed-manager` once",
            "prove post-seed",
            "run the trunk live canary",
        ]
        positions = [procedure.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        applies = re.findall(r"^\d+\. \*\*\[APPLY: ([^\]]+)\]\*\*", procedure, flags=re.M)
        self.assertEqual(
            applies,
            ["snapshot copy", "restore cutover", "main stack", "migration runner",
             "migrations 092-105", "seed", "repository variables"],
        )
        self.assertIn("post its plan summary on\nhonua-io/honua-demo-infra#79 before running it", procedure)
        self.assertNotIn("terraform destroy", runbook)

    def test_runbook_restore_source_matches_script_pins(self) -> None:
        runbook = RECOVERY_RUNBOOK.read_text(encoding="utf-8")
        restore = load_restore()
        for value in [
            restore.SOURCE_SNAPSHOT, restore.SOURCE_SNAPSHOT_CREATED, restore.RESTORE_SNAPSHOT,
            restore.EXPECTED_CONTENT_SHA256, restore.EXPECTED_KEY_SHA256,
            restore.EXPECTED_NON_SEED_CONTENT_SHA256, restore.EXPECTED_JOURNAL_LAST_CHANGE,
            f"{restore.EXPECTED_ROWS:,}", f"{restore.EXPECTED_NON_SEED_ROWS:,}",
            f"{restore.EXPECTED_JOURNAL_ROWS:,} / {restore.EXPECTED_JOURNAL_MAX_GENERATION:,}",
        ]:
            self.assertIn(value, runbook)
        layers = ", ".join(f"{layer}={rows:,}" for layer, rows in restore.EXPECTED_LAYER_ROWS.items())
        self.assertEqual(" ".join(runbook.split()).count(layers), 1)
        self.assertEqual(sum(restore.EXPECTED_LAYER_ROWS.values()), restore.EXPECTED_ROWS)
        self.assertEqual(
            restore.EXPECTED_ROWS - sum(restore.EXPECTED_LAYER_ROWS[l] for l in restore.SEED_LAYERS),
            restore.EXPECTED_NON_SEED_ROWS,
        )
        self.assertTrue(restore.RESTORE_SNAPSHOT.endswith(restore.EXPECTED_CONTENT_SHA256[:12]))
        self.assertIsNone(re.search(r"\b\d{12}\b", runbook), "no account ids in the recovery runbook")

    def test_cutover_is_one_guarded_non_destructive_schema_move(self) -> None:
        restore = load_restore()
        event = restore.render_cutover()
        self.assertEqual(set(event), {"operation", "statements", "query"})
        self.assertEqual(event["operation"], "break-glass-sql")
        self.assertEqual(len(event["statements"]), 1)
        sql = event["statements"][0]
        self.assertTrue(sql.startswith("DO $stac_3384_features_cutover$"))
        self.assertEqual(sql.count("ALTER TABLE"), 1)
        self.assertIn("ALTER TABLE public.features SET SCHEMA honua;", sql)
        self.assertIsNone(re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|CREATE)\b", sql, flags=re.I))
        self.assertIsNone(re.search(r"setval|sync_generation", sql, flags=re.I))
        self.assertIn("pg_advisory_xact_lock(144047712, 0)", sql)
        self.assertIn("LOCK TABLE public.features IN ACCESS EXCLUSIVE MODE", sql)
        self.assertIn("to_regclass('honua.features') IS NOT NULL", sql)
        self.assertIn("to_regclass('public.features') IS NULL", sql)
        move = sql.index("SET SCHEMA honua")
        for pin in [restore.EXPECTED_CONTENT_SHA256, str(restore.EXPECTED_ROWS)]:
            self.assertLess(sql.index(pin), move)
            self.assertGreater(sql.rindex(pin), move)
        pre_move = sql[:move]
        for pin in [restore.EXPECTED_KEY_SHA256, f"journal_rows IS DISTINCT FROM {restore.EXPECTED_JOURNAL_ROWS}",
                    f"journal_generation IS DISTINCT FROM {restore.EXPECTED_JOURNAL_MAX_GENERATION}",
                    "replica_rows IS DISTINCT FROM 0"]:
            self.assertIn(pin, pre_move)
        self.assertIn("pg_get_serial_sequence('honua.features', 'objectid') IS DISTINCT FROM 'honua.features_objectid_seq'", sql)
        self.assertIn("tgname = 'trigger_track_feature_changes'", sql[move:])
        self.assertIn("FROM honua.features", event["query"])

    def test_proof_events_are_query_only(self) -> None:
        restore = load_restore()
        for relation in restore.PROOF_RELATIONS:
            event = restore.render_proof(relation)
            self.assertEqual(set(event), {"operation", "query"})
            self.assertTrue(event["query"].startswith("WITH "))
            self.assertIsNone(
                re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|UPDATE|CREATE|ALTER|LOCK|setval)\b", event["query"], flags=re.I)
            )
        with self.assertRaises(ValueError):
            restore.render_proof("public.features; DROP TABLE honua.feature_changes")

    @staticmethod
    def _proof_row(restore, **overrides):
        values = {
            "sourcePresent": "True", "targetPresent": "False", "rows": str(restore.EXPECTED_ROWS),
            "contentSha256": restore.EXPECTED_CONTENT_SHA256, "nonSeedRows": str(restore.EXPECTED_NON_SEED_ROWS),
            "nonSeedContentSha256": restore.EXPECTED_NON_SEED_CONTENT_SHA256, "keySha256": restore.EXPECTED_KEY_SHA256,
            "journalNetLiveRows": str(restore.EXPECTED_ROWS), "journalNetLiveKeySha256": restore.EXPECTED_KEY_SHA256,
            "journalRows": str(restore.EXPECTED_JOURNAL_ROWS),
            "journalMaxGeneration": str(restore.EXPECTED_JOURNAL_MAX_GENERATION),
            "journalLastChange": restore.EXPECTED_JOURNAL_LAST_CHANGE, "replicas": "0", "trackingTrigger": "True",
            "objectidSequence": "public.features_objectid_seq",
            "layerRows": json.dumps({str(k): v for k, v in restore.EXPECTED_LAYER_ROWS.items()}),
        }
        values.update(overrides)
        return {"rows": [[values[field] for field in restore.FIELDS]]}

    def test_verify_accepts_reviewed_evidence_and_rejects_drift(self) -> None:
        restore = load_restore()
        moved = {"sourcePresent": "False", "targetPresent": "True", "objectidSequence": "honua.features_objectid_seq"}
        self.assertEqual(restore.verify("pre-cutover", restore.parse_response(self._proof_row(restore))), [])
        for phase in ["post-cutover", "post-migrations"]:
            self.assertEqual(restore.verify(phase, restore.parse_response(self._proof_row(restore, **moved))), [])
        seeded = dict(moved, contentSha256="0" * 64, journalRows=str(restore.EXPECTED_JOURNAL_ROWS + 14),
                      journalMaxGeneration=str(restore.EXPECTED_JOURNAL_MAX_GENERATION + 14),
                      journalLastChange="2026-09-20T00:00:00.000000Z")
        self.assertEqual(restore.verify("post-seed", restore.parse_response(self._proof_row(restore, **seeded))), [])

        drifts = [
            ("pre-cutover", {"rows": str(restore.EXPECTED_ROWS - 1)}),
            ("pre-cutover", {"contentSha256": "f" * 64}),
            ("pre-cutover", {"journalNetLiveKeySha256": "e" * 64}),
            ("pre-cutover", {"journalRows": str(restore.EXPECTED_JOURNAL_ROWS + 1)}),
            ("pre-cutover", {"replicas": "1"}),
            ("pre-cutover", {"targetPresent": "True"}),
            ("pre-cutover", {"trackingTrigger": "False"}),
            ("post-cutover", {}),
            ("post-cutover", dict(moved, contentSha256="d" * 64)),
            ("post-seed", dict(seeded, nonSeedContentSha256="c" * 64)),
            ("post-seed", dict(seeded, journalRows=str(restore.EXPECTED_JOURNAL_ROWS))),
            ("post-seed", dict(seeded, layerRows=json.dumps({"90810": 0}))),
        ]
        for phase, override in drifts:
            with self.subTest(phase=phase, override=override):
                self.assertNotEqual(restore.verify(phase, restore.parse_response(self._proof_row(restore, **override))), [])
        with self.assertRaises(RuntimeError):
            restore.parse_response({"errorMessage": "boom"})
        with self.assertRaises(RuntimeError):
            restore.parse_response(self._proof_row(restore, trackingTrigger="yes"))

    def test_cutover_requires_reviewed_proof_and_manual_snapshot_and_runs_once(self) -> None:
        restore = load_restore()
        moved = {"sourcePresent": "False", "targetPresent": "True", "objectidSequence": "honua.features_objectid_seq"}
        snapshot = {"id": restore.RESTORE_SNAPSHOT, "db": restore.DB_INSTANCE, "status": "available", "type": "manual"}

        def runner(calls, snapshot_state, invoke_response):
            def run(argv):
                calls.append(list(argv))
                if argv[:3] == ["aws", "rds", "describe-db-snapshots"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(snapshot_state), "")
                if argv[:3] == ["aws", "lambda", "invoke"]:
                    Path(argv[-1]).write_text(json.dumps(invoke_response), encoding="utf-8")
                    return subprocess.CompletedProcess(argv, 0, "None\n", "")
                raise AssertionError(f"unexpected command {argv}")
            return run

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            calls = []
            self.assertEqual(restore.prove("pre-cutover", evidence, runner(calls, snapshot, self._proof_row(restore))), 0)
            self.assertEqual(json.loads((evidence / "pre-cutover-event.json").read_text())["operation"], "break-glass-sql")
            self.assertNotIn("statements", json.loads((evidence / "pre-cutover-event.json").read_text()))
            reviewed = restore._sha256(evidence / "pre-cutover-proof.json")

            for bad_digest, bad_snapshot in [
                ("0" * 64, snapshot),
                ("not-a-digest", snapshot),
                (reviewed, dict(snapshot, status="creating")),
                (reviewed, dict(snapshot, type="automated")),
                (reviewed, dict(snapshot, id=restore.SOURCE_SNAPSHOT)),
            ]:
                calls = []
                with self.subTest(digest=bad_digest, snapshot=bad_snapshot), self.assertRaises(RuntimeError):
                    restore.cutover(evidence, bad_digest, runner(calls, bad_snapshot, {}))
                self.assertFalse(any(call[:3] == ["aws", "lambda", "invoke"] for call in calls))
                self.assertFalse((evidence / "cutover-attempt.json").exists())

            calls = []
            run = runner(calls, snapshot, self._proof_row(restore, **moved))
            self.assertEqual(restore.cutover(evidence, reviewed, run), 0)
            invokes = [call for call in calls if call[:3] == ["aws", "lambda", "invoke"]]
            self.assertEqual(len(invokes), 1)
            self.assertIn(restore.BOOTSTRAP_FUNCTION, invokes[0])
            self.assertEqual(json.loads((evidence / "cutover-event.json").read_text()), restore.render_cutover())
            self.assertTrue(json.loads((evidence / "post-cutover-proof.json").read_text())["pass"])

            calls = []
            with self.assertRaises(FileExistsError):
                restore.cutover(evidence, reviewed, runner(calls, snapshot, self._proof_row(restore, **moved)))
            self.assertFalse(any(call[:3] == ["aws", "lambda", "invoke"] for call in calls))

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
