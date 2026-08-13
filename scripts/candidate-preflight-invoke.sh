#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 GOVERNANCE_SHA DEPLOYMENT_SHA EVIDENCE_DIR DEPLOYMENT_ROOT" >&2
  exit 64
fi

readonly GOVERNANCE_SHA="$1"
readonly DEPLOYMENT_SHA="$2"
readonly EVIDENCE_DIR="$3"
readonly DEPLOYMENT_ROOT="$4"
readonly ARCHIVE="stacks/aws-candidate-preflight/candidate-preflight.zip"
readonly HELPER_NAME="honua-demo-demo-candidate-preflight"
readonly ROLE_NAME="${HELPER_NAME}-role"
readonly POLICY_NAME="credential-safe-candidate-preflight-v1"
readonly IMAGE_DIGEST="sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
readonly CONFIG_DIGEST="sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f"
readonly HISTORICAL_DEPLOYMENT_RECEIPT_SHA256="20082f457f4b44ed52db6bb5b634d8559c88c8d3dde0af7201099e789b222a29"

[[ "$GOVERNANCE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$DEPLOYMENT_SHA" = "3a00dfd36c298def8f8f49757dd56595d29097cb"
test "$GOVERNANCE_SHA" != "$DEPLOYMENT_SHA"
export AWS_MAX_ATTEMPTS=1
export AWS_RETRY_MODE=standard
readonly REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
test "$(git rev-parse HEAD)" = "$GOVERNANCE_SHA"
test -z "$(git status --porcelain)"
test -f "$EVIDENCE_DIR/postapply-deployment-receipt.json"
echo "$HISTORICAL_DEPLOYMENT_RECEIPT_SHA256  $EVIDENCE_DIR/postapply-deployment-receipt.json" | sha256sum --check

capture_helper_audit() {
  local prefix="$1"
  aws lambda get-function --function-name "$QUALIFIED_ARN" > "$EVIDENCE_DIR/$prefix-function.json" || return
  aws lambda get-function-concurrency --function-name "$HELPER_NAME" > "$EVIDENCE_DIR/$prefix-concurrency.json" || return
  aws iam get-role --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-role.json" || return
  aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" > "$EVIDENCE_DIR/$prefix-role-policy.json" || return
  aws iam list-attached-role-policies --no-paginate --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-attached-policies.json" || return
  aws iam list-role-policies --no-paginate --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-inline-policies.json" || return
}

capture_ecr_audit() {
  local prefix="$1"
  aws ecr batch-get-image \
    --repository-name honua-server \
    --image-ids "imageDigest=$IMAGE_DIGEST" \
    --accepted-media-types application/vnd.oci.image.manifest.v1+json \
    --region us-west-2 \
    --query '{images: images[].{registryId:registryId,repositoryName:repositoryName,imageId:{imageDigest:imageId.imageDigest},imageManifest:imageManifest,imageManifestMediaType:imageManifestMediaType},failures:failures}' \
    > "$EVIDENCE_DIR/$prefix-ecr-image.json" || return
  local config_digest
  config_digest="$(python scripts/assert-candidate-preflight-ecr.py config-digest \
    --image "$EVIDENCE_DIR/$prefix-ecr-image.json")" || return
  test "$config_digest" = "$CONFIG_DIGEST" || return
  local config_url
  config_url="$(aws ecr get-download-url-for-layer \
    --repository-name honua-server \
    --layer-digest "$config_digest" \
    --region us-west-2 \
    --query downloadUrl \
    --output text)" || return
  [[ "$config_url" == https://* ]] || return
  curl --fail --silent --show-error "$config_url" > "$EVIDENCE_DIR/$prefix-ecr-config.json" || return
  python scripts/assert-candidate-preflight-ecr.py assert \
    --image "$EVIDENCE_DIR/$prefix-ecr-image.json" \
    --config "$EVIDENCE_DIR/$prefix-ecr-config.json" \
    --evidence "$EVIDENCE_DIR/$prefix-ecr-evidence.json"
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
    --ecr-evidence "$EVIDENCE_DIR/$prefix-ecr-evidence.json" \
    --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
    --governance-receipt "$EVIDENCE_DIR/governance-receipt.json" \
    --historical-deployment-receipt "$EVIDENCE_DIR/postapply-deployment-receipt.json" \
    --governance-sha "$GOVERNANCE_SHA" \
    --deployment-sha "$DEPLOYMENT_SHA" \
    --receipt "$EVIDENCE_DIR/governed-deployment-receipt.json"
}

plan_receipt_verify() {
  python scripts/candidate-preflight-plan-receipt.py verify \
    --merged-sha "$DEPLOYMENT_SHA" \
    --checkout-sha "$GOVERNANCE_SHA" \
    --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
    --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
    --archive "$ARCHIVE" \
    --receipt "$EVIDENCE_DIR/plan-receipt.json"
}

governance_receipt_verify() {
  python scripts/candidate-preflight-governance-receipt.py verify \
    --governance-sha "$GOVERNANCE_SHA" \
    --deployment-sha "$DEPLOYMENT_SHA" \
    --historical-deployment-receipt "$EVIDENCE_DIR/postapply-deployment-receipt.json" \
    --receipt "$EVIDENCE_DIR/governance-receipt.json"
}

# Post-apply and pre-invocation gates are entirely fail-fast. Any failure here
# exits before aws lambda invoke is reachable.
python scripts/candidate-preflight-governance-receipt.py create \
  --governance-sha "$GOVERNANCE_SHA" \
  --deployment-sha "$DEPLOYMENT_SHA" \
  --historical-deployment-receipt "$EVIDENCE_DIR/postapply-deployment-receipt.json" \
  --receipt "$EVIDENCE_DIR/governance-receipt.json"
governance_receipt_verify
python scripts/materialize-candidate-preflight-archive.py \
  --governance-sha "$GOVERNANCE_SHA" \
  --deployment-sha "$DEPLOYMENT_SHA" \
  --deployment-root "$DEPLOYMENT_ROOT" \
  --governance-receipt "$EVIDENCE_DIR/governance-receipt.json" \
  --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
  --receipt "$EVIDENCE_DIR/archive-materialization-receipt.json"
plan_receipt_verify
readonly QUALIFIED_ARN="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_qualified_arn)"
readonly HELPER_VERSION="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_version)"
test "$HELPER_VERSION" = "1"
test "$QUALIFIED_ARN" = "arn:aws:lambda:us-west-2:585192672263:function:$HELPER_NAME:1"

capture_helper_audit postapply
capture_ecr_audit postapply
runtime_audit create postapply
sha256sum "$EVIDENCE_DIR/governed-deployment-receipt.json" > "$EVIDENCE_DIR/governed-deployment-receipt.sha256"

sha256sum --check "$EVIDENCE_DIR/governed-deployment-receipt.sha256"
governance_receipt_verify
plan_receipt_verify
capture_helper_audit preinvoke
capture_ecr_audit preinvoke
runtime_audit verify preinvoke
governance_receipt_verify

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
if ! python scripts/assert-candidate-preflight-invocation.py create \
  --metadata "$EVIDENCE_DIR/invocation-metadata.json" \
  --payload "$EVIDENCE_DIR/invocation-payload.json" \
  --expected-version "$HELPER_VERSION" \
  --deployment-receipt "$EVIDENCE_DIR/governed-deployment-receipt.json" \
  --ecr-evidence "$EVIDENCE_DIR/preinvoke-ecr-evidence.json" \
  --receipt "$EVIDENCE_DIR/invocation-receipt.json"; then
  invocation_status=1
fi
if ! capture_helper_audit postinvoke; then
  invocation_status=1
fi
if ! capture_ecr_audit postinvoke; then
  invocation_status=1
fi
if ! runtime_audit verify postinvoke; then
  invocation_status=1
fi
if ! governance_receipt_verify; then
  invocation_status=1
fi
if ! python scripts/assert-candidate-preflight-invocation.py verify \
  --metadata "$EVIDENCE_DIR/invocation-metadata.json" \
  --payload "$EVIDENCE_DIR/invocation-payload.json" \
  --expected-version "$HELPER_VERSION" \
  --deployment-receipt "$EVIDENCE_DIR/governed-deployment-receipt.json" \
  --ecr-evidence "$EVIDENCE_DIR/postinvoke-ecr-evidence.json" \
  --receipt "$EVIDENCE_DIR/invocation-receipt.json"; then
  invocation_status=1
fi
if ! sha256sum \
  "$EVIDENCE_DIR/invocation-metadata.json" \
  "$EVIDENCE_DIR/invocation-payload.json" \
  "$EVIDENCE_DIR/invocation-receipt.json" \
  "$EVIDENCE_DIR/archive-materialization-receipt.json" \
  "$EVIDENCE_DIR/governance-receipt.json" \
  "$EVIDENCE_DIR/governed-deployment-receipt.json" \
  "$EVIDENCE_DIR/postinvoke-ecr-evidence.json" \
  > "$EVIDENCE_DIR/invocation-artifacts.sha256"; then
  invocation_status=1
fi
test "$invocation_status" -eq 0
