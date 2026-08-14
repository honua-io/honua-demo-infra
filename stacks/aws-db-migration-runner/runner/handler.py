"""Fixed, transactional Honua database migration runner for scripts 092-105."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from pathlib import Path
from typing import Any

import boto3
import pg8000.native


OPERATION = "apply-092-105"
REQUEST = {"operation": OPERATION}
SCHEMA = "honua-db-migration-result-v1"
ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "migration-manifest.v1.json"
MIGRATIONS = ROOT / "migrations"
RDS_CA = ROOT / "rds-global-bundle.pem"
LOCK_KEY = 8_044_282_257_919_950_151
SCRIPT_PATTERN = re.compile(r"^Honua\.Server\.Migrations\.(\d{3})_[A-Za-z0-9_]+\.sql$")
DOLLAR_QUOTE = re.compile(r"\$\$|\$[A-Za-z_][A-Za-z0-9_]*\$")


class MigrationFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _failed(code: str) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA, "operation": OPERATION, "status": "failed", "failure": code}


def _load_manifest() -> tuple[dict[str, Any], list[tuple[str, str]]]:
    try:
        raw = MANIFEST_PATH.read_bytes()
        manifest = json.loads(raw)
    except Exception as exc:
        raise MigrationFailure("manifest-invalid") from exc
    required = {
        "schemaVersion", "sourceCommit", "candidateImageDigest", "preflightReceiptSha256",
        "executedScriptsSha256", "executedScripts", "beforeVersion", "afterVersion",
        "pendingScriptsSha256", "scripts",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise MigrationFailure("manifest-invalid")
    if manifest["schemaVersion"] != "honua-db-migration-runner-manifest-v1":
        raise MigrationFailure("manifest-invalid")
    pins = {
        "EXPECTED_SOURCE_COMMIT": manifest["sourceCommit"],
        "EXPECTED_CANDIDATE_IMAGE_DIGEST": manifest["candidateImageDigest"],
        "EXPECTED_PREFLIGHT_SHA256": manifest["preflightReceiptSha256"],
        "EXPECTED_PENDING_SET_SHA256": manifest["pendingScriptsSha256"],
        "MIGRATION_OPERATION": OPERATION,
    }
    if any(os.environ.get(key) != value for key, value in pins.items()):
        raise MigrationFailure("immutable-environment-drift")
    if os.environ.get("SOURCE_HANDLER_SHA256") != _sha(Path(__file__).read_bytes()):
        raise MigrationFailure("handler-source-drift")
    if os.environ.get("SOURCE_MANIFEST_SHA256") != _sha(raw):
        raise MigrationFailure("manifest-source-drift")
    if manifest["beforeVersion"] != 91 or manifest["afterVersion"] != 105:
        raise MigrationFailure("migration-boundary-drift")

    executed = manifest["executedScripts"]
    if not isinstance(executed, list) or len(executed) != 104 or any(type(name) is not str for name in executed):
        raise MigrationFailure("executed-script-baseline-drift")
    if len(set(executed)) != len(executed):
        raise MigrationFailure("executed-script-baseline-drift")
    executed_versions: list[int] = []
    for name in executed:
        match = SCRIPT_PATTERN.fullmatch(name)
        if not match:
            raise MigrationFailure("executed-script-baseline-drift")
        executed_versions.append(int(match.group(1)))
    if executed_versions != sorted(executed_versions) or set(executed_versions) != set(range(1, 92)):
        raise MigrationFailure("executed-script-baseline-drift")
    if _sha("\n".join(executed).encode()) != manifest["executedScriptsSha256"]:
        raise MigrationFailure("executed-script-baseline-digest-drift")

    loaded: list[tuple[str, str]] = []
    entries = manifest["scripts"]
    if not isinstance(entries, list) or len(entries) != 14:
        raise MigrationFailure("script-set-drift")
    for expected, entry in zip(range(92, 106), entries, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"name", "file", "phase", "sha256"} or entry["phase"] != "Expand":
            raise MigrationFailure("script-classification-drift")
        match = SCRIPT_PATTERN.fullmatch(entry["name"])
        if not match or int(match.group(1)) != expected or entry["file"] != entry["name"].removeprefix("Honua.Server.Migrations."):
            raise MigrationFailure("script-order-drift")
        try:
            source = (MIGRATIONS / entry["file"]).read_bytes()
        except OSError as exc:
            raise MigrationFailure("script-source-missing") from exc
        if _sha(source) != entry["sha256"]:
            raise MigrationFailure("script-source-drift")
        loaded.append((entry["name"], source.decode("utf-8")))
    names_digest = _sha("\n".join(name for name, _ in loaded).encode())
    if names_digest != manifest["pendingScriptsSha256"]:
        raise MigrationFailure("pending-set-digest-drift")
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


def _split_sql(script: str) -> list[str]:
    statements: list[str] = []
    start = index = depth = 0
    state, tag = "normal", None
    while index < len(script):
        if state == "normal":
            if script.startswith("--", index): state, index = "line", index + 2; continue
            if script.startswith("/*", index): state, depth, index = "block", 1, index + 2; continue
            char = script[index]
            if char == "'": state = "single"
            elif char == '"': state = "double"
            elif char == "$":
                match = DOLLAR_QUOTE.match(script, index)
                if match: state, tag, index = "dollar", match.group(), match.end(); continue
            elif char == ";": statements.append(script[start:index + 1]); start = index + 1
            index += 1; continue
        if state == "line":
            if script[index] in "\r\n": state = "normal"
            index += 1; continue
        if state == "block":
            if script.startswith("/*", index): depth += 1; index += 2; continue
            if script.startswith("*/", index):
                depth -= 1; index += 2
                if depth == 0: state = "normal"
                continue
            index += 1; continue
        if state == "single":
            if script[index] == "'":
                if index + 1 < len(script) and script[index + 1] == "'": index += 2; continue
                state = "normal"
            index += 1; continue
        if state == "double":
            if script[index] == '"':
                if index + 1 < len(script) and script[index + 1] == '"': index += 2; continue
                state = "normal"
            index += 1; continue
        if state == "dollar":
            if script.startswith(tag, index): index += len(tag); state, tag = "normal", None; continue
            index += 1
    if state not in {"normal", "line"}:
        raise MigrationFailure("sql-parse-failed")
    tail = script[start:]
    if tail.strip(): statements.append(tail)
    return [statement for statement in statements if statement.strip()]


def _journal(connection) -> list[str]:
    exists = connection.run("SELECT pg_catalog.to_regclass('public.schema_versions')::text")
    if exists != [["schema_versions"]]:
        raise MigrationFailure("journal-missing")
    rows = connection.run("SELECT scriptname FROM public.schema_versions ORDER BY schemaversionsid")
    names = [row[0] for row in rows]
    if len(names) != len(set(names)):
        raise MigrationFailure("journal-duplicate")
    return names


def _run() -> dict[str, Any]:
    manifest, scripts = _load_manifest()
    connection = _connect()
    committed = False
    try:
        connection.run("BEGIN")
        if connection.run("SELECT pg_try_advisory_xact_lock(:key)", key=LOCK_KEY) != [[True]]:
            raise MigrationFailure("migration-lock-unavailable")
        before = _journal(connection)
        expected_before = manifest["executedScripts"]
        expected = [name for name, _ in scripts]
        if any(name in before for name in expected):
            raise MigrationFailure("migration-replay-rejected")
        if before != expected_before:
            raise MigrationFailure("journal-before-boundary-drift")

        for name, source in scripts:
            rendered = source.replace("$HonuaSchema$", '"honua"')
            for statement in _split_sql(rendered):
                connection.run(statement)
            connection.run(
                "INSERT INTO public.schema_versions (scriptname, applied) VALUES (:name, CURRENT_TIMESTAMP)",
                name=name,
            )
        after = _journal(connection)
        if after != expected_before + expected:
            raise MigrationFailure("journal-after-boundary-drift")
        connection.run("COMMIT")
        committed = True
        return {
            "schemaVersion": SCHEMA,
            "operation": OPERATION,
            "status": "passed",
            "sourceCommit": manifest["sourceCommit"],
            "candidateImageDigest": manifest["candidateImageDigest"],
            "preflightReceiptSha256": manifest["preflightReceiptSha256"],
            "migration": {
                "phase": "Expand",
                "beforeVersion": 91,
                "afterVersion": 105,
                "appliedScriptCount": 14,
                "appliedScripts": expected,
                "pendingScripts": [],
                "executedButNotDiscoveredScripts": [],
                "pendingScriptsSha256": manifest["pendingScriptsSha256"],
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
    if type(event) is not dict or event != REQUEST:
        return _failed("invalid-request")
    try:
        return _run()
    except MigrationFailure as exc:
        return _failed(exc.code)
    except Exception:
        return _failed("internal-failure")
