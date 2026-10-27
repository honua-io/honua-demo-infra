#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then echo "usage: $0 GOVERNANCE_SHA DEPLOYMENT_EVIDENCE_DIR" >&2; exit 64; fi
readonly GOVERNANCE_SHA="$1" DEPLOYMENT_EVIDENCE_DIR="$2"
readonly SNAPSHOT_ID="honua-demo-pre-574c804-ed316a743591"
readonly DB_ID="honua-demo-demo-postgres"
readonly SERVING_ARN="arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua:54"
readonly EVIDENCE_DIR="$HOME/.honua-runtime-proof/db-migration-574c804-$GOVERNANCE_SHA"
export AWS_MAX_ATTEMPTS=1 AWS_RETRY_MODE=standard
[[ "$GOVERNANCE_SHA" =~ ^[0-9a-f]{40}$ ]]
cd "$(git rev-parse --show-toplevel)"
test "$(git rev-parse HEAD)" = "$GOVERNANCE_SHA" && test -z "$(git status --porcelain)"
sha256sum --check "$DEPLOYMENT_EVIDENCE_DIR/final-artifacts.sha256"
readonly STATE="$DEPLOYMENT_EVIDENCE_DIR/postapply-state.json"
readonly OUTPUT="$DEPLOYMENT_EVIDENCE_DIR/postapply-output.json"
readonly QUALIFIED_ARN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["db_migration_runner_qualified_arn"]["value"])' "$OUTPUT")"
readonly VERSION="${QUALIFIED_ARN##*:}"
[[ "$QUALIFIED_ARN" =~ ^arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-db-migration-574c804:[1-9][0-9]*$ ]]
test ! -e "$EVIDENCE_DIR"; mkdir -p "$EVIDENCE_DIR"

audit() {
  local prefix="$1" status=0
  # Reserved concurrency is function-level: get-function on a qualified version ARN returns no Concurrency block.
  aws lambda get-function --function-name "$QUALIFIED_ARN" --output json \
    | jq --argjson c "$(aws lambda get-function-concurrency --function-name "${QUALIFIED_ARN%:*}" --output json)" '.Concurrency = $c' \
    | python3 scripts/assert-db-migration-runtime.py sanitize runner --state "$STATE" > "$EVIDENCE_DIR/$prefix-runner.json" || status=1
  aws lambda get-function --function-name "$SERVING_ARN" --output json | python3 scripts/assert-db-migration-runtime.py sanitize serving > "$EVIDENCE_DIR/$prefix-serving.json" || status=1
  aws lambda get-alias --function-name honua-demo-demo-honua --name live --output json | python3 scripts/assert-db-migration-runtime.py sanitize live > "$EVIDENCE_DIR/$prefix-live.json" || status=1
  aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].{DBInstanceIdentifier:DBInstanceIdentifier,DBInstanceArn:DBInstanceArn,DbiResourceId:DbiResourceId,DBInstanceStatus:DBInstanceStatus,Engine:Engine,EngineVersion:EngineVersion,StorageEncrypted:StorageEncrypted,KmsKeyId:KmsKeyId}' > "$EVIDENCE_DIR/$prefix-db.json" || status=1
  return "$status"
}

audit pre
python3 scripts/assert-db-migration-runtime.py create --runner "$EVIDENCE_DIR/pre-runner.json" --serving "$EVIDENCE_DIR/pre-serving.json" --live "$EVIDENCE_DIR/pre-live.json" --db "$EVIDENCE_DIR/pre-db.json" --state "$STATE" --receipt "$EVIDENCE_DIR/runtime.json"
set -o noclobber
printf '{"schema":"honua-db-migration-attempt-v1","attempt":1,"snapshotIdentifier":"%s","qualifiedArn":"%s"}\n' "$SNAPSHOT_ID" "$QUALIFIED_ARN" > "$EVIDENCE_DIR/attempt.json"
set +o noclobber

# Existing snapshot identity is a terminal replay guard, never an idempotent reuse.
if aws rds describe-db-snapshots --db-snapshot-identifier "$SNAPSHOT_ID" --query 'DBSnapshots[0].{DBSnapshotIdentifier:DBSnapshotIdentifier,DBSnapshotArn:DBSnapshotArn,Status:Status}' > "$EVIDENCE_DIR/existing-snapshot.json" 2> "$EVIDENCE_DIR/existing-snapshot.stderr"; then exit 1; fi
aws rds create-db-snapshot --db-instance-identifier "$DB_ID" --db-snapshot-identifier "$SNAPSHOT_ID" --tags Key=HonuaMigration,Value=574c804 Key=PendingScriptsSha256,Value=ed316a74359ebf8a72348159955745ab3032fbfe035c4453dda826c0ce7ac6a3 --query 'DBSnapshot.{DBSnapshotIdentifier:DBSnapshotIdentifier,DBSnapshotArn:DBSnapshotArn,DBInstanceIdentifier:DBInstanceIdentifier,DbiResourceId:DbiResourceId,Status:Status,SnapshotType:SnapshotType,Engine:Engine,EngineVersion:EngineVersion,Encrypted:Encrypted,KmsKeyId:KmsKeyId,TagList:TagList}' > "$EVIDENCE_DIR/snapshot-created.json"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAPSHOT_ID"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAPSHOT_ID" --query 'DBSnapshots[0].{DBSnapshotIdentifier:DBSnapshotIdentifier,DBSnapshotArn:DBSnapshotArn,DBInstanceIdentifier:DBInstanceIdentifier,DbiResourceId:DbiResourceId,Status:Status,SnapshotType:SnapshotType,Engine:Engine,EngineVersion:EngineVersion,Encrypted:Encrypted,KmsKeyId:KmsKeyId,TagList:TagList}' > "$EVIDENCE_DIR/snapshot.json"
python3 scripts/assert-db-migration-snapshot.py "$EVIDENCE_DIR/snapshot.json"

status=0
aws lambda invoke --function-name "$QUALIFIED_ARN" --cli-binary-format raw-in-base64-out --invocation-type RequestResponse --log-type None --payload '{"operation":"apply-574c804"}' "$EVIDENCE_DIR/payload.json" > "$EVIDENCE_DIR/metadata.json" || status=1
python3 scripts/assert-db-migration-result.py create --metadata "$EVIDENCE_DIR/metadata.json" --payload "$EVIDENCE_DIR/payload.json" --snapshot "$EVIDENCE_DIR/snapshot.json" --runtime "$EVIDENCE_DIR/runtime.json" --receipt "$EVIDENCE_DIR/invocation-receipt.json" || status=1
audit post || status=1
python3 scripts/assert-db-migration-runtime.py verify --runner "$EVIDENCE_DIR/post-runner.json" --serving "$EVIDENCE_DIR/post-serving.json" --live "$EVIDENCE_DIR/post-live.json" --db "$EVIDENCE_DIR/post-db.json" --state "$STATE" --receipt "$EVIDENCE_DIR/runtime.json" || status=1
python3 scripts/assert-db-migration-result.py verify --metadata "$EVIDENCE_DIR/metadata.json" --payload "$EVIDENCE_DIR/payload.json" --snapshot "$EVIDENCE_DIR/snapshot.json" --runtime "$EVIDENCE_DIR/runtime.json" --receipt "$EVIDENCE_DIR/invocation-receipt.json" || status=1
sha256sum "$EVIDENCE_DIR"/*.json > "$EVIDENCE_DIR/final-artifacts.sha256" || status=1
test "$status" -eq 0
