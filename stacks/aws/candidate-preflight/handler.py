"""Credential-safe, fail-closed preflight for one immutable Honua candidate."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config


OPERATION = "candidate-preflight-v1"
REQUEST = {"operation": OPERATION}
SCHEMA_VERSION = "honua-candidate-preflight-result-v1"
MANIFEST_SCHEMA_VERSION = "honua-candidate-preflight-classification-v1"
MAX_RESPONSE_BYTES = 1_000_000

IMMUTABLE = {
    "appFunctionName": "honua-demo-demo-honua",
    "candidateVersion": "40",
    "candidateRevisionId": "0326e209-4231-4acd-9bb4-d3cb89402db0",
    "liveAliasName": "live",
    "liveVersion": "39",
    "liveRevisionId": "4f73dd76-0294-44d3-8362-c6f8606f034e",
    "imageDigest": "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
    "artifactReference": "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861",
    "sourceCommit": "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad",
    "architecture": "arm64",
    "packageType": "Image",
    "skipMigrations": "true",
}

ENVIRONMENT_PINS = {
    "EXPECTED_APP_FUNCTION_NAME": "appFunctionName",
    "EXPECTED_CANDIDATE_VERSION": "candidateVersion",
    "EXPECTED_CANDIDATE_REVISION_ID": "candidateRevisionId",
    "EXPECTED_LIVE_ALIAS_NAME": "liveAliasName",
    "EXPECTED_LIVE_VERSION": "liveVersion",
    "EXPECTED_LIVE_REVISION_ID": "liveRevisionId",
    "EXPECTED_IMAGE_DIGEST": "imageDigest",
    "EXPECTED_ARTIFACT_REFERENCE": "artifactReference",
    "EXPECTED_SOURCE_COMMIT": "sourceCommit",
    "EXPECTED_ARCHITECTURE": "architecture",
    "EXPECTED_PACKAGE_TYPE": "packageType",
    "EXPECTED_SKIP_MIGRATIONS": "skipMigrations",
}

ENDPOINTS = (
    ("liveness", "/healthz/live", "", False),
    ("readiness", "/healthz/ready", "", False),
    (
        "deploy-preflight",
        "/api/v1/admin/deploy/preflight",
        "includeDiagnostics=true",
        True,
    ),
    ("migration-observability", "/api/v1/admin/observability/migrations", "", True),
)

AWS_CONFIG = Config(
    connect_timeout=3,
    read_timeout=25,
    retries={"max_attempts": 0, "mode": "standard"},
    tcp_keepalive=True,
)


class PreflightFailure(Exception):
    """Carries only an allowlisted, non-sensitive failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _failure(code: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "operation": OPERATION,
        "status": "failed",
        "failure": code,
    }


def _validate_environment() -> str:
    for variable, pin in ENVIRONMENT_PINS.items():
        if os.environ.get(variable) != IMMUTABLE[pin]:
            raise PreflightFailure("immutable-environment-drift")

    secret_arn = os.environ.get("ADMIN_PASSWORD_SECRET_ARN", "")
    if not secret_arn.startswith("arn:aws:secretsmanager:us-west-2:"):
        raise PreflightFailure("secret-reference-invalid")
    return secret_arn


