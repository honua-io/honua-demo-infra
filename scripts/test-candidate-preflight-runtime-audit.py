#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-candidate-preflight-runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("runtime_audit", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load runtime audit")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load_module()
SECRET = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-Ab12Cd"


class RuntimeAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="candidate-runtime-audit-")
        self.root = Path(self.temporary.name)
        version = "1"
        env = {
            "ADMIN_PASSWORD_SECRET_ARN": SECRET,
            "EXPECTED_APP_FUNCTION_NAME": "honua-demo-demo-honua",
            "EXPECTED_ARCHITECTURE": "arm64",
            "EXPECTED_ARTIFACT_REFERENCE": f"585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@{AUDIT.IMAGE_DIGEST}",
            "EXPECTED_CANDIDATE_REVISION_ID": "0326e209-4231-4acd-9bb4-d3cb89402db0",
            "EXPECTED_CANDIDATE_VERSION": "40",
            "EXPECTED_IMAGE_DIGEST": AUDIT.IMAGE_DIGEST,
            "EXPECTED_LIVE_ALIAS_NAME": "live",
            "EXPECTED_LIVE_REVISION_ID": "4f73dd76-0294-44d3-8362-c6f8606f034e",
            "EXPECTED_LIVE_VERSION": "39",
            "EXPECTED_PACKAGE_TYPE": "Image",
            "EXPECTED_SKIP_MIGRATIONS": "true",
            "EXPECTED_SOURCE_COMMIT": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
            "SOURCE_CLASSIFICATION_SHA256": AUDIT.CLASSIFICATION_SHA256,
            "SOURCE_HANDLER_SHA256": AUDIT.HANDLER_SHA256,
        }
        self.documents = {
            "function": {
                "Configuration": {
                    "FunctionName": AUDIT.NAME,
                    "FunctionArn": f"arn:aws:lambda:us-west-2:585192672263:function:{AUDIT.NAME}:{version}",
                    "Runtime": "python3.13",
                    "Role": AUDIT.ROLE_ARN,
                    "Handler": "handler.handler",
                    "CodeSha256": AUDIT.CODE_SHA256,
                    "Version": version,
                    "RevisionId": "0326e209-4231-4acd-9bb4-d3cb89402db1",
                    "PackageType": "Zip",
                    "Architectures": ["arm64"],
                    "Timeout": 120,
                    "MemorySize": 128,
                    "Environment": {"Variables": env},
                    "Layers": [],
                    "VpcConfig": {"SubnetIds": [], "SecurityGroupIds": [], "VpcId": ""},
                    "FileSystemConfigs": [],
                }
            },
            "concurrency": {"ReservedConcurrentExecutions": 1},
            "role": {
                "Role": {
                    "RoleName": AUDIT.ROLE_NAME,
                    "Arn": AUDIT.ROLE_ARN,
                    "Path": "/",
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
                    },
                }
            },
            "role_policy": {"RoleName": AUDIT.ROLE_NAME, "PolicyName": AUDIT.POLICY_NAME, "PolicyDocument": AUDIT.expected_policy(SECRET)},
            "attached_policies": {"AttachedPolicies": [], "IsTruncated": False},
            "inline_policies": {"PolicyNames": [AUDIT.POLICY_NAME], "IsTruncated": False},
            "plan_receipt": {"schema": "fixture"},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, documents):
        paths = {}
        for name, document in documents.items():
            path = self.root / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            paths[name] = path
        return SimpleNamespace(**paths, merged_sha="a" * 40)

    def test_exact_qualified_runtime_passes(self):
        receipt = AUDIT.audit(self.args(self.documents))
        self.assertEqual("1", receipt["version"])
        self.assertTrue(receipt["qualifiedArn"].endswith(":1"))

    def test_runtime_and_role_bypasses_fail_closed(self):
        mutations = {
            "unqualified ARN": lambda d: d["function"]["Configuration"].update(FunctionArn=f"arn:aws:lambda:us-west-2:585192672263:function:{AUDIT.NAME}"),
            "latest version": lambda d: d["function"]["Configuration"].update(Version="$LATEST"),
            "code": lambda d: d["function"]["Configuration"].update(CodeSha256="wrong"),
            "revision": lambda d: d["function"]["Configuration"].update(RevisionId="wrong"),
            "environment": lambda d: d["function"]["Configuration"]["Environment"]["Variables"].update(EXPECTED_LIVE_VERSION="40"),
            "layer": lambda d: d["function"]["Configuration"].update(Layers=[{"Arn": "arn:bad"}]),
            "VPC": lambda d: d["function"]["Configuration"]["VpcConfig"].update(SubnetIds=["subnet-bad"]),
            "filesystem": lambda d: d["function"]["Configuration"].update(FileSystemConfigs=[{"Arn": "arn:bad"}]),
            "DLQ": lambda d: d["function"]["Configuration"].update(DeadLetterConfig={"TargetArn": "arn:bad"}),
            "concurrency": lambda d: d["concurrency"].update(ReservedConcurrentExecutions=2),
            "managed policy": lambda d: d["attached_policies"].update(AttachedPolicies=[{"PolicyArn": "arn:bad"}]),
            "inline policy set": lambda d: d["inline_policies"].update(PolicyNames=[AUDIT.POLICY_NAME, "bad"]),
            "widened policy": lambda d: d["role_policy"]["PolicyDocument"]["Statement"][0].update(Resource=["*"]),
        }
        for label, mutation in mutations.items():
            documents = copy.deepcopy(self.documents)
            mutation(documents)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                AUDIT.audit(self.args(documents))

    def test_receipt_verification_rejects_post_invoke_drift(self):
        args = self.args(self.documents)
        receipt = AUDIT.audit(args)
        changed = copy.deepcopy(self.documents)
        changed["function"]["Configuration"]["RevisionId"] = "1326e209-4231-4acd-9bb4-d3cb89402db1"
        self.assertNotEqual(receipt, AUDIT.audit(self.args(changed)))


if __name__ == "__main__":
    unittest.main()
