#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 MERGED_SHA EVIDENCE_DIR" >&2
  exit 64
fi

readonly MERGED_SHA="$1"
readonly EVIDENCE_DIR="$2"
readonly ARCHIVE="stacks/aws-candidate-preflight/candidate-preflight.zip"
readonly HELPER_NAME="honua-demo-demo-candidate-preflight"
readonly ROLE_NAME="${HELPER_NAME}-role"
readonly POLICY_NAME="credential-safe-candidate-preflight-v1"

[[ "$MERGED_SHA" =~ ^[0-9a-f]{40}$ ]]
readonly REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

capture_helper_audit() {
  local prefix="$1"
  aws lambda get-function --function-name "$QUALIFIED_ARN" > "$EVIDENCE_DIR/$prefix-function.json" || return
  aws lambda get-function-concurrency --function-name "$HELPER_NAME" > "$EVIDENCE_DIR/$prefix-concurrency.json" || return
  aws iam get-role --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-role.json" || return
  aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" > "$EVIDENCE_DIR/$prefix-role-policy.json" || return
  aws iam list-attached-role-policies --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-attached-policies.json" || return
  aws iam list-role-policies --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-inline-policies.json" || return
}

runtime_audit() {
  local mode="$1"
  local prefix="$2"
  python scripts/assert-candidate-preflight-runtime.py "$mode" \
    --function "$EVIDENCE_DIR/$prefix-function.json" \
    --concurrency "$EVIDENCE_DIR/$prefix-concurrency.json" \
    --role "$EVIDENCE_DIR/$prefix-role.json" \
    --role-policy "$EVIDENCE_DIR/$prefix-role-policy.json" \
    --attached-policies "$EVIDENCE_DIR/$prefix-attached-policies.json" \
    --inline-policies "$EVIDENCE_DIR/$prefix-inline-policies.json" \
    --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
    --merged-sha "$MERGED_SHA" \
    --receipt "$EVIDENCE_DIR/deployment-receipt.json"
}

plan_receipt_verify() {
  python scripts/candidate-preflight-plan-receipt.py verify \
    --merged-sha "$MERGED_SHA" \
    --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
    --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
    --archive "$ARCHIVE" \
    --receipt "$EVIDENCE_DIR/plan-receipt.json"
}

# Post-apply and pre-invocation gates are entirely fail-fast. Any failure here
# exits before aws lambda invoke is reachable.
plan_receipt_verify
readonly QUALIFIED_ARN="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_qualified_arn)"
readonly HELPER_VERSION="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_version)"
test "$QUALIFIED_ARN" = "arn:aws:lambda:us-west-2:585192672263:function:$HELPER_NAME:$HELPER_VERSION"

capture_helper_audit postapply
runtime_audit create postapply
sha256sum "$EVIDENCE_DIR/deployment-receipt.json" > "$EVIDENCE_DIR/deployment-receipt.sha256"

sha256sum --check "$EVIDENCE_DIR/deployment-receipt.sha256"
plan_receipt_verify
capture_helper_audit preinvoke
runtime_audit verify preinvoke

# Controlled aggregation begins only at invocation. Invocation and semantic
# failures must not prevent the post-invocation audit, but any failure remains
# fatal after every feasible post-audit step has been attempted.
invocation_status=0
if ! aws lambda invoke \
  --function-name "$QUALIFIED_ARN" \
  --cli-binary-format raw-in-base64-out \
  --invocation-type RequestResponse \
  --log-type None \
  --payload '{"operation":"candidate-preflight-v1"}' \
  "$EVIDENCE_DIR/invocation-payload.json" \
  > "$EVIDENCE_DIR/invocation-metadata.json"; then
  invocation_status=1
fi
if ! python scripts/assert-candidate-preflight-invocation.py \
  --metadata "$EVIDENCE_DIR/invocation-metadata.json" \
  --payload "$EVIDENCE_DIR/invocation-payload.json" \
  --expected-version "$HELPER_VERSION"; then
  invocation_status=1
fi
if ! capture_helper_audit postinvoke; then
  invocation_status=1
fi
if ! runtime_audit verify postinvoke; then
  invocation_status=1
fi
if ! sha256sum \
  "$EVIDENCE_DIR/invocation-metadata.json" \
  "$EVIDENCE_DIR/invocation-payload.json" \
  > "$EVIDENCE_DIR/invocation-artifacts.sha256"; then
  invocation_status=1
fi
test "$invocation_status" -eq 0
