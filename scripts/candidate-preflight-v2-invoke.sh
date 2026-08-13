#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 GOVERNANCE_SHA APPLY_EVIDENCE_DIR" >&2
  exit 64
fi

readonly GOVERNANCE_SHA="$1"
readonly APPLY_EVIDENCE_DIR="$2"
readonly EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-v2-invocation-$GOVERNANCE_SHA"
readonly APPLY_MANIFEST="$APPLY_EVIDENCE_DIR/final-evidence-manifest.json"
readonly QUALIFIED_ARN="arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:2"
readonly HELPER_NAME="honua-demo-demo-candidate-preflight"
readonly ROLE_NAME="${HELPER_NAME}-role"
readonly POLICY_NAME="credential-safe-candidate-preflight-v1"
readonly IMAGE_DIGEST="sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
readonly CONFIG_DIGEST="sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f"

[[ "$GOVERNANCE_SHA" =~ ^[0-9a-f]{40}$ ]]
export AWS_MAX_ATTEMPTS=1
export AWS_RETRY_MODE=standard
readonly REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
test "$(git rev-parse HEAD)" = "$GOVERNANCE_SHA"
test -z "$(git status --porcelain)"
mkdir -p "$HOME/.honua-runtime-proof"
test ! -e "$EVIDENCE_DIR"
mkdir "$EVIDENCE_DIR"

capture_helper_audit() {
  local prefix="$1" status=0
  aws lambda get-function --function-name "$QUALIFIED_ARN" > "$EVIDENCE_DIR/$prefix-function.json" || status=1
  aws lambda get-function-concurrency --function-name "$HELPER_NAME" > "$EVIDENCE_DIR/$prefix-concurrency.json" || status=1
  aws iam get-role --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-role.json" || status=1
  aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" > "$EVIDENCE_DIR/$prefix-role-policy.json" || status=1
  aws iam list-attached-role-policies --no-paginate --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-attached-policies.json" || status=1
  aws iam list-role-policies --no-paginate --role-name "$ROLE_NAME" > "$EVIDENCE_DIR/$prefix-inline-policies.json" || status=1
  return "$status"
}

capture_app_audit() {
  local prefix="$1" status=0
  aws lambda get-function --function-name arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua:40 > "$EVIDENCE_DIR/$prefix-candidate.json" || status=1
  aws lambda get-alias --function-name honua-demo-demo-honua --name live > "$EVIDENCE_DIR/$prefix-live-alias.json" || status=1
  return "$status"
}

