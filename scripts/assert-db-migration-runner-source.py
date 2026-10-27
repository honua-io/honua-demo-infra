#!/usr/bin/env python3
"""Fail closed on migration runner source, isolation, IAM, and artifact drift."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "stacks" / "aws-db-migration-runner"
RUNNER = STACK / "runner"
SERVER_SOURCE = "574c804176d969550a8e3002e0ebcc5b12df6893"
SERVING_IMAGE_DIGEST = "sha256:82cf04288a8f7a5aa054f06e7a2201fbc72e00fe7e6fabfa7b7eac4acfd0788e"
PENDING_DIGEST = "ed316a74359ebf8a72348159955745ab3032fbfe035c4453dda826c0ce7ac6a3"
EXECUTED_DIGEST = "9b9cbb998176d159b278e5a57515aeaed3e37c490943585089a6e974cb02cc33"
PENDING_SCRIPTS = [
    "Honua.Postgres.Migrations.008_AddRasterSamplingFunctions.sql",
    "Honua.Server.Migrations.136_CreateAdminApiKeys.sql",
    "Honua.Server.Migrations.137_CaptureFeatureChangeCommittedImage.sql",
    "Honua.Server.Migrations.138_CreateJobArtifactImportReceipt.sql",
    "Honua.Server.Migrations.139_AddAdminApiKeyRestrictions.sql",
    "Honua.Server.Migrations.140_CreateAiExecutionAdmission.sql",
]
ARCHIVE_SHA256 = "2a8be2ef98433c526876aef2c7bbf28fc31ba4a39835c340879f24873e639511"


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main() -> None:
    versions = (STACK / "versions.tf").read_text(encoding="utf-8")
    main_tf = (STACK / "main.tf").read_text(encoding="utf-8")
    all_tf = versions + main_tf
    require('key          = "demo/aws-demo/db-migration-runner.tfstate"' in versions, "isolated state key drifted")
    require('allowed_account_ids = ["585192672263"]' in versions, "account guard drifted")
    require('region              = "us-west-2"' in versions, "provider region drifted")
    require('terraform.workspace == "default"' in main_tf, "default workspace guard missing")
    for forbidden in ("terraform_remote_state", "terraform_data", "archive_file", "local-exec", "module.honua", "aws_db_instance", "aws_db_snapshot", "aws_lambda_alias", "aws_lambda_invocation", "aws_secretsmanager_secret_version", "secret_string", "ignore_changes"):
        require(forbidden not in all_tf, f"forbidden resource or escape hatch: {forbidden}")
    require(main_tf.count('resource "aws_lambda_function"') == 1, "runner function graph drifted")
    require("reserved_concurrent_executions = 1" in main_tf and "timeout                        = 900" in main_tf and "publish                        = true" in main_tf, "runner bounds drifted")
    require("filename                       = local.runner_archive" in main_tf and "source_code_hash               = filebase64sha256(local.runner_archive)" in main_tf, "runner is not bound to the pre-plan canonical archive")
    require(not re.search(r'(?m)^\s*(event_source|schedule_expression|function_url|source_arn)\s*=', main_tf), "runner trigger surfaced")
    require('Action   = ["secretsmanager:GetSecretValue"]' in main_tf, "exact secret read missing")
    require('Resource = [data.aws_secretsmanager_secret.db_connection.arn]' in main_tf, "secret IAM scope drifted")
    require(main_tf.count('Resource = ["*"]') == 1 and 'Sid    = "ManageOwnVpcInterface"' in main_tf, "wildcard IAM is not limited to ENI operations")
    for forbidden_action in ("rds:Delete", "rds:Restore", "rds:Modify", "lambda:Update", "lambda:Publish", "lambda:AddPermission", "kms:"):
        require(forbidden_action not in main_tf, f"forbidden runner IAM action: {forbidden_action}")

    manifest_path = RUNNER / "migration-manifest.v2.json"
    require(not (RUNNER / "migration-manifest.v1.json").exists(), "retired v1 manifest still present")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["schemaVersion"] == "honua-db-migration-runner-manifest-v2" and manifest["operation"] == "apply-574c804", "manifest schema/operation drifted")
    require(manifest["sourceCommit"] == SERVER_SOURCE, "server source pin drifted")
    require(manifest["candidateImageDigest"] == SERVING_IMAGE_DIGEST, "candidate image pin drifted")
    executed = manifest["executedScripts"]
    require(len(executed) == 158 and len(set(executed)) == 158 and executed == sorted(executed), "executed-script baseline size or order drifted")
    require(all(re.fullmatch(r"Honua\.(Server|Postgres)\.Migrations\.\d{3}_[A-Za-z0-9_]+\.sql", name) for name in executed), "executed-script baseline name drifted")
    require("Honua.Server.Migrations.109_AdoptConfiguredGuardedSchema.sql" not in executed, "non-default-schema adoption script admitted")
    require(hashlib.sha256("\n".join(executed).encode()).hexdigest() == EXECUTED_DIGEST == manifest["executedScriptsSha256"], "executed-script baseline digest drifted")
    entries = manifest["scripts"]
    names = [entry["name"] for entry in entries]
    require(names == PENDING_SCRIPTS, "pending script set or order drifted")
    require(all(entry["phase"] == "Expand" for entry in entries) and manifest["contractApprovalNonce"] is None, "Contract migration admitted without a reviewed approval nonce")
    require(not set(names) & set(executed), "pending script overlaps the baseline")
    require(hashlib.sha256("\n".join(names).encode()).hexdigest() == PENDING_DIGEST == manifest["pendingScriptsSha256"], "pending digest drifted")
    require(set(path.name for path in (RUNNER / "migrations").iterdir()) == {entry["file"] for entry in entries}, "migration source set drifted")
    for entry in entries:
        data = (RUNNER / "migrations" / entry["file"]).read_bytes()
        require(hashlib.sha256(data).hexdigest() == entry["sha256"], f"migration source hash drifted: {entry['file']}")
        script = json.loads(data)
        require(script["name"] == entry["name"] and script["phase"] == entry["phase"] and script["commands"], f"migration body drifted: {entry['file']}")
        declaration = script["declaration"]
        require(declaration["scriptName"] == entry["name"] and declaration["declarationFormat"] == 1 and declaration["classification"] == "expand", f"reader declaration drifted: {entry['file']}")
        require(hashlib.sha256(declaration["scriptText"].encode()).hexdigest().upper() == declaration["scriptSha256"], f"reader declaration hash drifted: {entry['file']}")
        require(not any(re.search(r"\$Honua[A-Za-z0-9_]*\$", command) for command in script["commands"]), f"unrendered DbUp variable: {entry['file']}")
    handler = (RUNNER / "handler.py").read_text(encoding="utf-8")
    build = (RUNNER / "build.py").read_text(encoding="utf-8")
    for required in ('date_time=(1980, 1, 1, 0, 0, 0)', "zipfile.ZIP_STORED", "0o100644 << 16", 'ZipFile(output, "w")', 'if b"\\r" in data', 'key=lambda item: item[0].encode("utf-8")'):
        require(required in build, f"deterministic archive contract missing: {required}")
    require("ZIP_DEFLATED" not in build and "compresslevel" not in build, "host-dependent ZIP compression admitted")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for required in ("stacks/aws-db-migration-runner/runner/*.py text eol=lf", "stacks/aws-db-migration-runner/runner/*.json text eol=lf", "stacks/aws-db-migration-runner/runner/migrations/*.json text eol=lf"):
        require(required in attributes, f"hashed input LF pin missing: {required}")
    operator = (ROOT / "scripts" / "db-migration-runner-plan-apply.sh").read_text(encoding="utf-8")
    require(f'ARCHIVE_SHA256="{ARCHIVE_SHA256}"' in operator, "operator archive digest drifted")
    workflow = (ROOT / ".github" / "workflows" / "manifest-drift.yml").read_text(encoding="utf-8")
    require(workflow.count("runner/build.py --source") == 2 and ARCHIVE_SHA256 in workflow, "CI does not independently rebuild and pin the advertised archive")
    for required in ("pg_try_advisory_xact_lock", 'connection.run("BEGIN")', 'connection.run("COMMIT")', 'connection.run("ROLLBACK")', "journal-before-boundary-drift", "migration-replay-rejected", "journal-after-boundary-drift", "postgis-raster-missing", "reader-compatibility-declaration-conflict", 'SET search_path TO public'):
        require(required in handler, f"transaction/replay contract missing: {required}")
    for leak in ("traceback", "str(exc)", "SecretString\"]", "connectionString"):
        require(leak not in handler, f"secret/error leakage surface present: {leak}")
    print("db migration runner source contract: PASS")


if __name__ == "__main__":
    main()
