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
SERVER_SOURCE = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
PENDING_DIGEST = "e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7"
EXECUTED_DIGEST = "8a49e1c886f6ddf58f4baf89f7b74bdfcde3fbea4d2555050d56a050f78a15c4"
ARCHIVE_SHA256 = "ea935421413a4029e775cb26346a30f38b202fe6fdc341af47de328ee16d256a"


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

    manifest_path = RUNNER / "migration-manifest.v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["sourceCommit"] == SERVER_SOURCE, "server source pin drifted")
    require(manifest["beforeVersion"] == 91 and manifest["afterVersion"] == 105, "migration boundary drifted")
    executed = manifest["executedScripts"]
    require(len(executed) == 104 and len(set(executed)) == 104, "executed-script baseline size drifted")
    require(all(re.fullmatch(r"Honua\.Server\.Migrations\.\d{3}_[A-Za-z0-9_]+\.sql", name) for name in executed), "executed-script baseline name drifted")
    executed_versions = [int(re.search(r"\.(\d{3})_", name).group(1)) for name in executed]
    require(executed_versions == sorted(executed_versions) and set(executed_versions) == set(range(1, 92)), "executed-script baseline order or coverage drifted")
    require(executed[0] == "Honua.Server.Migrations.001_CreateHonuaSchema.sql" and executed[-1] == "Honua.Server.Migrations.091_RenormalizeGeocodeReferenceSearchText.sql", "executed-script baseline boundary drifted")
    require(hashlib.sha256("\n".join(executed).encode()).hexdigest() == EXECUTED_DIGEST == manifest["executedScriptsSha256"], "executed-script baseline digest drifted")
    entries = manifest["scripts"]
    require(len(entries) == 14 and all(entry["phase"] == "Expand" for entry in entries), "exact Expand classification drifted")
    require(not any(entry["phase"] == "Contract" for entry in entries), "Contract migration admitted")
    names = [entry["name"] for entry in entries]
    require([int(re.search(r"\.(\d{3})_", name).group(1)) for name in names] == list(range(92, 106)), "migration order drifted")
    require(hashlib.sha256("\n".join(names).encode()).hexdigest() == PENDING_DIGEST == manifest["pendingScriptsSha256"], "pending digest drifted")
    require(set(path.name for path in (RUNNER / "migrations").glob("*.sql")) == {entry["file"] for entry in entries}, "migration source set drifted")
    for entry in entries:
        data = (RUNNER / "migrations" / entry["file"]).read_bytes()
        require(hashlib.sha256(data).hexdigest() == entry["sha256"], f"migration source hash drifted: {entry['file']}")
    handler = (RUNNER / "handler.py").read_text(encoding="utf-8")
    build = (RUNNER / "build.py").read_text(encoding="utf-8")
    for required in ('date_time=(1980, 1, 1, 0, 0, 0)', "compresslevel=9", "0o100644 << 16", 'ZipFile(output, "w")'):
        require(required in build, f"deterministic archive contract missing: {required}")
    operator = (ROOT / "scripts" / "db-migration-runner-plan-apply.sh").read_text(encoding="utf-8")
    require(f'ARCHIVE_SHA256="{ARCHIVE_SHA256}"' in operator, "operator archive digest drifted")
    for required in ("pg_try_advisory_xact_lock", 'connection.run("BEGIN")', 'connection.run("COMMIT")', 'connection.run("ROLLBACK")', "journal-before-boundary-drift", "migration-replay-rejected", "journal-after-boundary-drift"):
        require(required in handler, f"transaction/replay contract missing: {required}")
    for leak in ("traceback", "str(exc)", "SecretString\"]", "connectionString"):
        require(leak not in handler, f"secret/error leakage surface present: {leak}")
    print("db migration runner source contract: PASS")


if __name__ == "__main__":
    main()