capture_ecr_audit() {
  local prefix="$1" status=0 config_digest config_url
  aws ecr batch-get-image --repository-name honua-server --image-ids "imageDigest=$IMAGE_DIGEST" --accepted-media-types application/vnd.oci.image.manifest.v1+json --region us-west-2 --query '{images: images[].{registryId:registryId,repositoryName:repositoryName,imageId:{imageDigest:imageId.imageDigest},imageManifest:imageManifest,imageManifestMediaType:imageManifestMediaType},failures:failures}' > "$EVIDENCE_DIR/$prefix-ecr-image.json" || return 1
  config_digest="$(python scripts/assert-candidate-preflight-ecr.py config-digest --image "$EVIDENCE_DIR/$prefix-ecr-image.json")" || return 1
  test "$config_digest" = "$CONFIG_DIGEST" || return 1
  config_url="$(aws ecr get-download-url-for-layer --repository-name honua-server --layer-digest "$config_digest" --region us-west-2 --query downloadUrl --output text)" || return 1
  [[ "$config_url" == https://* ]] || return 1
  curl --fail --silent --show-error "$config_url" > "$EVIDENCE_DIR/$prefix-ecr-config.json" || status=1
  python scripts/assert-candidate-preflight-ecr.py assert --image "$EVIDENCE_DIR/$prefix-ecr-image.json" --config "$EVIDENCE_DIR/$prefix-ecr-config.json" --evidence "$EVIDENCE_DIR/$prefix-ecr-evidence.json" || status=1
  return "$status"
}

runtime_audit() {
  local mode="$1" prefix="$2"
  python scripts/assert-candidate-preflight-v2-runtime.py "$mode" \
    --function "$EVIDENCE_DIR/$prefix-function.json" \
    --candidate "$EVIDENCE_DIR/$prefix-candidate.json" \
    --live-alias "$EVIDENCE_DIR/$prefix-live-alias.json" \
    --concurrency "$EVIDENCE_DIR/$prefix-concurrency.json" \
    --role "$EVIDENCE_DIR/$prefix-role.json" \
    --role-policy "$EVIDENCE_DIR/$prefix-role-policy.json" \
    --attached-policies "$EVIDENCE_DIR/$prefix-attached-policies.json" \
    --inline-policies "$EVIDENCE_DIR/$prefix-inline-policies.json" \
    --apply-evidence-manifest "$APPLY_MANIFEST" \
    --governance-receipt "$EVIDENCE_DIR/governance-receipt-v2.json" \
    --ecr-evidence "$EVIDENCE_DIR/$prefix-ecr-evidence.json" \
    --governance-sha "$GOVERNANCE_SHA" \
    --receipt "$EVIDENCE_DIR/runtime-receipt-v2.json"
}

governance_verify() {
  python scripts/candidate-preflight-v2-governance-receipt.py verify --governance-sha "$GOVERNANCE_SHA" --apply-evidence-manifest "$APPLY_MANIFEST" --evidence-dir "$EVIDENCE_DIR" --receipt "$EVIDENCE_DIR/governance-receipt-v2.json"
}

# Every gate before the marker is fail-fast and cannot invoke Lambda.
python scripts/candidate-preflight-v2-governance-receipt.py create --governance-sha "$GOVERNANCE_SHA" --apply-evidence-manifest "$APPLY_MANIFEST" --evidence-dir "$EVIDENCE_DIR" --receipt "$EVIDENCE_DIR/governance-receipt-v2.json"
governance_verify
capture_helper_audit preinvoke
capture_app_audit preinvoke
capture_ecr_audit preinvoke
runtime_audit create preinvoke
sha256sum "$EVIDENCE_DIR/runtime-receipt-v2.json" > "$EVIDENCE_DIR/runtime-receipt-v2.sha256"
sha256sum --check "$EVIDENCE_DIR/runtime-receipt-v2.sha256"
governance_verify
runtime_audit verify preinvoke

# Creation is exclusive and happens before the sole AWS attempt. The fixed
# evidence directory plus this marker makes any outcome terminal.
set -o noclobber
printf '{"schema":"honua-candidate-preflight-v2-attempt-v1","attempt":1,"governanceSha":"%s","qualifiedArn":"%s"}\n' "$GOVERNANCE_SHA" "$QUALIFIED_ARN" > "$EVIDENCE_DIR/invocation-attempt-v2.json"
set +o noclobber

invocation_status=0
if ! aws lambda invoke \
  --function-name "$QUALIFIED_ARN" \
  --cli-binary-format raw-in-base64-out \
  --invocation-type RequestResponse \
  --log-type None \
  --payload '{"operation":"candidate-preflight-v1"}' \
  "$EVIDENCE_DIR/invocation-payload-v2.json" \
  > "$EVIDENCE_DIR/invocation-metadata-v2.json"; then
  invocation_status=1
fi
if ! python scripts/assert-candidate-preflight-v2-invocation.py create --metadata "$EVIDENCE_DIR/invocation-metadata-v2.json" --payload "$EVIDENCE_DIR/invocation-payload-v2.json" --runtime-receipt "$EVIDENCE_DIR/runtime-receipt-v2.json" --ecr-evidence "$EVIDENCE_DIR/preinvoke-ecr-evidence.json" --receipt "$EVIDENCE_DIR/invocation-receipt-v2.json"; then invocation_status=1; fi

# Never short-circuit the feasible post-audit after the attempt.
if ! capture_helper_audit postinvoke; then invocation_status=1; fi
if ! capture_app_audit postinvoke; then invocation_status=1; fi
if ! capture_ecr_audit postinvoke; then invocation_status=1; fi
if ! runtime_audit verify postinvoke; then invocation_status=1; fi
if ! governance_verify; then invocation_status=1; fi
if ! python scripts/assert-candidate-preflight-v2-invocation.py verify --metadata "$EVIDENCE_DIR/invocation-metadata-v2.json" --payload "$EVIDENCE_DIR/invocation-payload-v2.json" --runtime-receipt "$EVIDENCE_DIR/runtime-receipt-v2.json" --ecr-evidence "$EVIDENCE_DIR/postinvoke-ecr-evidence.json" --receipt "$EVIDENCE_DIR/invocation-receipt-v2.json"; then invocation_status=1; fi
if ! sha256sum "$EVIDENCE_DIR"/*.json > "$EVIDENCE_DIR/invocation-artifacts-v2.sha256"; then invocation_status=1; fi
test "$invocation_status" -eq 0
