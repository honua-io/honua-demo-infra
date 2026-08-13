#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "stacks" / "aws" / "candidate-preflight" / "handler.py"
MANIFEST = HANDLER.with_name("classification.v1.json")
SECRET = "candidate-preflight-secret-sentinel"


class FakeConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeLambda:
    def __init__(
        self,
        pending,
        *,
        alias_post_drift=False,
        contract=False,
        mode=None,
        nested_lifecycle="skipped",
        observability_status="skipped",
        observability_ready=True,
        observability_failed=False,
        backup_hook="exact-non-contract",
        plan_available=True,
        upgrade_required=True,
        executed_but_not_discovered=None,
        plan_error=None,
        omit_deploy_backup_hook=False,
        omit_observability_backup_hook=False,
    ):
        self.pending = pending
        self.alias_post_drift = alias_post_drift
        self.contract = contract
        self.mode = mode
        self.nested_lifecycle = nested_lifecycle
        self.observability_status = observability_status
        self.observability_ready = observability_ready
        self.observability_failed = observability_failed
        self.backup_hook = backup_hook
        self.plan_available = plan_available
        self.upgrade_required = upgrade_required
        self.executed_but_not_discovered = executed_but_not_discovered or []
        self.plan_error = plan_error
        self.omit_deploy_backup_hook = omit_deploy_backup_hook
        self.omit_observability_backup_hook = omit_observability_backup_hook
        self.get_function_calls = []
        self.get_configuration_calls = []
        self.get_alias_calls = []
        self.invocations = []

    @staticmethod
    def configuration():
        return {
            "FunctionName": "honua-demo-demo-honua",
            "Version": "40",
            "RevisionId": "0326e209-4231-4acd-9bb4-d3cb89402db0",
            "PackageType": "Image",
            "Architectures": ["arm64"],
            "State": "Active",
            "LastUpdateStatus": "Successful",
            "Environment": {
                "Variables": {
                    "HONUA_SKIP_MIGRATIONS": "true",
                    "ControlPlane__DeployTargets__0__ArtifactReference": (
                        "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@"
                        "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
                    ),
                }
            },
        }

    def get_function(self, **kwargs):
        self.get_function_calls.append(kwargs)
        artifact = self.configuration()["Environment"]["Variables"][
            "ControlPlane__DeployTargets__0__ArtifactReference"
        ]
        return {
            "Configuration": self.configuration(),
            "Code": {"ImageUri": artifact, "ResolvedImageUri": artifact},
        }

    def get_function_configuration(self, **kwargs):
        self.get_configuration_calls.append(kwargs)
        return self.configuration()

    def get_alias(self, **kwargs):
        self.get_alias_calls.append(kwargs)
        revision = "4f73dd76-0294-44d3-8362-c6f8606f034e"
        if self.alias_post_drift and len(self.get_alias_calls) == 2:
            revision = "drifted"
        return {
            "Name": "live",
            "FunctionVersion": "39",
            "RevisionId": revision,
        }

    def _migration(self, *, nested=False):
        contract_scripts = [self.pending[0]] if self.contract else []
        backup_hook = self.backup_hook
        if backup_hook == "exact-non-contract":
            backup_hook = {
                "configured": False,
                "requiredForPendingSet": self.contract,
                "ranForPendingSet": False,
                "pendingContractScripts": contract_scripts,
            }
        result = {
            "planAvailable": self.plan_available,
            "upgradeRequired": self.upgrade_required,
            "pendingScripts": self.pending,
            "executedButNotDiscoveredScripts": self.executed_but_not_discovered,
            "planError": self.plan_error,
        }
        if not (
            (nested and self.omit_deploy_backup_hook)
            or (not nested and self.omit_observability_backup_hook)
        ):
            result["backupHook"] = backup_hook
        if nested:
            result["lifecycleStatus"] = self.nested_lifecycle
        else:
            result.update(
                {
                    "status": self.observability_status,
                    "isReady": self.observability_ready,
                    "isFailed": self.observability_failed,
                }
            )
        return result

    def invoke(self, **kwargs):
        event = json.loads(kwargs["Payload"].decode("utf-8"))
        self.invocations.append((kwargs, event))
        path = event["rawPath"]
        if self.mode == "timeout":
            raise TimeoutError("contains-sensitive-upstream-detail")
        if path == "/healthz/live":
            body = "Healthy"
        elif path == "/healthz/ready":
            body = "Ready"
        elif path == "/api/v1/admin/deploy/preflight":
            migration = self._migration(nested=True)
            body = json.dumps(
                {
                    "status": "blocked",
                    "readyForCoordinatedDeploy": False,
                    "readiness": {"isReady": True, "statusCode": 200},
                    "migration": migration,
                    "databaseCompatibility": {"isCompatible": True},
                    "platformRelease": {
                        "serving": [
                            {
                                "effectiveArtifactReference": (
                                    "585192672263.dkr.ecr.us-west-2.amazonaws.com/"
                                    "honua-server@sha256:67d96f75ec9220c7cc238e241888d5cf"
                                    "79d9587b8220aaa1bfcb4f0d6f4bd861"
                                )
                            }
                        ]
                    },
                }
            )
        else:
            body = json.dumps(self._migration())

        status = 401 if self.mode == "401" and "admin" in path else 200
        if self.mode == "non-200" and path == "/healthz/ready":
            status = 503
        if self.mode == "invalid-json" and path.endswith("/migrations"):
            body = "not-json-sensitive-looking-body"
        proxy = {"statusCode": status, "body": body, "isBase64Encoded": False}
        response = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Payload": BytesIO(json.dumps(proxy).encode("utf-8")),
        }
        if self.mode == "function-error":
            response["FunctionError"] = "Unhandled"
        return response


