import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

SPEC = importlib.util.spec_from_file_location("shared_load", Path(__file__).with_name("shared-load-workflow.py"))
LOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOAD)


class SharedLoadTests(unittest.TestCase):
    def setUp(self):
        self.env = {"GITHUB_REF": "refs/heads/trunk", "GITHUB_RUN_ID": "20", "GITHUB_REPOSITORY": "example/demo",
                    "LOAD_ACTION": "stage", "SAMPLES_SHA": "a" * 40, "PLAN_SHA256": "b" * 64,
                    "PREVIOUS_RUN_ID": "19"}

    def prior(self, **changes):
        return {"workflow_id": 1, "path": LOAD.WORKFLOW, "head_branch": "trunk", "event": "workflow_dispatch",
                "status": "completed", "conclusion": "failure", "repository": {"full_name": "example/demo"}, **changes}

    def test_dispatch_rejects_branch_and_untrusted_inputs_before_network(self):
        for key, value in [("GITHUB_REF", "refs/heads/topic"), ("SAMPLES_SHA", "trunk"),
                           ("PLAN_SHA256", "bad"), ("PREVIOUS_RUN_ID", "20"), ("PREVIOUS_RUN_ID", "../19")]:
            with self.subTest(key=key, value=value), patch.object(LOAD, "capture") as capture:
                with self.assertRaises(ValueError):
                    LOAD.validate({**self.env, key: value})
                capture.assert_not_called()

    def test_failed_same_workflow_run_is_recoverable(self):
        with patch.object(LOAD, "capture", side_effect=[json.dumps({"workflow_runs": []}), json.dumps({"workflow_id": 1}), json.dumps(self.prior())]):
            LOAD.validate(self.env)

    def test_artifact_requires_same_workflow_repository_branch_and_event(self):
        for changes in [{"workflow_id": 2}, {"path": "other.yml"}, {"head_branch": "topic"},
                        {"event": "pull_request"}, {"status": "in_progress"}, {"repository": {"full_name": "other/repo"}}]:
            with self.subTest(changes=changes), patch.object(LOAD, "capture", side_effect=[
                    json.dumps({"workflow_runs": []}), json.dumps({"workflow_id": 1}), json.dumps(self.prior(**changes))]):
                with self.assertRaises(ValueError):
                    LOAD.validate(self.env)

    def test_prepare_has_no_prior_artifact(self):
        with patch.object(LOAD, "capture") as capture:
            LOAD.validate({**self.env, "LOAD_ACTION": "prepare", "PLAN_SHA256": "", "PREVIOUS_RUN_ID": ""})
            capture.assert_not_called()
            with self.assertRaises(ValueError):
                LOAD.validate({**self.env, "LOAD_ACTION": "prepare"})

    def test_metrics_fail_closed_when_missing_or_stale(self):
        for points in [[], [{"Timestamp": "2000-01-01T00:00:00Z", "Maximum": 1}]]:
            with patch.object(LOAD, "aws", return_value={"Datapoints": points}), self.assertRaises(ValueError):
                LOAD.metric("demo", "DatabaseConnections", "Maximum")
        now = datetime.now(timezone.utc).isoformat()
        with patch.object(LOAD, "aws", return_value={"Datapoints": [{"Timestamp": now, "Maximum": 129}]}):
            self.assertEqual(129, LOAD.metric("demo", "DatabaseConnections", "Maximum"))

    def test_connection_and_actual_storage_guards(self):
        for connections, free in [(130, 3e9), (10, 2e9), (10, 3.1e9)]:
            with self.subTest(connections=connections, free=free), patch.object(LOAD, "metric", side_effect=[connections, free]):
                with self.assertRaises(ValueError):
                    LOAD.monitor("demo", 4e9)
        with patch.object(LOAD, "metric", side_effect=[129, 3.5e9]):
            self.assertEqual(5e8, LOAD.monitor("demo", 4e9)["storageGrowthBytes"])

    def test_rollback_can_disable_while_connection_ceiling_is_high(self):
        with patch.object(LOAD, "metric", side_effect=[140, 1e9]):
            self.assertEqual(140, LOAD.monitor("demo", 4e9, action="rollback")["connections"])

    def test_generation_rejects_stale_receipts_before_mutating(self):
        def get_object(args, **kwargs):
            Path(args[-1]).write_text(json.dumps({"runId": "18", "samplesSha": "a" * 40, "planSha256": "b" * 64}))
            return subprocess.CompletedProcess(args, 0, json.dumps({"ETag": '"etag"'}), "")
        with patch.object(LOAD.subprocess, "run", side_effect=get_object), patch.object(LOAD, "aws") as aws:
            with self.assertRaises(ValueError):
                LOAD.claim_generation("private-bucket", self.env)
            aws.assert_not_called()

    def test_generation_uses_conditional_write_and_saves_receipt(self):
        def get_object(args, **kwargs):
            Path(args[-1]).write_text(json.dumps({"runId": "19", "samplesSha": "a" * 40, "planSha256": "b" * 64}))
            return subprocess.CompletedProcess(args, 0, json.dumps({"ETag": '"etag"'}), "")
        with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                patch.object(LOAD.subprocess, "run", side_effect=get_object), patch.object(LOAD, "aws") as aws:
            LOAD.claim_generation("private-bucket", self.env)
            self.assertEqual(("--if-match", '"etag"'), aws.call_args.args[-2:])
            self.assertEqual("20", json.loads((Path(directory) / "generation.json").read_text())["runId"])

    def test_missing_generation_is_only_allowed_for_stage_and_is_create_only(self):
        result = subprocess.CompletedProcess([], 255, "", "An error occurred (NoSuchKey)")
        with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                patch.object(LOAD.subprocess, "run", return_value=result), patch.object(LOAD, "aws") as aws:
            LOAD.claim_generation("private-bucket", self.env)
            self.assertEqual(("--if-none-match", "*"), aws.call_args.args[-2:])
            with self.assertRaises(ValueError):
                LOAD.claim_generation("private-bucket", {**self.env, "LOAD_ACTION": "activate"})

    def test_upload_never_overwrites_existing_source(self):
        layer = {"name": "maui-example", "cleanSha256": "a" * 64, "bytes": 12}
        result = subprocess.CompletedProcess([], 0, json.dumps({"Metadata": {"sha256": "wrong"}, "ContentLength": 12}), "")
        with patch.object(LOAD.subprocess, "run", return_value=result), patch.object(LOAD, "aws") as aws:
            with self.assertRaises(ValueError):
                LOAD.stage_objects("private-bucket", {"layers": [layer]}, "b" * 64)
            aws.assert_not_called()

    def test_monitor_failure_terminates_child(self):
        class Child:
            returncode = None
            terminated = False

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.returncode = -15

        child = Child()
        with patch.object(LOAD, "monitor", side_effect=[{}, ValueError("ceiling")]), \
                patch.object(LOAD.subprocess, "Popen", return_value=child):
            with self.assertRaises(ValueError):
                LOAD.run_guarded(["node"], self.env, ("demo", 4e9))
            self.assertTrue(child.terminated)

    def test_cross_plan_generation_requires_manual_reconciliation(self):
        def get_object(args, **kwargs):
            self.assertIn("state-local/last-attempt.json", args)
            Path(args[-1]).write_text(json.dumps({"runId": "19", "samplesSha": "a" * 40, "planSha256": "c" * 64}))
            return subprocess.CompletedProcess(args, 0, json.dumps({"ETag": '"etag"'}), "")
        with patch.object(LOAD.subprocess, "run", side_effect=get_object), patch.object(LOAD, "aws") as aws:
            with self.assertRaises(ValueError):
                LOAD.claim_generation("private-bucket", self.env)
            aws.assert_not_called()

    def test_samples_receive_only_allowlisted_environment(self):
        env = {**self.env, "PATH": "/bin", "HOME": "/tmp", "HONUA_ADMIN_API_KEY": "key",
               "AWS_SECRET_ACCESS_KEY": "private", "GH_TOKEN": "private",
               "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private"}
        child = unittest.mock.Mock()
        child.poll.return_value = 0
        child.returncode = 0
        with patch.object(LOAD, "monitor"), patch.object(LOAD.subprocess, "Popen", return_value=child) as popen:
            LOAD.run_guarded(["node"], env, ("demo", 4e9))
            self.assertEqual({"PATH": "/bin", "HOME": "/tmp", "HONUA_ADMIN_API_KEY": "key"}, popen.call_args.kwargs["env"])

    def test_pending_apply_refuses_load(self):
        with patch.object(LOAD, "capture", return_value=json.dumps({"workflow_runs": [{"status": "queued"}]})):
            with self.assertRaisesRegex(ValueError, "Terraform apply"):
                LOAD.validate(self.env)

    def test_verify_rejects_artifact_revision_or_digest_mismatch(self):
        for changes in [{"samplesSha": "c" * 40}, {"planSha256": "c" * 64}]:
            with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                    patch.object(LOAD, "capture", side_effect=[self.env["SAMPLES_SHA"], ""]):
                Path(directory, "workflow.json").write_text(json.dumps({"samplesSha": "a" * 40,
                                                                       "planSha256": "b" * 64, **changes}))
                with self.assertRaises(ValueError):
                    LOAD.verify(self.env)

    def test_stage_creates_only_missing_objects_and_refuses_other_errors(self):
        layer = {"name": "example", "cleanSha256": "a" * 64, "bytes": 12}
        for error in ["(404)", "(403)"]:
            result = subprocess.CompletedProcess([], 255, "", error)
            with patch.object(LOAD.subprocess, "run", return_value=result), patch.object(LOAD, "aws") as aws, \
                    patch.object(LOAD, "capture", return_value="https://source.test"):
                if error == "(404)":
                    LOAD.stage_objects("private-bucket", {"layers": [layer]}, "b" * 64)
                    self.assertIn("--if-none-match", aws.call_args.args)
                else:
                    with self.assertRaises(ValueError):
                        LOAD.stage_objects("private-bucket", {"layers": [layer]}, "b" * 64)
                    aws.assert_not_called()

    def test_configure_requires_exactly_one_database(self):
        for names in [[], ["honua-demo-demo-a", "honua-demo-demo-b"]]:
            with patch.object(LOAD, "aws", side_effect=[{"SecretString": "key"}, "private-bucket", names]):
                with self.assertRaises(ValueError):
                    LOAD.configure({})

    def test_load_claims_before_upload_and_uses_private_url_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                patch.object(LOAD, "configure", return_value=("private-bucket", "demo")), \
                patch.object(LOAD, "metric", return_value=4e9), patch.object(LOAD, "monitor"):
            root = Path(directory)
            (root / "shared-load-plan.json").write_text('{"layers": []}')
            events = []
            def stage(*args):
                events.append("objects")
                return {"example": "https://source.test"}
            def guarded(command, env, monitoring):
                events.append("import")
                filename = Path(env["HONUA_SAMPLE_IMPORT_URLS_FILE"])
                self.assertNotIn(root, filename.parents)
                self.assertEqual(0o600, filename.stat().st_mode & 0o777)
                return {}
            with patch.object(LOAD, "claim_generation", side_effect=lambda *args: events.append("claim")), \
                    patch.object(LOAD, "stage_objects", side_effect=stage), patch.object(LOAD, "run_guarded", side_effect=guarded):
                LOAD.run(dict(self.env))
            self.assertEqual(["claim", "objects", "import"], events)

    def test_nonstage_requires_baseline_before_claim(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                patch.object(LOAD, "configure", return_value=("private-bucket", "demo")), \
                patch.object(LOAD, "claim_generation") as claim:
            Path(directory, "shared-load-plan.json").write_text('{"layers": []}')
            for action in ["activate", "rollback"]:
                with self.assertRaises(ValueError):
                    LOAD.run({**self.env, "LOAD_ACTION": action})
            claim.assert_not_called()

    def test_rollback_passes_action_to_monitor_without_upload(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(LOAD, "ROOT", Path(directory)), \
                patch.object(LOAD, "configure", return_value=("private-bucket", "demo")), \
                patch.object(LOAD, "monitor") as monitor, patch.object(LOAD, "claim_generation"), \
                patch.object(LOAD, "stage_objects") as stage, patch.object(LOAD, "run_guarded", return_value={}):
            Path(directory, "shared-load-plan.json").write_text('{"layers": []}')
            Path(directory, "monitor-baseline.json").write_text('{"freeBytes": 4000000000}')
            LOAD.run({**self.env, "LOAD_ACTION": "rollback"})
            monitor.assert_called_once_with("demo", 4e9, "rollback")
            stage.assert_not_called()

    def test_workflow_has_protection_pins_and_failure_artifacts(self):
        text = Path(".github/workflows/load-demo-dataset.yml").read_text()
        for value in ["environment: demo", "group: demo-apply", "cancel-in-progress: false", "if: always()",
                      "retention-days: 90", "github.ref == 'refs/heads/trunk'", "persist-credentials: false"]:
            self.assertIn(value, text)
        for line in text.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[a-f0-9]{40}(?:\s|$)")
        prepare = text.split("  prepare:", 1)[1].split("  load:", 1)[0]
        self.assertNotIn("environment:", prepare)
        self.assertNotIn("id-token:", prepare)
        self.assertNotIn("GH_TOKEN:", prepare)
        self.assertNotIn("secrets.", text)
        self.assertLess(text.index("Verify samples revision"), text.index("Configure existing deployment role"))


if __name__ == "__main__":
    unittest.main()
