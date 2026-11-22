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
SERVER_SOURCE = "99c422646c4cd246a73e9b65efd00e9e36724587"
CANDIDATE_IMAGE_DIGEST = "sha256:2ef00dc750c8222ed01694f52371bfd3e7b17cd12378ad6f2281efdde2bd2e76"
PENDING_DIGEST = "0727b14c42422add2eaa88e45ee1f8be5e70762a1faf7405bd9fca7216197ecb"
EXECUTED_DIGEST = "b5bbb10c11ce58f33b6a1d92e4228544fa9728c30b75192f36770f4cf5dd8903"
# honua-server MigrationSafetyClassifier at SERVER_SOURCE: one ContractAnnotated script (drop-index).
CONTRACT_SCRIPTS = ["Honua.Server.Migrations.183_RemoveUnusedFeatureIndexes.sql"]
CONTRACT_APPROVAL_NONCE = "bc2202c7644b6d7f"
# Reader-compatibility classes the server declares for this set (MigrationReaderCompatibility.Declare).
READER_CLASSES = {"expand", "reader-incompatible", "contract"}
PENDING_SCRIPTS = [
    "Honua.Server.Migrations.141_CreateImportedDatasetSpatialMetadata.sql",
    "Honua.Server.Migrations.142_CreateAiFinancialAccounting.sql",
    "Honua.Server.Migrations.143_PromoteImportTypedColumns.sql",
    "Honua.Server.Migrations.144_RenameGeoServicesRestSourceKind.sql",
    "Honua.Server.Migrations.145_ProtectImportDependencies.sql",
    "Honua.Server.Migrations.146_AddMigrationBatchPublicationTarget.sql",
    "Honua.Server.Migrations.147_CreateJobSubstrate.sql",
    "Honua.Server.Migrations.148_CreateControlPlaneState.sql",
    "Honua.Server.Migrations.149_CreateSecurityState.sql",
    "Honua.Server.Migrations.150_CreateOAuthClientsAndOperationProgress.sql",
    "Honua.Server.Migrations.151_CreateCdnReleaseRecord.sql",
    "Honua.Server.Migrations.152_IndexAdminApiKeyExpiry.sql",
    "Honua.Server.Migrations.153_CreateVersionJobsAndTileGenerationCheckpoints.sql",
    "Honua.Server.Migrations.154_CreatePackageDraftsAndMcpSessions.sql",
    "Honua.Server.Migrations.155_CreateAgentWorkspaceIdentities.sql",
    "Honua.Server.Migrations.155_CreateMetadataFieldHistory.sql",
    "Honua.Server.Migrations.156_CreateConfigurationDocuments.sql",
    "Honua.Server.Migrations.157_FeatureChangeRealtimeStream.sql",
    "Honua.Server.Migrations.158_CreateRealtimeEventLog.sql",
    "Honua.Server.Migrations.161_CreateOpsExternalEvents.sql",
    "Honua.Server.Migrations.161_CreateOpsNotificationSchedules.sql",
    "Honua.Server.Migrations.161_PublishSavedMapOperationsToRealtime.sql",
    "Honua.Server.Migrations.162_CreateCatalogActivationWatches.sql",
    "Honua.Server.Migrations.162_CreateStudioDraftRevisions.sql",
    "Honua.Server.Migrations.163_CreateCdnDeploymentPurges.sql",
    "Honua.Server.Migrations.165_CreateExternalLayerChanges.sql",
    "Honua.Server.Migrations.166_CreateSourceWatermarks.sql",
    "Honua.Server.Migrations.167_CreateWorkflowStores.sql",
    "Honua.Server.Migrations.168_AddAgentAuditActor.sql",
    "Honua.Server.Migrations.169_CreateOpsIncidentObservations.sql",
    "Honua.Server.Migrations.170_CreateAiMonthlyActorSpend.sql",
    "Honua.Server.Migrations.171_CaptureObservationRealtimeEvents.sql",
    "Honua.Server.Migrations.172_CreateOpsIncidentEpisodes.sql",
    "Honua.Server.Migrations.174_CreateOperationProposalBatches.sql",
    "Honua.Server.Migrations.175_CreateRealtimeJobCatalogTriggers.sql",
    "Honua.Server.Migrations.176_CreateRemoteImportCommitReceipt.sql",
    "Honua.Server.Migrations.180_CreateFeatureLocks.sql",
    "Honua.Server.Migrations.181_CreateAttachmentUploadSessions.sql",
    "Honua.Server.Migrations.182_CreateAttachmentNativeMetadata.sql",
    "Honua.Server.Migrations.183_RemoveUnusedFeatureIndexes.sql",
    "Honua.Server.Migrations.186_ExpandMetadataV2Vocabulary.sql",
    "Honua.Server.Migrations.189_AssignFeatureChangeTransactionId.sql",
    "Honua.Server.Migrations.191_CreateCdnPurgeBudget.sql",
    "Honua.Server.Migrations.192_CreateWfsStoredQueries.sql",
    "Honua.Server.Migrations.193_AllowIncidentInvestigationReferences.sql",
    "Honua.Server.Migrations.194_CaptureFeatureOperationEvidence.sql",
    "Honua.Server.Migrations.195_AddGrpcReceiptOperationEvidence.sql",
    "Honua.Server.Migrations.198_EnforceMetadataGlobalIdUniqueness.sql",
    "Honua.Server.Migrations.199_AddAnalysisContentTenantOwnership.sql",
    "Honua.Server.Migrations.200_ExpandAdminApiKeyStorage.sql",
    "Honua.Server.Migrations.201_CreateEmbedKeyUsage.sql",
    "Honua.Server.Migrations.204_CreateAttachmentUploadCleanupLedger.sql",
    "Honua.Server.Migrations.205_AddAttachmentPublicationScope.sql",
    "Honua.Server.Migrations.206_CreateAttachmentCleanupRetries.sql",
    "Honua.Server.Migrations.207_CreateAnalysisReports.sql",
    "Honua.Server.Migrations.208_CreateOAuthScopeCatalogue.sql",
    "Honua.Server.Migrations.212_CreateConfigurationDocumentRevisions.sql",
    "Honua.Server.Migrations.213_CreateFeatureChangeReadHorizon.sql",
    "Honua.Server.Migrations.214_StageFeatureChangePrefixPublisher.sql",
    "Honua.Server.Migrations.215_AddConsoleAccessEnvironmentOwnership.sql",
    "Honua.Server.Migrations.216_AdvanceConfigurationGenerationOnSceneAndPolicyChanges.sql",
    "Honua.Server.Migrations.217_CreateAiIncidentSpend.sql",
    "Honua.Server.Migrations.218_CreateAssetTaskCore.sql",
    "Honua.Server.Migrations.219_SkipGlobalIdIndexesOfPublishingLayers.sql",
    "Honua.Server.Migrations.220_AddPolicyRestrictions.sql",
    "Honua.Server.Migrations.221_CreateProposalEvidenceConsumptions.sql",
    "Honua.Server.Migrations.222_CreateObjectStorageConnections.sql",
    "Honua.Server.Migrations.223_AddCloudRasterDecodeMetadata.sql",
    "Honua.Server.Migrations.224_CreateRealtimeSecurityTriggers.sql",
]
ARCHIVE_SHA256 = "221caa61e28c716fca4b5286267a2f037c2927d05ac473c9318d37be0319982d"


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
    require(manifest["schemaVersion"] == "honua-db-migration-runner-manifest-v2" and manifest["operation"] == "apply-99c4226", "manifest schema/operation drifted")
    require(manifest["sourceCommit"] == SERVER_SOURCE, "server source pin drifted")
    require(manifest["candidateImageDigest"] == CANDIDATE_IMAGE_DIGEST, "candidate image pin drifted")
    executed = manifest["executedScripts"]
    require(len(executed) == 164 and len(set(executed)) == 164 and executed == sorted(executed), "executed-script baseline size or order drifted")
    require(all(re.fullmatch(r"Honua\.(Server|Postgres)\.Migrations\.\d{3}_[A-Za-z0-9_]+\.sql", name) for name in executed), "executed-script baseline name drifted")
    require("Honua.Server.Migrations.109_AdoptConfiguredGuardedSchema.sql" not in executed, "non-default-schema adoption script admitted")
    require(hashlib.sha256("\n".join(executed).encode()).hexdigest() == EXECUTED_DIGEST == manifest["executedScriptsSha256"], "executed-script baseline digest drifted")
    entries = manifest["scripts"]
    names = [entry["name"] for entry in entries]
    require(names == PENDING_SCRIPTS, "pending script set or order drifted")
    require([entry["name"] for entry in entries if entry["phase"] == "Contract"] == CONTRACT_SCRIPTS, "Contract script set drifted")
    require(all(entry["phase"] in {"Expand", "Contract"} for entry in entries), "unknown migration phase admitted")
    require(manifest["contractApprovalNonce"] == CONTRACT_APPROVAL_NONCE, "Contract migration admitted without the reviewed approval nonce")
    require(not set(names) & set(executed), "pending script overlaps the baseline")
    require(hashlib.sha256("\n".join(names).encode()).hexdigest() == PENDING_DIGEST == manifest["pendingScriptsSha256"], "pending digest drifted")
    require(set(path.name for path in (RUNNER / "migrations").iterdir()) == {entry["file"] for entry in entries}, "migration source set drifted")
    for entry in entries:
        data = (RUNNER / "migrations" / entry["file"]).read_bytes()
        require(hashlib.sha256(data).hexdigest() == entry["sha256"], f"migration source hash drifted: {entry['file']}")
        script = json.loads(data)
        require(script["name"] == entry["name"] and script["phase"] == entry["phase"] and script["commands"], f"migration body drifted: {entry['file']}")
        declaration = script["declaration"]
        require(declaration["scriptName"] == entry["name"] and declaration["declarationFormat"] == 1 and declaration["classification"] in READER_CLASSES and (declaration["classification"] == "contract") == (entry["phase"] == "Contract"), f"reader declaration drifted: {entry['file']}")
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
