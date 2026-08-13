#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSERTION = ROOT / "scripts" / "assert-candidate-preflight-postapply.py"
FIXTURE = ROOT / "scripts" / "fixtures" / "candidate-preflight" / "postapply-actionless-provider-readback.json"
STACK = ROOT / "stacks" / "aws-candidate-preflight"
SOURCE = ROOT / "stacks" / "aws" / "candidate-preflight"
RUNBOOK = ROOT / "runbook" / "candidate-preflight-v1.md"
SEALED_SHOW = Path.home() / ".honua-runtime-proof" / "candidate-preflight-3a00dfd36c298def8f8f49757dd56595d29097cb-plan" / "postapply-nochange.show.json"
SEALED_SHOW_SHA256 = "64b1a415dd65dcf0c8cfead10b6b46dd255d23dd67afc237712bb2ab1949214e"


def load_assertion():
    spec = importlib.util.spec_from_file_location("candidate_postapply_assertion", ASSERTION)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load candidate post-apply assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POSTAPPLY = load_assertion()


class CandidatePreflightPostapplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valid = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def assert_rejected(self, label: str, mutate) -> None:
        value = copy.deepcopy(self.valid)
        mutate(value)
        with self.subTest(label=label), self.assertRaises(RuntimeError):
            POSTAPPLY.assert_postapply(value)

    @staticmethod
    def change(value: dict, address: str) -> dict:
        return next(item for item in value["resource_changes"] if item["address"] == address)

    @staticmethod
    def drift(value: dict, address: str) -> dict:
        return next(item for item in value["resource_drift"] if item["address"] == address)

    def test_exact_actionless_provider_readback_passes(self) -> None:
        POSTAPPLY.assert_postapply(self.valid)
        without_drift = copy.deepcopy(self.valid)
        without_drift["resource_drift"] = []
        POSTAPPLY.assert_postapply(without_drift)

    def test_exact_sealed_postapply_show_passes_when_local_evidence_exists(self) -> None:
        if not SEALED_SHOW.is_file():
            self.skipTest("exact sealed post-apply evidence is intentionally local-only")
        self.assertEqual(SEALED_SHOW_SHA256, hashlib.sha256(SEALED_SHOW.read_bytes()).hexdigest())
        POSTAPPLY.assert_postapply(json.loads(SEALED_SHOW.read_text(encoding="utf-8")))

    def test_every_action_or_graph_mutation_fails_closed(self) -> None:
        mutations = {
            "applyable": lambda p: p.update(applyable=True),
            "incomplete": lambda p: p.update(complete=False),
            "errored": lambda p: p.update(errored=True),
            "terraform version": lambda p: p.update(terraform_version="1.15.7"),
            "plan format": lambda p: p.update(format_version="1.1"),
            "deferred": lambda p: p.update(deferred_changes=[{"reason": "fixture"}]),
            "actions": lambda p: p.update(actions=[{"address": "bad"}]),
            "action invocations": lambda p: p.update(action_invocations=[{"address": "bad"}]),
            "action triggers": lambda p: p.update(action_triggers=[{"address": "bad"}]),
            "missing resource": lambda p: p["resource_changes"].pop(),
            "extra resource": lambda p: p["resource_changes"].append({"address": "aws_db_instance.bad", "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {}, "before_sensitive": {}, "after_sensitive": {}}}),
            "resource action": lambda p: self.change(p, "aws_lambda_function.candidate_preflight")["change"].update(actions=["update"]),
            "action reason": lambda p: self.change(p, "aws_lambda_function.candidate_preflight").update(action_reason="replace_because_tainted"),
            "no-op value difference": lambda p: self.change(p, "aws_lambda_function.candidate_preflight")["change"]["after"].update(memory_size=256),
            "unknown value": lambda p: self.change(p, "aws_lambda_function.candidate_preflight")["change"].update(after_unknown={"arn": True}),
            "sensitivity difference": lambda p: self.change(p, "aws_lambda_function.candidate_preflight")["change"].update(after_sensitive={"layers": [True]}),
            "missing output": lambda p: p["output_changes"].pop("candidate_preflight_version"),
            "extra output": lambda p: p["output_changes"].update(other=copy.deepcopy(p["output_changes"]["candidate_preflight_version"])),
            "output action": lambda p: p["output_changes"]["candidate_preflight_version"].update(actions=["update"]),
            "output difference": lambda p: p["output_changes"]["candidate_preflight_version"].update(after="2"),
            "provider": lambda p: p["configuration"]["provider_config"]["aws"]["expressions"]["region"].update(constant_value="us-east-1"),
            "configuration resource": lambda p: p["configuration"]["root_module"]["resources"].append({"address": "aws_db_instance.bad", "mode": "managed", "provider_config_key": "aws"}),
            "child module": lambda p: p["configuration"]["root_module"].update(module_calls={"bad": {}}),
            "failed check": lambda p: p["checks"][0].update(status="fail"),
            "missing check": lambda p: p["checks"].pop(),
        }
        for label, mutation in mutations.items():
            self.assert_rejected(label, mutation)

    def test_every_provider_readback_mutation_fails_closed(self) -> None:
        policy_address = "aws_iam_role.candidate_preflight"
        lambda_address = "aws_lambda_function.candidate_preflight"
        mutations = {
            "unreviewed drift": lambda p: p["resource_drift"].append({"address": "aws_s3_bucket.bad", "change": {"actions": ["update"], "before": {}, "after": {}}}),
            "duplicate drift": lambda p: p["resource_drift"].append(copy.deepcopy(self.drift(p, policy_address))),
            "drift action": lambda p: self.drift(p, policy_address)["change"].update(actions=["delete", "create"]),
            "drift action reason": lambda p: self.drift(p, policy_address).update(action_reason="update_because_drift"),
            "drift unknown": lambda p: self.drift(p, policy_address)["change"].update(after_unknown={"inline_policy": True}),
            "policy before nonempty": lambda p: self.drift(p, policy_address)["change"]["before"].update(inline_policy=[{"name": "bad", "policy": "{}"}]),
            "policy missing": lambda p: self.drift(p, policy_address)["change"]["after"].update(inline_policy=[]),
            "policy extra": lambda p: self.drift(p, policy_address)["change"]["after"]["inline_policy"].append(copy.deepcopy(self.drift(p, policy_address)["change"]["after"]["inline_policy"][0])),
            "policy name": lambda p: self.drift(p, policy_address)["change"]["after"]["inline_policy"][0].update(name="other"),
            "policy semantic": lambda p: self.drift(p, policy_address)["change"]["after"]["inline_policy"][0].update(policy="{\"Version\":\"2012-10-17\",\"Statement\":[]}"),
            "standalone policy semantic": lambda p: self.change(p, "aws_iam_role_policy.candidate_preflight")["change"]["after"].update(policy="{\"Version\":\"2012-10-17\",\"Statement\":[]}"),
            "policy other field": lambda p: self.drift(p, policy_address)["change"]["after"].update(description="changed"),
            "layers before": lambda p: self.drift(p, lambda_address)["change"]["before"].update(layers=[]),
            "layers after": lambda p: self.drift(p, lambda_address)["change"]["after"].update(layers=["arn:bad"]),
            "layers other field": lambda p: self.drift(p, lambda_address)["change"]["after"].update(memory_size=256),
        }
        for label, mutation in mutations.items():
            self.assert_rejected(label, mutation)

    def test_every_exact_source_mutation_fails_closed(self) -> None:
        files = (
            ("aws-candidate-preflight", "main.tf"),
            ("aws-candidate-preflight", "versions.tf"),
            ("aws-candidate-preflight", ".terraform.lock.hcl"),
            ("aws/candidate-preflight", "handler.py"),
            ("aws/candidate-preflight", "classification.v1.json"),
        )
        for relative, name in files:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="candidate-postapply-source-") as temporary:
                stacks = Path(temporary) / "stacks"
                shutil.copytree(STACK, stacks / "aws-candidate-preflight")
                shutil.copytree(SOURCE, stacks / "aws" / "candidate-preflight")
                target = stacks / relative / name
                target.write_bytes(target.read_bytes() + b"\n")
                with self.assertRaises(RuntimeError):
                    POSTAPPLY.assert_postapply(self.valid, stacks / "aws-candidate-preflight")

    def test_exact_source_identity_is_line_ending_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-postapply-line-endings-") as temporary:
            stacks = Path(temporary) / "stacks"
            shutil.copytree(STACK, stacks / "aws-candidate-preflight")
            shutil.copytree(SOURCE, stacks / "aws" / "candidate-preflight")
            paths = (
                stacks / "aws-candidate-preflight" / "main.tf",
                stacks / "aws-candidate-preflight" / "versions.tf",
                stacks / "aws-candidate-preflight" / ".terraform.lock.hcl",
                stacks / "aws" / "candidate-preflight" / "handler.py",
                stacks / "aws" / "candidate-preflight" / "classification.v1.json",
            )
            for path in paths:
                path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
            POSTAPPLY.assert_postapply(self.valid, stacks / "aws-candidate-preflight")
            for path in paths:
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            POSTAPPLY.assert_postapply(self.valid, stacks / "aws-candidate-preflight")

    def test_runbook_preserves_apply_actionless_boundary(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "apply-actionless provider readback",
            "assert-candidate-preflight-postapply.py",
            "assert-candidate-preflight-runtime.py verify",
            "terraform apply -refresh-only",
            "terraform state",
            "configuration churn",
        ):
            self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