class FakeSecrets:
    def __init__(self):
        self.calls = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        return {"SecretString": SECRET}


def load_handler(lambda_client, secrets_client, client_calls):
    boto3 = types.ModuleType("boto3")

    def client(name, **kwargs):
        client_calls.append((name, kwargs))
        return lambda_client if name == "lambda" else secrets_client

    boto3.client = client
    botocore = types.ModuleType("botocore")
    botocore_config = types.ModuleType("botocore.config")
    botocore_config.Config = FakeConfig
    botocore.config = botocore_config
    with patch.dict(
        sys.modules,
        {"boto3": boto3, "botocore": botocore, "botocore.config": botocore_config},
    ):
        spec = importlib.util.spec_from_file_location("candidate_preflight_handler", HANDLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class CandidatePreflightHandlerTests(unittest.TestCase):
    def setUp(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.pending = [entry["name"] for entry in manifest["scripts"]]
        self.environment = {
            "ADMIN_PASSWORD_SECRET_ARN": (
                "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-admin"
            ),
            "EXPECTED_APP_FUNCTION_NAME": "honua-demo-demo-honua",
            "EXPECTED_ARCHITECTURE": "arm64",
            "EXPECTED_ARTIFACT_REFERENCE": (
                "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@"
                "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
            ),
            "EXPECTED_CANDIDATE_REVISION_ID": "0326e209-4231-4acd-9bb4-d3cb89402db0",
            "EXPECTED_CANDIDATE_VERSION": "40",
            "EXPECTED_IMAGE_DIGEST": (
                "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
            ),
            "EXPECTED_LIVE_ALIAS_NAME": "live",
            "EXPECTED_LIVE_REVISION_ID": "4f73dd76-0294-44d3-8362-c6f8606f034e",
            "EXPECTED_LIVE_VERSION": "39",
            "EXPECTED_PACKAGE_TYPE": "Image",
            "EXPECTED_SKIP_MIGRATIONS": "true",
            "EXPECTED_SOURCE_COMMIT": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
        }

    def execute(self, **fake_options):
        lambda_client = FakeLambda(self.pending.copy(), **fake_options)
        secrets_client = FakeSecrets()
        client_calls = []
        module = load_handler(lambda_client, secrets_client, client_calls)
        stdout, stderr = StringIO(), StringIO()
        with patch.dict(os.environ, self.environment, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
            result = module.handler({"operation": "candidate-preflight-v1"}, None)
        return result, lambda_client, secrets_client, client_calls, stdout.getvalue() + stderr.getvalue()

    def test_success_is_sanitized_and_invokes_only_qualified_candidate(self):
        result, lambda_client, secrets_client, _, output = self.execute()
        self.assertEqual("passed", result["status"])
        self.assertEqual("7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad", result["candidate"]["sourceCommit"])
        self.assertEqual("classification-manifest+resolved-image", result["candidate"]["provenance"])
        self.assertEqual(
            "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@"
            "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
            result["candidate"]["artifactReference"],
        )
        self.assertEqual(14, result["migration"]["pendingScriptCount"])
        self.assertNotIn(SECRET, json.dumps(result) + output)
        self.assertEqual(1, len(secrets_client.calls))
        self.assertEqual(2, len(lambda_client.get_function_calls))
        self.assertEqual(2, len(lambda_client.get_configuration_calls))
        self.assertEqual(2, len(lambda_client.get_alias_calls))
        self.assertEqual(4, len(lambda_client.invocations))

        for kwargs, event in lambda_client.invocations:
            self.assertEqual("honua-demo-demo-honua", kwargs["FunctionName"])
            self.assertEqual("40", kwargs["Qualifier"])
            self.assertEqual("RequestResponse", kwargs["InvocationType"])
            self.assertEqual("None", kwargs["LogType"])
            self.assertEqual("GET", event["requestContext"]["http"]["method"])

        by_path = {event["rawPath"]: event for _, event in lambda_client.invocations}
        self.assertNotIn("X-API-Key", by_path["/healthz/live"]["headers"])
        self.assertNotIn("X-API-Key", by_path["/healthz/ready"]["headers"])
        for path in (
            "/api/v1/admin/deploy/preflight",
            "/api/v1/admin/observability/migrations",
        ):
            self.assertEqual(SECRET, by_path[path]["headers"]["X-API-Key"])

    def test_extra_input_is_rejected_before_clients_or_secret_access(self):
        lambda_client = FakeLambda(self.pending)
        secrets_client = FakeSecrets()
        client_calls = []
        module = load_handler(lambda_client, secrets_client, client_calls)
        result = module.handler(
            {"operation": "candidate-preflight-v1", "candidate": "$LATEST"}, None
        )
        self.assertEqual("invalid-request", result["failure"])
        self.assertEqual([], client_calls)
        self.assertEqual([], secrets_client.calls)

    def test_alias_post_check_drift_fails_closed(self):
        result, *_ = self.execute(alias_post_drift=True)
        self.assertEqual("live-alias-revision-drift", result["failure"])

    def test_runtime_pending_set_must_match_manifest_exactly(self):
        lambda_client = FakeLambda(self.pending[:-1])
        secrets_client = FakeSecrets()
        module = load_handler(lambda_client, secrets_client, [])
        with patch.dict(os.environ, self.environment, clear=True):
            result = module.handler({"operation": "candidate-preflight-v1"}, None)
        self.assertEqual("migration-pending-set-drift", result["failure"])

    def test_contract_phase_branch_is_rejected(self):
        result, *_ = self.execute(contract=True)
        self.assertEqual("contract-phase-rejected", result["failure"])

    def test_null_backup_hook_is_accepted_only_after_every_expand_gate(self):
        result, *_ = self.execute(backup_hook=None)
        self.assertEqual("passed", result["status"])

        cases = (
            ({"nested_lifecycle": "succeeded"}, "migration-lifecycle-drift"),
            ({"plan_available": False}, "migration-plan-unavailable"),
            ({"upgrade_required": False}, "migration-upgrade-branch-drift"),
            ({"executed_but_not_discovered": [self.pending[0]]}, "migration-journal-drift"),
            ({"plan_error": "redacted"}, "migration-plan-error"),
            ({"observability_ready": False}, "migration-readiness-drift"),
            ({"observability_failed": True}, "migration-failure-state-drift"),
        )
        for options, code in cases:
            with self.subTest(options=options):
                result, *_ = self.execute(backup_hook=None, **options)
                self.assertEqual(code, result["failure"])

        lambda_client = FakeLambda(self.pending[:-1], backup_hook=None)
        module = load_handler(lambda_client, FakeSecrets(), [])
        with patch.dict(os.environ, self.environment, clear=True):
            result = module.handler({"operation": "candidate-preflight-v1"}, None)
        self.assertEqual("migration-pending-set-drift", result["failure"])

    def test_deploy_preflight_migration_may_omit_backup_hook(self):
        client = FakeLambda(self.pending, omit_deploy_backup_hook=True)
        self.assertNotIn("backupHook", client._migration(nested=True))
        self.assertIn("backupHook", client._migration(nested=False))
        result, *_ = self.execute(omit_deploy_backup_hook=True)
        self.assertEqual("passed", result["status"])

    def test_migration_observability_root_may_omit_backup_hook(self):
        client = FakeLambda(self.pending, omit_observability_backup_hook=True)
        self.assertIn("backupHook", client._migration(nested=True))
        self.assertNotIn("backupHook", client._migration(nested=False))
        result, *_ = self.execute(omit_observability_backup_hook=True)
        self.assertEqual("passed", result["status"])

    def test_non_null_backup_hook_remains_an_exact_non_contract_object(self):
        malformed = (
            "not-an-object",
            [],
            {},
            {"configured": False, "requiredForPendingSet": False, "ranForPendingSet": False},
            {
                "configured": "false",
                "requiredForPendingSet": False,
                "ranForPendingSet": False,
                "pendingContractScripts": [],
            },
            {
                "configured": False,
                "requiredForPendingSet": False,
                "ranForPendingSet": False,
                "pendingContractScripts": [],
                "extra": True,
            },
        )
        for backup_hook in malformed:
            with self.subTest(backup_hook=backup_hook):
                result, *_ = self.execute(backup_hook=backup_hook)
                self.assertEqual("migration-classification-missing", result["failure"])

        contract_values = (
            {
                "configured": True,
                "requiredForPendingSet": True,
                "ranForPendingSet": False,
                "pendingContractScripts": [self.pending[0]],
            },
            {
                "configured": True,
                "requiredForPendingSet": False,
                "ranForPendingSet": False,
                "pendingContractScripts": [self.pending[0]],
            },
        )
        for backup_hook in contract_values:
            with self.subTest(backup_hook=backup_hook):
                result, *_ = self.execute(backup_hook=backup_hook)
                self.assertEqual("contract-phase-rejected", result["failure"])

    def test_manifest_source_digest_and_reference_drift_fail_closed(self):
        module = load_handler(FakeLambda(self.pending), FakeSecrets(), [])
        for key, value in (
            ("sourceCommit", "0" * 40),
            ("imageDigest", "sha256:" + "0" * 64),
            ("artifactReference", "invalid"),
        ):
            original = module.IMMUTABLE[key]
            module.IMMUTABLE[key] = value
            try:
                with self.subTest(key=key), self.assertRaises(module.PreflightFailure) as failure:
                    module._load_classification_manifest()
                self.assertEqual("classification-candidate-drift", failure.exception.code)
            finally:
                module.IMMUTABLE[key] = original

    def test_candidate_artifact_and_resolved_digest_drift_fail_closed(self):
        module = load_handler(FakeLambda(self.pending), FakeSecrets(), [])
        manifest_candidate = json.loads(MANIFEST.read_text(encoding="utf-8"))["candidate"]

        class ArtifactDrift(FakeLambda):
            @staticmethod
            def configuration():
                value = FakeLambda.configuration()
                value["Environment"]["Variables"][
                    "ControlPlane__DeployTargets__0__ArtifactReference"
                ] = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:" + "0" * 64
                return value

        class ResolvedDrift(FakeLambda):
            def get_function(self, **kwargs):
                value = super().get_function(**kwargs)
                value["Code"]["ResolvedImageUri"] = (
                    "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:" + "0" * 64
                )
                return value

        for client, code in (
            (ArtifactDrift(self.pending), "candidate-artifact-reference-drift"),
            (ResolvedDrift(self.pending), "candidate-resolved-image-drift"),
        ):
            with self.subTest(code=code), self.assertRaises(module.PreflightFailure) as failure:
                module._candidate_fingerprint(client, manifest_candidate)
            self.assertEqual(code, failure.exception.code)

    def test_deploy_nested_migration_lifecycle_must_be_skipped(self):
        result, *_ = self.execute(nested_lifecycle="succeeded")
        self.assertEqual("migration-lifecycle-drift", result["failure"])

    def test_observability_lifecycle_must_be_skipped_ready_and_not_failed(self):
        cases = (
            ({"observability_status": "succeeded"}, "migration-lifecycle-drift"),
            ({"observability_ready": False}, "migration-readiness-drift"),
            ({"observability_failed": True}, "migration-failure-state-drift"),
        )
        for options, code in cases:
            with self.subTest(options=options):
                result, *_ = self.execute(**options)
                self.assertEqual(code, result["failure"])

    def test_transport_and_payload_failures_are_sanitized(self):
        expected = {
            "timeout": "candidate-invoke-timeout",
            "401": "candidate-http-401",
            "non-200": "candidate-http-non-200",
            "invalid-json": "candidate-body-json-invalid",
            "function-error": "candidate-function-error",
        }
        for mode, code in expected.items():
            with self.subTest(mode=mode):
                result, _, _, _, output = self.execute(mode=mode)
                serialized = json.dumps(result) + output
                self.assertEqual(code, result["failure"])
                self.assertNotIn(SECRET, serialized)
                self.assertNotIn("sensitive", serialized)


if __name__ == "__main__":
    unittest.main()