def _load_classification_manifest() -> tuple[list[str], dict[str, str]]:
    try:
        manifest = json.loads(
            Path(__file__).with_name("classification.v1.json").read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise PreflightFailure("classification-manifest-invalid") from exc

    if not isinstance(manifest, dict) or set(manifest) != {
        "schemaVersion",
        "candidate",
        "scripts",
    }:
        raise PreflightFailure("classification-manifest-invalid")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise PreflightFailure("classification-manifest-invalid")

    candidate = manifest.get("candidate")
    if candidate != {
        "sourceCommit": IMMUTABLE["sourceCommit"],
        "imageDigest": IMMUTABLE["imageDigest"],
        "artifactReference": IMMUTABLE["artifactReference"],
    }:
        raise PreflightFailure("classification-candidate-drift")

    scripts = manifest.get("scripts")
    if not isinstance(scripts, list) or len(scripts) != 14:
        raise PreflightFailure("classification-script-set-invalid")

    names: list[str] = []
    for entry in scripts:
        if not isinstance(entry, dict) or set(entry) != {"name", "phase"}:
            raise PreflightFailure("classification-script-set-invalid")
        name = entry.get("name")
        if not isinstance(name, str) or not name or entry.get("phase") != "Expand":
            raise PreflightFailure("contract-phase-rejected")
        names.append(name)
    if len(set(names)) != len(names):
        raise PreflightFailure("classification-script-set-invalid")
    return names, candidate


def _candidate_fingerprint(
    lambda_client: Any,
    manifest_candidate: dict[str, str],
) -> dict[str, Any]:
    try:
        function = lambda_client.get_function(
            FunctionName=IMMUTABLE["appFunctionName"],
            Qualifier=IMMUTABLE["candidateVersion"],
        )
        configuration = lambda_client.get_function_configuration(
            FunctionName=IMMUTABLE["appFunctionName"],
            Qualifier=IMMUTABLE["candidateVersion"],
        )
    except Exception as exc:
        raise PreflightFailure("candidate-metadata-read-failed") from exc

    embedded = function.get("Configuration")
    code = function.get("Code")
    if not isinstance(embedded, dict) or not isinstance(configuration, dict) or not isinstance(code, dict):
        raise PreflightFailure("candidate-metadata-invalid")

    for candidate in (embedded, configuration):
        if candidate.get("FunctionName") != IMMUTABLE["appFunctionName"]:
            raise PreflightFailure("candidate-function-drift")
        if str(candidate.get("Version")) != IMMUTABLE["candidateVersion"]:
            raise PreflightFailure("candidate-version-drift")
        if candidate.get("RevisionId") != IMMUTABLE["candidateRevisionId"]:
            raise PreflightFailure("candidate-revision-drift")
        if candidate.get("PackageType") != IMMUTABLE["packageType"]:
            raise PreflightFailure("candidate-package-drift")
        if candidate.get("Architectures") != [IMMUTABLE["architecture"]]:
            raise PreflightFailure("candidate-architecture-drift")
        if candidate.get("State") != "Active":
            raise PreflightFailure("candidate-state-invalid")
        if candidate.get("LastUpdateStatus") not in (None, "Successful"):
            raise PreflightFailure("candidate-update-invalid")

    variables = configuration.get("Environment", {}).get("Variables", {})
    if not isinstance(variables, dict):
        raise PreflightFailure("candidate-environment-invalid")
    if variables.get("HONUA_SKIP_MIGRATIONS") != IMMUTABLE["skipMigrations"]:
        raise PreflightFailure("candidate-migration-mode-drift")
    if (
        variables.get("ControlPlane__DeployTargets__0__ArtifactReference")
        != IMMUTABLE["artifactReference"]
    ):
        raise PreflightFailure("candidate-artifact-reference-drift")

    if code.get("ImageUri") != IMMUTABLE["artifactReference"]:
        raise PreflightFailure("candidate-image-drift")
    if code.get("ResolvedImageUri") != IMMUTABLE["artifactReference"]:
        raise PreflightFailure("candidate-resolved-image-drift")

    return {
        "functionName": embedded["FunctionName"],
        "version": str(embedded["Version"]),
        "revisionId": embedded["RevisionId"],
        "packageType": embedded["PackageType"],
        "architectures": embedded["Architectures"],
        "imageUri": code["ImageUri"],
        "resolvedImageUri": code["ResolvedImageUri"],
        "skipMigrations": variables["HONUA_SKIP_MIGRATIONS"],
        "artifactReference": variables["ControlPlane__DeployTargets__0__ArtifactReference"],
        "sourceCommit": manifest_candidate["sourceCommit"],
        "imageDigest": manifest_candidate["imageDigest"],
        "provenance": "classification-manifest+resolved-image",
    }


def _alias_fingerprint(lambda_client: Any) -> dict[str, Any]:
    try:
        alias = lambda_client.get_alias(
            FunctionName=IMMUTABLE["appFunctionName"],
            Name=IMMUTABLE["liveAliasName"],
        )
    except Exception as exc:
        raise PreflightFailure("live-alias-read-failed") from exc

    if alias.get("Name") != IMMUTABLE["liveAliasName"]:
        raise PreflightFailure("live-alias-name-drift")
    if str(alias.get("FunctionVersion")) != IMMUTABLE["liveVersion"]:
        raise PreflightFailure("live-alias-version-drift")
    if alias.get("RevisionId") != IMMUTABLE["liveRevisionId"]:
        raise PreflightFailure("live-alias-revision-drift")
    if alias.get("RoutingConfig") not in (None, {}):
        raise PreflightFailure("live-alias-routing-drift")
    return {
        "name": alias["Name"],
        "version": str(alias["FunctionVersion"]),
        "revisionId": alias["RevisionId"],
        "routingConfig": alias.get("RoutingConfig"),
    }


def _read_admin_password(secrets_client: Any, secret_arn: str) -> str:
    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn)
    except Exception as exc:
        raise PreflightFailure("admin-secret-read-failed") from exc
    secret = response.get("SecretString")
    if not isinstance(secret, str) or not secret:
        raise PreflightFailure("admin-secret-invalid")
    return secret


