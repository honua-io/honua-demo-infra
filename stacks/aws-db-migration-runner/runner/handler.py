"""Fixed, transactional Honua database migration runner for the honua-server 574c804 pending set.

The runner replays exactly what honua-server's own migration engine (DbUp, single transaction,
journal ``public.schema_versions``) would execute for the reviewed pending set, using commands
rendered and split ahead of time from the server source at the pinned commit. It never splits or
templates SQL itself. After the scripts it records the forward-compatibility declarations the
server records beside each journal entry (honua-server#6477).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import ssl
from pathlib import Path
from typing import Any

import boto3
import pg8000.native


OPERATION = "apply-574c804"
SCHEMA = "honua-db-migration-result-v2"
MANIFEST_SCHEMA = "honua-db-migration-runner-manifest-v2"
ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "migration-manifest.v2.json"
MIGRATIONS = ROOT / "migrations"
RDS_CA = ROOT / "rds-global-bundle.pem"
# honua-server PostgresDatabaseMigrationRunner.MigrationLockKey: the server's own migration lock.
LOCK_KEY = 8_044_282_257_919_950_151
READER_TABLE = "public.schema_version_reader_compatibility"
SCRIPT_NAME = re.compile(r"^Honua\.(Server|Postgres)\.Migrations\.\d{3}_[A-Za-z0-9_]+\.sql$")
DECLARATION_KEYS = {"scriptName", "declarationFormat", "classification", "scriptSha256", "minReaderSchema", "scriptText"}
MAX_REPORTED_NAMES = 50


class MigrationFailure(Exception):
    def __init__(self, code: str, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _names_sha(names: list[str]) -> str:
    return _sha("\n".join(names).encode())


def _failed(code: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"schemaVersion": SCHEMA, "operation": OPERATION, "status": "failed", "failure": code}
    if detail:
        result["detail"] = detail
    return result


def _load_manifest() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = MANIFEST_PATH.read_bytes()
        manifest = json.loads(raw)
    except Exception as exc:
        raise MigrationFailure("manifest-invalid") from exc
    required = {
        "schemaVersion", "operation", "sourceCommit", "candidateImageDigest", "executedScripts",
        "executedScriptsSha256", "scripts", "pendingScriptsSha256", "contractApprovalNonce",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise MigrationFailure("manifest-invalid")
    if manifest["schemaVersion"] != MANIFEST_SCHEMA or manifest["operation"] != OPERATION:
        raise MigrationFailure("manifest-invalid")
    pins = {
        "EXPECTED_SOURCE_COMMIT": manifest["sourceCommit"],
        "EXPECTED_CANDIDATE_IMAGE_DIGEST": manifest["candidateImageDigest"],
        "EXPECTED_EXECUTED_SET_SHA256": manifest["executedScriptsSha256"],
        "EXPECTED_PENDING_SET_SHA256": manifest["pendingScriptsSha256"],
        "MIGRATION_OPERATION": OPERATION,
    }
    if any(os.environ.get(key) != value for key, value in pins.items()):
        raise MigrationFailure("immutable-environment-drift")
    if os.environ.get("SOURCE_HANDLER_SHA256") != _sha(Path(__file__).read_bytes()):
        raise MigrationFailure("handler-source-drift")
    if os.environ.get("SOURCE_MANIFEST_SHA256") != _sha(raw):
        raise MigrationFailure("manifest-source-drift")

    executed = manifest["executedScripts"]
    if (not isinstance(executed, list) or not executed or any(type(name) is not str or not SCRIPT_NAME.fullmatch(name) for name in executed)
            or executed != sorted(executed) or len(set(executed)) != len(executed)):
        raise MigrationFailure("executed-script-baseline-drift")
    if _names_sha(executed) != manifest["executedScriptsSha256"]:
        raise MigrationFailure("executed-script-baseline-digest-drift")

    nonce = manifest["contractApprovalNonce"]
    entries = manifest["scripts"]
    if not isinstance(entries, list) or not entries:
        raise MigrationFailure("script-set-drift")
    loaded: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "file", "phase", "sha256"} or entry["phase"] not in {"Expand", "Contract"}:
            raise MigrationFailure("script-classification-drift")
        if not SCRIPT_NAME.fullmatch(entry["name"]) or entry["name"] in executed:
            raise MigrationFailure("script-order-drift")
        try:
            raw_script = (MIGRATIONS / entry["file"]).read_bytes()
        except OSError as exc:
            raise MigrationFailure("script-source-missing") from exc
        if _sha(raw_script) != entry["sha256"]:
            raise MigrationFailure("script-source-drift")
        script = json.loads(raw_script)
        if (not isinstance(script, dict) or set(script) != {"name", "phase", "commands", "declaration"}
                or script["name"] != entry["name"] or script["phase"] != entry["phase"]
                or not isinstance(script["commands"], list) or not script["commands"]
                or any(type(command) is not str or not command.strip() for command in script["commands"])):
            raise MigrationFailure("script-source-drift")
        declaration = script["declaration"]
        if (not isinstance(declaration, dict) or set(declaration) != DECLARATION_KEYS
                or declaration["scriptName"] != entry["name"] or declaration["declarationFormat"] != 1
                or _sha(declaration["scriptText"].encode()).upper() != declaration["scriptSha256"]):
            raise MigrationFailure("reader-compatibility-declaration-drift")
        loaded.append(script)
    names = [script["name"] for script in loaded]
    if len(set(names)) != len(names) or _names_sha(names) != manifest["pendingScriptsSha256"]:
        raise MigrationFailure("pending-set-digest-drift")
    has_contract = any(script["phase"] == "Contract" for script in loaded)
    # A Contract script needs the server's one-shot approval nonce for exactly this set; an
    # Expand-only set carries none (honua-server MigrationSafetyClassifier, #2812).
    if has_contract != (type(nonce) is str and re.fullmatch(r"[0-9a-f]{16,64}", nonce) is not None) or (not has_contract and nonce is not None):
        raise MigrationFailure("contract-approval-manifest-drift")
    return manifest, loaded


def _parse_connection_string(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in value.split(";"):
        if "=" in chunk:
            key, item = chunk.split("=", 1)
            parts[key.strip().lower()] = item.strip()
    required = {"host", "database", "username", "password"}
    if not required.issubset(parts) or not all(parts[key] for key in required):
        raise MigrationFailure("connection-secret-invalid")
    return parts


def _connect():
    secret_arn = os.environ.get("DB_SECRET_ARN", "")
    if not re.fullmatch(r"arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-[A-Za-z0-9]{6}", secret_arn):
        raise MigrationFailure("secret-reference-invalid")
    try:
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        params = _parse_connection_string(secret.get("SecretString", ""))
        context = ssl.create_default_context(cafile=str(RDS_CA))
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return pg8000.native.Connection(
            user=params["username"], password=params["password"], host=params["host"],
            port=int(params.get("port", "5432")), database=params["database"],
            ssl_context=context, timeout=30,
        )
    except MigrationFailure:
        raise
    except Exception as exc:
        raise MigrationFailure("database-connect-failed") from exc


def _journal(connection) -> list[str]:
    exists = connection.run("SELECT pg_catalog.to_regclass('public.schema_versions')::text")
    if exists != [["schema_versions"]]:
        raise MigrationFailure("journal-missing")
    rows = connection.run("SELECT scriptname FROM public.schema_versions ORDER BY schemaversionsid")
    names = [row[0] for row in rows]
    if len(names) != len(set(names)):
        raise MigrationFailure("journal-duplicate")
    return names


def _bounded(names: set[str]) -> list[str]:
    return sorted(names)[:MAX_REPORTED_NAMES]


def _check_approval(event: Any, manifest: dict[str, Any]) -> None:
    nonce = manifest["contractApprovalNonce"]
    if nonce is None:
        if event != {"operation": OPERATION}:
            raise MigrationFailure("invalid-request")
        return
    if type(event) is not dict or set(event) != {"operation", "approveContractMigrations"} or event["operation"] != OPERATION:
        raise MigrationFailure("contract-approval-required", {"contractApprovalNonce": nonce})
    supplied = event["approveContractMigrations"]
    if type(supplied) is not str or not hmac.compare_digest(supplied.strip().lower(), nonce):
        raise MigrationFailure("contract-approval-required", {"contractApprovalNonce": nonce})


def _run(manifest: dict[str, Any], scripts: list[dict[str, Any]]) -> dict[str, Any]:
    connection = _connect()
    committed = False
    try:
        # The server migrates with SearchPath=public on its migration connection.
        connection.run("SET search_path TO public")
        connection.run("BEGIN")
        if connection.run("SELECT pg_try_advisory_xact_lock(:key)", key=LOCK_KEY) != [[True]]:
            raise MigrationFailure("migration-lock-unavailable")
        before = _journal(connection)
        expected = [script["name"] for script in scripts]
        if any(name in before for name in expected):
            raise MigrationFailure("migration-replay-rejected")
        baseline = set(manifest["executedScripts"])
        if set(before) != baseline:
            raise MigrationFailure("journal-before-boundary-drift", {
                "missingScripts": _bounded(baseline - set(before)),
                "unexpectedScripts": _bounded(set(before) - baseline),
            })
        if any(name.startswith("Honua.Postgres.Migrations.") for name in expected):
            # The server only discovers provider (raster) migrations when postgis_raster is installed.
            if connection.run("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'postgis_raster')") != [[True]]:
                raise MigrationFailure("postgis-raster-missing")
        if connection.run(f"SELECT pg_catalog.to_regclass('{READER_TABLE}') IS NOT NULL") != [[True]]:
            raise MigrationFailure("reader-compatibility-table-missing")

        for script in scripts:
            for command in script["commands"]:
                connection.run(command)
            connection.run(
                "INSERT INTO public.schema_versions (scriptname, applied) VALUES (:name, CURRENT_TIMESTAMP)",
                name=script["name"],
            )
        after = _journal(connection)
        if after != before + expected:
            raise MigrationFailure("journal-after-boundary-drift")

        for script in scripts:
            declaration = script["declaration"]
            connection.run(
                f"""INSERT INTO {READER_TABLE}
                    (script_name, declaration_format, classification, script_sha256, min_reader_schema, script_text)
                VALUES (:name, :format, :classification, :sha256, :min_reader_schema, :text)
                ON CONFLICT (script_name) DO NOTHING""",
                name=declaration["scriptName"], format=declaration["declarationFormat"],
                classification=declaration["classification"], sha256=declaration["scriptSha256"],
                min_reader_schema=declaration["minReaderSchema"], text=declaration["scriptText"],
            )
            recorded = connection.run(
                f"SELECT classification, script_sha256, min_reader_schema FROM {READER_TABLE} WHERE script_name = :name",
                name=declaration["scriptName"],
            )
            if recorded != [[declaration["classification"], declaration["scriptSha256"], declaration["minReaderSchema"]]]:
                raise MigrationFailure("reader-compatibility-declaration-conflict")

        connection.run("COMMIT")
        committed = True
        return {
            "schemaVersion": SCHEMA,
            "operation": OPERATION,
            "status": "passed",
            "sourceCommit": manifest["sourceCommit"],
            "candidateImageDigest": manifest["candidateImageDigest"],
            "migration": {
                "executedScriptsSha256": manifest["executedScriptsSha256"],
                "appliedScriptCount": len(expected),
                "appliedScripts": expected,
                "contractScripts": [script["name"] for script in scripts if script["phase"] == "Contract"],
                "contractApprovalNonce": manifest["contractApprovalNonce"],
                "pendingScripts": [],
                "pendingScriptsSha256": manifest["pendingScriptsSha256"],
                "readerCompatibilityDeclarations": len(expected),
                "journalContinuous": True,
            },
        }
    except MigrationFailure:
        if not committed:
            try: connection.run("ROLLBACK")
            except Exception: pass
        raise
    except Exception as exc:
        if not committed:
            try: connection.run("ROLLBACK")
            except Exception: pass
        raise MigrationFailure("migration-transaction-failed") from exc
    finally:
        connection.close()


def handler(event: Any, _context: Any) -> dict[str, Any]:
    if type(event) is not dict or event.get("operation") != OPERATION:
        return _failed("invalid-request")
    try:
        manifest, scripts = _load_manifest()
        _check_approval(event, manifest)
        return _run(manifest, scripts)
    except MigrationFailure as exc:
        return _failed(exc.code, exc.detail)
    except Exception:
        return _failed("internal-failure")
