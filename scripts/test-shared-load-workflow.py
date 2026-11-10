import importlib.util
import json
import os
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
        with patch.object(LOAD, "capture", side_effect=[json.dumps({"workflow_id": 1}), json.dumps(self.prior())]):
            LOAD.validate(self.env)

    def test_artifact_requires_same_workflow_repository_branch_and_event(self):
        for changes in [{"workflow_id": 2}, {"path": "other.yml"}, {"head_branch": "topic"},
                        {"event": "pull_request"}, {"status": "in_progress"}, {"repository": {"full_name": "other/repo"}}]:
            with self.subTest(changes=changes), patch.object(LOAD, "capture", side_effect=[
                    json.dumps({"workflow_id": 1}), json.dumps(self.prior(**changes))]):
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
            self.assertEqual(140, LOAD.monitor("demo", 4e9, enforce=False)["connections"])

    def test_generation_rejects_stale_receipts_before_mutating(self):
        def get_object(args, **kwargs):
            Path(args[-1]).write_text(json.dumps({"runId": "18", "samplesSha": "a" * 40}))
            return subprocess.CompletedProcess(args, 0, json.dumps({"ETag": '"etag"'}), "")
        with patch.object(LOAD.subprocess, "run", side_effect=get_object), patch.object(LOAD, "aws") as aws:
            with self.assertRaises(ValueError):
                LOAD.claim_generation("private-bucket", self.env)
            aws.assert_not_called()

    def test_generation_uses_conditional_write_and_saves_receipt(self):
        def get_object(args, **kwargs):
            Path(args[-1]).write_text(json.dumps({"runId": "19", "samplesSha": "a" * 40}))
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

    def test_monitor_failure_terminates_child_and_preserves_files(self):
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
                LOAD.run_guarded(["node"], {}, "demo", 4e9, True)
            self.assertTrue(child.terminated)

    def test_workflow_has_protection_pins_and_failure_artifacts(self):
        text = Path(".github/workflows/load-demo-dataset.yml").read_text()
        for value in ["environment: demo", "group: demo-apply", "cancel-in-progress: false", "if: always()",
                      "retention-days: 7", "github.ref == 'refs/heads/trunk'", "persist-credentials: false"]:
            self.assertIn(value, text)
        for line in text.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[a-f0-9]{40}(?:\s|$)")
        self.assertNotIn("secrets.", text)
        self.assertLess(text.index("Verify samples revision"), text.index("Configure existing deployment role"))


if __name__ == "__main__":
    unittest.main()