def _api_gateway_event(
    name: str,
    path: str,
    query: str,
    admin: bool,
    admin_password: str,
) -> dict[str, Any]:
    headers = {
        "Host": "demo.honua.io",
        "User-Agent": "honua-candidate-preflight-v1",
    }
    if admin:
        headers["X-API-Key"] = admin_password

    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": headers,
        "requestContext": {
            "accountId": "candidate-preflight-v1",
            "apiId": "candidate-preflight-v1",
            "domainName": "demo.honua.io",
            "domainPrefix": "demo",
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "honua-candidate-preflight-v1",
            },
            "requestId": f"candidate-preflight-v1-{name}",
            "routeKey": "$default",
            "stage": "$default",
            "time": "01/Jan/1970:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }
    if query:
        event["queryStringParameters"] = {"includeDiagnostics": "true"}
    return event


def _invoke_candidate(lambda_client: Any, event: dict[str, Any]) -> tuple[int, str]:
    try:
        response = lambda_client.invoke(
            FunctionName=IMMUTABLE["appFunctionName"],
            Qualifier=IMMUTABLE["candidateVersion"],
            InvocationType="RequestResponse",
            LogType="None",
            Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
        )
    except Exception as exc:
        code = "candidate-invoke-timeout" if exc.__class__.__name__ in {
            "ConnectTimeoutError",
            "ReadTimeoutError",
            "TimeoutError",
        } else "candidate-invoke-failed"
        raise PreflightFailure(code) from exc

    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        raise PreflightFailure("candidate-invoke-transport-failed")
    if response.get("FunctionError"):
        raise PreflightFailure("candidate-function-error")

    payload_stream = response.get("Payload")
    try:
        raw = payload_stream.read()
    except Exception as exc:
        raise PreflightFailure("candidate-response-read-failed") from exc
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise PreflightFailure("candidate-response-size-invalid")
    try:
        proxy = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PreflightFailure("candidate-response-json-invalid") from exc
    if not isinstance(proxy, dict):
        raise PreflightFailure("candidate-response-shape-invalid")

    status = proxy.get("statusCode")
    if status == 401:
        raise PreflightFailure("candidate-http-401")
    if status != 200:
        raise PreflightFailure("candidate-http-non-200")
    body = proxy.get("body")
    if not isinstance(body, str):
        raise PreflightFailure("candidate-body-invalid")
    if proxy.get("isBase64Encoded") is True:
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except Exception as exc:
            raise PreflightFailure("candidate-body-invalid") from exc
    return status, body


