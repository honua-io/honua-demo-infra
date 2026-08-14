#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then echo "usage: $0 GOVERNANCE_SHA DEPLOYMENT_EVIDENCE_DIR PREFLIGHT_EVIDENCE_DIR" >&2; exit 64; fi
readonly GOVERNANCE_SHA="$1" DEPLOYMENT_EVIDENCE_DIR="$2" PREFLIGHT_EVIDENCE_DIR="$3"
readonly SNAPSHOT_ID="honua-demo-pre-092-105-e0ee6b49e116"
readonly DB_ID="honua-demo-demo-postgres"
readonly EVIDENCE_DIR="$HOME/.honua-runtime-proof/db-migration-092-105-$GOVERNANCE_SHA"
readonly PREFLIGHT_RECEIPT="$PREFLIGHT_EVIDENCE_DIR/invocation-receipt-v3.json"
readonly PREFLIGHT_SHA="357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee"
export AWS_MAX_ATTEMPTS=1 AWS_RETRY_MODE=standard
[[ "$GOVERNANCE_SHA" =~ ^[0-9a-f]{40}$ ]]
cd "$(git rev-parse --show-toplevel)"
test "$(git rev-parse HEAD)" = "$GOVERNANCE_SHA" && test -z "$(git status --porcelain)"
test "$(sha256sum "$PREFLIGHT_RECEIPT" | cut -d' ' -f1)" = "$PREFLIGHT_SHA"
sha256sum --check "$DEPLOYMENT_EVIDENCE_DIR/final-artifacts.sha256"
readonly STATE="$DEPLOYMENT_EVIDENCE_DIR/postapply-state.json"
readonly OUTPUT="$DEPLOYMENT_EVIDENCE_DIR/postapply-output.json"
readonly QUALIFIED_ARN="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["db_migration_runner_qualified_arn"]["value"])' "$OUTPUT")"
readonly VERSION="${QUALIFIED_ARN##*:}"
[[ "$QUALIFIED_ARN" =~ ^arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-db-migration-092-105:[1-9][0-9]*$ ]]
test ! -e "$EVIDENCE_DIR"; mkdir -p "$EVIDENCE_DIR"

audit() {
  local prefix="$1" status=0
  aws lambda get-function --function-name "$QUALIFIED_ARN" > "$EVIDENCE_DIR/$prefix-runner.json" || status=1
  aws lambda get-function --function-name arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua:40 > "$EVIDENCE_DIR/$prefix-candidate.json" || status=1
  aws lambda get-alias --function-name honua-demo-demo-honua --name live > "$EVIDENCE_DIR/$prefix-live.json" || status=1
  aws lambda get-function --function-name arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:3 > "$EVIDENCE_DIR/$prefix-preflight.json" || status=1
  aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0]' > "$EVIDENCE_DIR/$prefix-db.json" || status=1
  return "$status"
}

audit pre
python scripts/assert-db-migration-runtime.py create --runner "$EVIDENCE_DIR/pre-runner.json" --candidate "$EVIDENCE_DIR/pre-candidate.json" --live "$EVIDENCE_DIR/pre-live.json" --preflight "$EVIDENCE_DIR/pre-preflight.json" --db "$EVIDENCE_DIR/pre-db.json" --state "$STATE" --receipt "$EVIDENCE_DIR/runtime.json"
set -o noclobber
printf '{"schema":"honua-db-migration-attempt-v1","attempt":1,"snapshotIdentifier":"%s","qualifiedArn":"%s"}\n' "$SNAPSHOT_ID" "$QUALIFIED_ARN" > "$EVIDENCE_DIR/attempt.json"
set +o noclobber

# Existing snapshot identity is a terminal replay guard, never an idempotent reuse.
if aws rds describe-db-snapshots --db-snapshot-identifier "$SNAPSHOT_ID" > "$EVIDENCE_DIR/existing-snapshot.json" 2> "$EVIDENCE_DIR/existing-snapshot.stderr"; then exit 1; fi
aws rds create-db-snapshot --db-instance-identifier "$DB_ID" --db-snapshot-identifier "$SNAPSHOT_ID" --tags Key=HonuaMigration,Value=092-105 Key=PendingScriptsSha256,Value=e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7 > "$EVIDENCE_DIR/snapshot-created.json"
aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAPSHOT_ID"
aws rds describe-db-snapshots --db-snapshot-identifier "$SNAPSHOT_ID" --query 'DBSnapshots[0]' > "$EVIDENCE_DIR/snapshot.json"
python scripts/assert-db-migration-snapshot.py "$EVIDENCE_DIR/snapshot.json"

status=0
aws lambda invoke --function-name "$QUALIFIED_ARN" --cli-binary-format raw-in-base64-out --invocation-type RequestResponse --log-type None --payload '{"operation":"apply-092-105"}' "$EVIDENCE_DIR/payload.json" > "$EVIDENCE_DIR/metadata.json" || status=1
python scripts/assert-db-migration-result.py create --metadata "$EVIDENCE_DIR/metadata.json" --payload "$EVIDENCE_DIR/payload.json" --snapshot "$EVIDENCE_DIR/snapshot.json" --runtime "$EVIDENCE_DIR/runtime.json" --receipt "$EVIDENCE_DIR/invocation-receipt.json" || status=1
audit post || status=1
python scripts/assert-db-migration-runtime.py verify --runner "$EVIDENCE_DIR/post-runner.json" --candidate "$EVIDENCE_DIR/post-candidate.json" --live "$EVIDENCE_DIR/post-live.json" --preflight "$EVIDENCE_DIR/post-preflight.json" --db "$EVIDENCE_DIR/post-db.json" --state "$STATE" --receipt "$EVIDENCE_DIR/runtime.json" || status=1
python scripts/assert-db-migration-result.py verify --metadata "$EVIDENCE_DIR/metadata.json" --payload "$EVIDENCE_DIR/payload.json" --snapshot "$EVIDENCE_DIR/snapshot.json" --runtime "$EVIDENCE_DIR/runtime.json" --receipt "$EVIDENCE_DIR/invocation-receipt.json" || status=1
sha256sum "$EVIDENCE_DIR"/*.json > "$EVIDENCE_DIR/final-artifacts.sha256" || status=1
test "$status" -eq 0
