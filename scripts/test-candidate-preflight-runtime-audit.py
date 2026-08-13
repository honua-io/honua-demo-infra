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
                    "RevisionId": AUDIT.HELPER_REVISION_ID,
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
            "plan_receipt": {
                "schema": AUDIT.PLAN_RECEIPT_SCHEMA,
                "mergedSha": AUDIT.GOVERNANCE.DEPLOYMENT_SHA,
                "terraformVersion": AUDIT.TERRAFORM_VERSION,
                "planFormatVersion": AUDIT.PLAN_FORMAT_VERSION,
                "artifacts": {
                    "savedPlanSha256": "1" * 64,
                    "showJsonSha256": "2" * 64,
                    "archiveSha256": AUDIT.ARCHIVE_SHA256,
                },
                "sourceSha256": AUDIT.SOURCE_HASHES,
            },
            "governance_receipt": {
                "schema": AUDIT.GOVERNANCE.SCHEMA,
                "governanceSha": "a" * 40,
                "deploymentSha": AUDIT.GOVERNANCE.DEPLOYMENT_SHA,
                "historicalDeploymentReceiptSha256": AUDIT.GOVERNANCE.HISTORICAL_DEPLOYMENT_RECEIPT_SHA256,
                "sourceSha256": AUDIT.GOVERNANCE.source_hashes(),
                "operatorContract": AUDIT.GOVERNANCE.OPERATOR_CONTRACT,
            },
            "historical_deployment_receipt": AUDIT.GOVERNANCE.HISTORICAL_DEPLOYMENT_RECEIPT,
            "ecr_evidence": {
                "schema": AUDIT.ECR_EVIDENCE_SCHEMA,
                "registryId": AUDIT.ACCOUNT,
                "region": AUDIT.REGION,
                "repositoryName": "honua-server",
                "imageDigest": AUDIT.IMAGE_DIGEST,
                "manifestMediaType": "application/vnd.oci.image.manifest.v1+json",
                "manifestSha256": AUDIT.IMAGE_DIGEST,
                "config": {
                    "digest": "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f",
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "size": 6052,
                    "sha256": "sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f",
                    "architecture": "arm64",
                    "os": "linux",
                    "entrypoint": ["/var/task/Honua.Server"],
                    "cmd": None,
                    "workingDir": "/var/task",
                    "nativeAot": "native-aot",
                    "runtimeEntrypoint": "/var/task/Honua.Server",
                    "ociRevision": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
                    "source": "https://github.com/honua-io/honua-server",
                    "honuaGitSha": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
                },
            },
        }

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, documents):
        paths = {}
        for name, document in documents.items():
            path = self.root / f"{name}.json"
            if name == "historical_deployment_receipt":
                path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            else:
                path.write_text(json.dumps(document), encoding="utf-8")
            paths[name] = path
        return SimpleNamespace(
            **paths,
            governance_sha="a" * 40,
            deployment_sha=AUDIT.GOVERNANCE.DEPLOYMENT_SHA,
        )

    def test_exact_qualified_runtime_passes(self):
        receipt = AUDIT.audit(self.args(self.documents))
        self.assertEqual("1", receipt["version"])
        self.assertTrue(receipt["qualifiedArn"].endswith(":1"))

    def test_runtime_and_role_bypasses_fail_closed(self):
        mutations = {
            "unqualified ARN": lambda d: d["function"]["Configuration"].update(FunctionArn=f"arn:aws:lambda:us-west-2:585192672263:function:{AUDIT.NAME}"),
            "latest version": lambda d: d["function"]["Configuration"].update(Version="$LATEST"),
            "later version": lambda d: d["function"]["Configuration"].update(Version="2", FunctionArn=f"arn:aws:lambda:us-west-2:585192672263:function:{AUDIT.NAME}:2"),
            "code": lambda d: d["function"]["Configuration"].update(CodeSha256="wrong"),
            "revision": lambda d: d["function"]["Configuration"].update(RevisionId="wrong"),
            "environment": lambda d: d["function"]["Configuration"]["Environment"]["Variables"].update(EXPECTED_LIVE_VERSION="40"),
            "layer": lambda d: d["function"]["Configuration"].update(Layers=[{"Arn": "arn:bad"}]),
            "VPC": lambda d: d["function"]["Configuration"]["VpcConfig"].update(SubnetIds=["subnet-bad"]),
            "filesystem": lambda d: d["function"]["Configuration"].update(FileSystemConfigs=[{"Arn": "arn:bad"}]),
            "DLQ": lambda d: d["function"]["Configuration"].update(DeadLetterConfig={"TargetArn": "arn:bad"}),
            "concurrency": lambda d: d["concurrency"].update(ReservedConcurrentExecutions=2),
            "managed policy": lambda d: d["attached_policies"].update(AttachedPolicies=[{"PolicyArn": "arn:bad"}]),
            "missing attached pagination": lambda d: d["attached_policies"].pop("IsTruncated"),
            "truncated attached pagination": lambda d: d["attached_policies"].update(IsTruncated=True),
            "inline policy set": lambda d: d["inline_policies"].update(PolicyNames=[AUDIT.POLICY_NAME, "bad"]),
            "missing inline pagination": lambda d: d["inline_policies"].pop("IsTruncated"),
            "truncated inline pagination": lambda d: d["inline_policies"].update(IsTruncated=True),
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
        self.assertEqual(AUDIT.HELPER_REVISION_ID, receipt["revisionId"])
        with self.assertRaises(RuntimeError):
            AUDIT.audit(self.args(changed))

    def test_plan_receipt_mutations_fail_closed(self):
        mutations = {
            "root extra": lambda r: r.update(extra=True),
            "schema": lambda r: r.update(schema="wrong"),
            "deployment SHA": lambda r: r.update(mergedSha="b" * 40),
            "Terraform version": lambda r: r.update(terraformVersion="1.15.9"),
            "format version": lambda r: r.update(planFormatVersion="1.3"),
            "artifact extra": lambda r: r["artifacts"].update(extra="0" * 64),
            "saved plan hash": lambda r: r["artifacts"].update(savedPlanSha256="bad"),
            "show hash": lambda r: r["artifacts"].update(showJsonSha256="bad"),
            "ZIP hash": lambda r: r["artifacts"].update(archiveSha256="0" * 64),
            "source extra": lambda r: r["sourceSha256"].update(**{"sensitive-output": "0" * 64}),
            "handler hash": lambda r: r["sourceSha256"].update(**{"handler.py": "0" * 64}),
            "classification hash": lambda r: r["sourceSha256"].update(**{"classification.v1.json": "0" * 64}),
        }
        for label, mutation in mutations.items():
            documents = copy.deepcopy(self.documents)
            mutation(documents["plan_receipt"])
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                AUDIT.audit(self.args(documents))

    def test_deployment_receipt_mutations_fail_closed(self):
        receipt = AUDIT.audit(self.args(self.documents))
        mutations = {
            "root extra": lambda r: r.update(extra=True),
            "schema": lambda r: r.update(schema="wrong"),
            "governance SHA": lambda r: r.update(governanceSha="b" * 40),
            "deployment SHA": lambda r: r.update(deploymentSha="b" * 40),
            "governance receipt hash": lambda r: r.update(governanceReceiptSha256="bad"),
            "historical receipt hash": lambda r: r.update(historicalDeploymentReceiptSha256="bad"),
            "plan receipt hash": lambda r: r.update(planReceiptSha256="bad"),
            "qualified ARN": lambda r: r.update(qualifiedArn=r["qualifiedArn"].rsplit(":", 1)[0]),
            "version": lambda r: r.update(version="$LATEST"),
            "revision": lambda r: r.update(revisionId="wrong"),
            "code hash": lambda r: r.update(codeSha256="wrong"),
            "role": lambda r: r.update(roleArn="arn:aws:iam::585192672263:role/other"),
            "policy": lambda r: r.update(policyName="other"),
            "secret": lambda r: r.update(secretArn="arn:aws:secretsmanager:us-west-2:585192672263:secret:other-Ab12Cd"),
            "ECR evidence hash": lambda r: r.update(ecrEvidenceSha256="bad"),
        }
        for label, mutation in mutations.items():
            changed = copy.deepcopy(receipt)
            mutation(changed)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                AUDIT.validate_deployment_receipt(
                    changed,
                    "a" * 40,
                    AUDIT.GOVERNANCE.DEPLOYMENT_SHA,
                    AUDIT.sha256(self.args(self.documents).governance_receipt),
                )

    def test_governance_cross_binding_mutations_fail_closed(self):
        mutations = {
            "wrong governance": lambda d: d["governance_receipt"].update(governanceSha="b" * 40),
            "wrong deployment": lambda d: d["governance_receipt"].update(deploymentSha="b" * 40),
            "wrong controls": lambda d: d["governance_receipt"]["operatorContract"].update(awsMaxAttempts=2),
            "wrong source": lambda d: d["governance_receipt"]["sourceSha256"].update(**{AUDIT.GOVERNANCE.CONTROL_PATHS[0]: "0" * 64}),
            "wrong historical receipt": lambda d: d["historical_deployment_receipt"].update(version="2"),
        }
        for label, mutation in mutations.items():
            documents = copy.deepcopy(self.documents)
            mutation(documents)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                AUDIT.audit(self.args(documents))

    def test_ecr_evidence_mutations_fail_closed(self):
        mutations = {
            "missing proof": lambda d: d.pop("ecr_evidence"),
            "wrong digest": lambda d: d["ecr_evidence"].update(imageDigest="sha256:" + "0" * 64),
            "wrong architecture": lambda d: d["ecr_evidence"]["config"].update(architecture="amd64"),
            "wrong native AOT": lambda d: d["ecr_evidence"]["config"].update(nativeAot="framework-dependent"),
            "wrong OCI revision": lambda d: d["ecr_evidence"]["config"].update(ociRevision="0" * 40),
            "wrong HONUA_GIT_SHA": lambda d: d["ecr_evidence"]["config"].update(honuaGitSha="0" * 40),
        }
        for label, mutation in mutations.items():
            documents = copy.deepcopy(self.documents)
            mutation(documents)
            with self.subTest(label=label), self.assertRaises((RuntimeError, TypeError, AttributeError)):
                AUDIT.audit(self.args(documents))


if __name__ == "__main__":
    unittest.main()