def _json_body(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except Exception as exc:
        raise PreflightFailure("candidate-body-json-invalid") from exc
    if not isinstance(value, dict):
        raise PreflightFailure("candidate-body-shape-invalid")
    return value


def _validate_non_contract_migration(
    value: dict[str, Any],
    pending: list[str],
    *,
    lifecycle_field: str,
) -> None:
    if value.get(lifecycle_field) != "skipped":
        raise PreflightFailure("migration-lifecycle-drift")
    if lifecycle_field == "status":
        if value.get("isReady") is not True:
            raise PreflightFailure("migration-readiness-drift")
        if value.get("isFailed") is not False:
            raise PreflightFailure("migration-failure-state-drift")
    if value.get("planAvailable") is not True:
        raise PreflightFailure("migration-plan-unavailable")
    if value.get("upgradeRequired") is not True:
        raise PreflightFailure("migration-upgrade-branch-drift")
    if value.get("pendingScripts") != pending:
        raise PreflightFailure("migration-pending-set-drift")
    if value.get("executedButNotDiscoveredScripts") != []:
        raise PreflightFailure("migration-journal-drift")
    if value.get("planError") is not None:
        raise PreflightFailure("migration-plan-error")
    backup = value.get("backupHook")
    if not isinstance(backup, dict):
        raise PreflightFailure("migration-classification-missing")
    if backup.get("requiredForPendingSet") is not False:
        raise PreflightFailure("contract-phase-rejected")
    if backup.get("pendingContractScripts") != []:
        raise PreflightFailure("contract-phase-rejected")


def _validate_preflight(value: dict[str, Any], pending: list[str]) -> None:
    if value.get("status") != "blocked" or value.get("readyForCoordinatedDeploy") is not False:
        raise PreflightFailure("deploy-preflight-branch-drift")
    readiness = value.get("readiness")
    if not isinstance(readiness, dict) or readiness.get("isReady") is not True:
        raise PreflightFailure("deploy-readiness-invalid")
    if readiness.get("statusCode") != 200:
        raise PreflightFailure("deploy-readiness-invalid")
    compatibility = value.get("databaseCompatibility")
    if not isinstance(compatibility, dict) or compatibility.get("isCompatible") is not True:
        raise PreflightFailure("database-compatibility-invalid")
    migration = value.get("migration")
    if not isinstance(migration, dict):
        raise PreflightFailure("migration-diagnostics-missing")
    _validate_non_contract_migration(
        migration,
        pending,
        lifecycle_field="lifecycleStatus",
    )

    platform = value.get("platformRelease")
    serving = platform.get("serving") if isinstance(platform, dict) else None
    if not isinstance(serving, list) or len(serving) != 1:
        raise PreflightFailure("artifact-projection-invalid")
    if serving[0].get("effectiveArtifactReference") != IMMUTABLE["artifactReference"]:
        raise PreflightFailure("artifact-projection-drift")


def _run() -> dict[str, Any]:
    secret_arn = _validate_environment()
    pending, manifest_candidate = _load_classification_manifest()
    lambda_client = boto3.client("lambda", config=AWS_CONFIG)
    secrets_client = boto3.client("secretsmanager", config=AWS_CONFIG)

    candidate_before = _candidate_fingerprint(lambda_client, manifest_candidate)
    alias_before = _alias_fingerprint(lambda_client)
    admin_password = _read_admin_password(secrets_client, secret_arn)

    responses: dict[str, str] = {}
    for name, path, query, admin in ENDPOINTS:
        event = _api_gateway_event(name, path, query, admin, admin_password)
        _, responses[name] = _invoke_candidate(lambda_client, event)

    if responses["liveness"] != "Healthy":
        raise PreflightFailure("liveness-body-invalid")
    if responses["readiness"] != "Ready":
        raise PreflightFailure("readiness-body-invalid")
    _validate_preflight(_json_body(responses["deploy-preflight"]), pending)
    _validate_non_contract_migration(
        _json_body(responses["migration-observability"]),
        pending,
        lifecycle_field="status",
    )

    alias_after = _alias_fingerprint(lambda_client)
    candidate_after = _candidate_fingerprint(lambda_client, manifest_candidate)
    if alias_after != alias_before:
        raise PreflightFailure("live-alias-post-check-drift")
    if candidate_after != candidate_before:
        raise PreflightFailure("candidate-post-check-drift")

    pending_digest = hashlib.sha256("\n".join(pending).encode("utf-8")).hexdigest()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "operation": OPERATION,
        "status": "passed",
        "candidate": {
            "functionName": IMMUTABLE["appFunctionName"],
            "version": IMMUTABLE["candidateVersion"],
            "revisionId": IMMUTABLE["candidateRevisionId"],
            "imageDigest": IMMUTABLE["imageDigest"],
            "artifactReference": IMMUTABLE["artifactReference"],
            "sourceCommit": IMMUTABLE["sourceCommit"],
            "provenance": "classification-manifest+resolved-image",
        },
        "liveAlias": {
            "name": IMMUTABLE["liveAliasName"],
            "version": IMMUTABLE["liveVersion"],
            "revisionId": IMMUTABLE["liveRevisionId"],
        },
        "migration": {
            "phase": "Expand",
            "pendingScriptCount": len(pending),
            "pendingScriptsSha256": pending_digest,
        },
        "checks": [
            "candidate-config",
            "live-alias-pre",
            "liveness",
            "readiness",
            "deploy-preflight-diagnostics",
            "migration-observability",
            "non-contract-pending-set",
            "live-alias-post",
            "candidate-config-post",
        ],
    }


def handler(event: Any, _context: Any) -> dict[str, Any]:
    if type(event) is not dict or event != REQUEST:
        return _failure("invalid-request")
    try:
        return _run()
    except PreflightFailure as exc:
        return _failure(exc.code)
    except Exception:
        return _failure("internal-failure")
