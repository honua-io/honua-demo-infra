#!/usr/bin/env python3
"""Tests for scripts/plan-report.py and scripts/drift-stacks.py (no AWS, no Terraform)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = load("plan-report")
stacks = load("drift-stacks")

PARAM_BEFORE = [{"apply_method": "pending-reboot", "name": "rds.force_ssl", "value": "1"}]
PARAM_AFTER = [{"apply_method": "immediate", "name": "rds.force_ssl", "value": "1"}]
PARAM_ADDRESS = "module.honua.module.rds[0].module.db_parameter_group.aws_db_parameter_group.this[0]"

STANDING = [
    {
        "address": PARAM_ADDRESS,
        "actions": ["update"],
        "attribute": "parameter",
        "before": PARAM_BEFORE,
        "after": PARAM_AFTER,
        "issue": "https://github.com/honua-io/honua-iac/issues/0",
    }
]


def change(address, actions, before, after, before_sensitive=None, after_sensitive=None, after_unknown=None):
    return {
        "address": address,
        "mode": "managed",
        "type": address.split(".")[-2] if "." in address else address,
        "change": {
            "actions": actions,
            "before": before,
            "after": after,
            "before_sensitive": before_sensitive or {},
            "after_sensitive": after_sensitive or {},
            "after_unknown": after_unknown or {},
        },
    }


def plan(*changes, drift=(), outputs=None):
    return {
        "format_version": "1.2",
        "resource_changes": list(changes),
        "resource_drift": list(drift),
        "output_changes": outputs or {},
    }


ALIAS_DRIFT = change(
    "module.honua.aws_lambda_alias.live",
    ["update"],
    {"name": "live", "function_version": "52"},
    {"name": "live", "function_version": "53"},
)


class ClassifyTests(unittest.TestCase):
    def test_empty_plan_is_clean(self):
        result = report.classify(plan(change("aws_s3_bucket.b", ["no-op"], {"a": 1}, {"a": 1})), [])
        self.assertEqual(result.status, "clean")
        self.assertEqual(result.changes, [])

    def test_reads_alone_are_clean(self):
        result = report.classify(plan(change("data.archive_file.z", ["read"], None, {"a": 1})), [])
        self.assertEqual(result.status, "clean")

    def test_hand_moved_alias_is_drift(self):
        result = report.classify(plan(ALIAS_DRIFT), STANDING)
        self.assertEqual(result.status, "drift")
        self.assertEqual([c["address"] for c in result.changes], ["module.honua.aws_lambda_alias.live"])

    def test_output_change_is_drift(self):
        result = report.classify(plan(outputs={"url": {"actions": ["update"], "before": "a", "after": "b"}}), [])
        self.assertEqual(result.status, "drift")

    def test_exact_standing_entry_is_tolerated_and_reported(self):
        result = report.classify(plan(change(PARAM_ADDRESS, ["update"], {"name": "g", "parameter": PARAM_BEFORE}, {"name": "g", "parameter": PARAM_AFTER})), STANDING)
        self.assertEqual(result.status, "clean")
        self.assertEqual(len(result.standing), 1)

    def test_standing_entry_does_not_hide_a_second_attribute(self):
        result = report.classify(
            plan(change(PARAM_ADDRESS, ["update"], {"name": "g", "parameter": PARAM_BEFORE}, {"name": "h", "parameter": PARAM_AFTER})),
            STANDING,
        )
        self.assertEqual(result.status, "drift")

    def test_standing_entry_does_not_hide_a_value_change(self):
        after = [{"apply_method": "immediate", "name": "rds.force_ssl", "value": "0"}]
        result = report.classify(plan(change(PARAM_ADDRESS, ["update"], {"parameter": PARAM_BEFORE}, {"parameter": after})), STANDING)
        self.assertEqual(result.status, "drift")

    def test_standing_entry_does_not_hide_a_replace(self):
        result = report.classify(
            plan(change(PARAM_ADDRESS, ["delete", "create"], {"parameter": PARAM_BEFORE}, {"parameter": PARAM_AFTER})), STANDING
        )
        self.assertEqual(result.status, "drift")

    def test_standing_entry_must_name_an_issue(self):
        with self.assertRaises(SystemExit):
            report.validate_standing([dict(STANDING[0], issue="")])


class RenderTests(unittest.TestCase):
    def test_sensitive_values_never_render(self):
        secret = change(
            "module.honua.aws_secretsmanager_secret_version.admin_password",
            ["delete", "create"],
            {"secret_string": "hunter2-before", "arn": "x"},
            {"secret_string": "hunter2-after", "arn": "x"},
            before_sensitive={"secret_string": True},
            after_sensitive={"secret_string": True},
        )
        env = change(
            "module.honua.aws_lambda_function.this",
            ["update"],
            {"environment": [{"variables": {"K": "old-secret-ref"}}]},
            {"environment": [{"variables": {"K": "new-secret-ref"}}]},
            before_sensitive={"environment": [{"variables": True}]},
            after_sensitive={"environment": [{"variables": True}]},
        )
        text = report.render(report.classify(plan(secret, env), []), title="t")
        for value in ("hunter2-before", "hunter2-after", "old-secret-ref", "new-secret-ref"):
            self.assertNotIn(value, text)
        self.assertIn("(sensitive)", text)
        self.assertIn("admin_password", text)

    def test_render_shows_attribute_values(self):
        text = report.render(report.classify(plan(ALIAS_DRIFT), []), title="t")
        self.assertIn("function_version", text)
        self.assertIn('"52"', text)
        self.assertIn('"53"', text)

    def test_unknown_after_values(self):
        c = change("aws_lambda_function.f", ["update"], {"source_code_hash": "a"}, {"source_code_hash": None}, after_unknown={"source_code_hash": True})
        text = report.render(report.classify(plan(c), []), title="t")
        self.assertIn("(known after apply)", text)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "clean.json"
            dirty = Path(tmp) / "dirty.json"
            clean.write_text(json.dumps(plan()))
            dirty.write_text(json.dumps(plan(ALIAS_DRIFT)))
            script = str(ROOT / "scripts" / "plan-report.py")
            ok = subprocess.run([sys.executable, script, "--plan", str(clean), "--title", "x", "--out", str(Path(tmp) / "a.md")])
            bad = subprocess.run([sys.executable, script, "--plan", str(dirty), "--title", "x", "--out", str(Path(tmp) / "b.md")])
            self.assertEqual(ok.returncode, 0)
            self.assertEqual(bad.returncode, 2)


class StackManifestTests(unittest.TestCase):
    def test_every_stack_directory_is_covered(self):
        manifest = stacks.load_manifest()
        self.assertEqual(stacks.uncovered(manifest), [])
        self.assertEqual(stacks.unknown(manifest), [])

    def test_a_new_stack_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "aws").mkdir()
            (root / "aws" / "main.tf").write_text("")
            (root / "aws-new").mkdir()
            (root / "aws-new" / "main.tf").write_text("")
            self.assertEqual(stacks.uncovered({"stacks": {"aws": {}}}, root), ["aws-new"])

    def test_matrix_lists_every_stack(self):
        manifest = stacks.load_manifest()
        names = [entry["stack"] for entry in stacks.matrix(manifest)]
        self.assertEqual(sorted(names), sorted(manifest["stacks"]))

    def test_standing_entries_are_valid(self):
        for name, entry in stacks.load_manifest()["stacks"].items():
            report.validate_standing(entry.get("standing", []))


class BootstrapLockTests(unittest.TestCase):
    def test_scramp_stays_below_1_5_for_pg8000_1_31_2(self):
        # honua-demo-infra#105: scramp 1.5.0 removed make_channel_binding, which pg8000 1.31.2 calls.
        lock = json.loads((ROOT / "stacks" / "aws" / "postgis-bootstrap" / "requirements.lock.json").read_text())
        versions = {wheel["name"]: wheel["version"] for wheel in lock["wheels"]}
        self.assertEqual(versions["pg8000"], "1.31.2")
        self.assertEqual(versions["scramp"], "1.4.18")


if __name__ == "__main__":
    unittest.main()
