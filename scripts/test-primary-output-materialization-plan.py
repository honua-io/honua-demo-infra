#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSERTION = ROOT / "scripts" / "assert-primary-output-materialization-plan.py"


def load_assertion_module():
    spec = importlib.util.spec_from_file_location("primary_output_assertion", ASSERTION)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load primary output assertion")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSERTION_MODULE = load_assertion_module()
ARN = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd"


def valid_plan():
    return {
        "complete": True,
        "errored": False,
        "resource_drift": [],
        "deferred_changes": [],
        "resource_changes": [
            {"address": "module.honua.aws_lambda_function.this", "change": {"actions": ["no-op"]}}
        ],
        "output_changes": {
            "lambda_function_name": {"actions": ["no-op"]},
            "admin_password_secret_arn": {
                "actions": ["create"],
                "before": None,
                "after": ARN,
                "after_unknown": False,
                "after_sensitive": False,
            },
        },
        "planned_values": {
            "outputs": {"admin_password_secret_arn": {"value": ARN, "sensitive": False}}
        },
        "configuration": {
            "root_module": {
                "outputs": {
                    "admin_password_secret_arn": {
                        "expression": {
                            "references": ["module.honua.admin_password_secret_arn", "module.honua"]
                        }
                    }
                }
            }
        },
    }


class PrimaryOutputMaterializationPlanTests(unittest.TestCase):
    def test_exact_state_only_plan_passes(self):
        ASSERTION_MODULE.assert_plan(valid_plan())

    def test_every_bypass_fails_closed(self):
        mutations = {
            "errored": lambda plan: plan.update(errored=True),
            "incomplete": lambda plan: plan.update(complete=False),
            "drift": lambda plan: plan.update(resource_drift=[{"address": "bad"}]),
            "deferred": lambda plan: plan.update(deferred_changes=[{"reason": "bad"}]),
            "resource action": lambda plan: plan["resource_changes"][0]["change"].update(actions=["update"]),
            "action reason": lambda plan: plan["resource_changes"][0].update(action_reason="bad"),
            "extra output": lambda plan: plan["output_changes"].update(bad={"actions": ["create"]}),
            "output update": lambda plan: plan["output_changes"]["admin_password_secret_arn"].update(actions=["update"]),
            "output unknown": lambda plan: plan["output_changes"]["admin_password_secret_arn"].update(after_unknown=True),
            "output sensitive": lambda plan: plan["output_changes"]["admin_password_secret_arn"].update(after_sensitive=True),
            "wrong account": lambda plan: plan["output_changes"]["admin_password_secret_arn"].update(after=ARN.replace("585192672263", "000000000000")),
            "wrong secret": lambda plan: plan["output_changes"]["admin_password_secret_arn"].update(after=ARN.replace("admin-password", "other")),
            "planned disagreement": lambda plan: plan["planned_values"]["outputs"]["admin_password_secret_arn"].update(value="wrong"),
            "planned sensitive": lambda plan: plan["planned_values"]["outputs"]["admin_password_secret_arn"].update(sensitive=True),
            "provenance": lambda plan: plan["configuration"]["root_module"]["outputs"]["admin_password_secret_arn"]["expression"].update(references=["local.copied_arn"]),
        }
        for label, mutation in mutations.items():
            candidate = copy.deepcopy(valid_plan())
            mutation(candidate)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                ASSERTION_MODULE.assert_plan(candidate)


if __name__ == "__main__":
    unittest.main()
