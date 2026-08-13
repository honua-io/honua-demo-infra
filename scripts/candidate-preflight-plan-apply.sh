#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 MERGED_SHA EVIDENCE_DIR" >&2
  exit 64
fi

readonly MERGED_SHA="$1"
readonly EVIDENCE_DIR="$2"
readonly ARCHIVE="stacks/aws-candidate-preflight/candidate-preflight.zip"
readonly ARCHIVE_SHA256="52e879d531b3fc94cf08921b2fb140c6d02c8e5e18e36bb5284c7b52da2c8554"

[[ "$MERGED_SHA" =~ ^[0-9a-f]{40}$ ]]
readonly REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
mkdir -p "$EVIDENCE_DIR"

assert_clean_checkout() {
  test "$(git rev-parse HEAD)" = "$MERGED_SHA"
  git diff --exit-code
  git diff --cached --exit-code
  test -z "$(git status --porcelain)"
}

assert_clean_checkout
terraform -chdir=stacks/aws-candidate-preflight init -input=false -lockfile=readonly
terraform -chdir=stacks/aws-candidate-preflight plan -refresh=false -input=false -out="$EVIDENCE_DIR/candidate-preflight.tfplan"
terraform -chdir=stacks/aws-candidate-preflight show -json "$EVIDENCE_DIR/candidate-preflight.tfplan" > "$EVIDENCE_DIR/candidate-preflight.show.json"
python scripts/assert-candidate-preflight-plan.py "$EVIDENCE_DIR/candidate-preflight.show.json"
archive_hash="$(sha256sum "$ARCHIVE")"
test "${archive_hash%% *}" = "$ARCHIVE_SHA256"
python scripts/candidate-preflight-plan-receipt.py create \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive "$ARCHIVE" \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
sha256sum \
  "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  "$EVIDENCE_DIR/candidate-preflight.show.json" \
  "$ARCHIVE" \
  "$EVIDENCE_DIR/plan-receipt.json" \
  > "$EVIDENCE_DIR/reviewed-artifacts.sha256"

# Every command above and below this point is fail-fast. No failed gate can
# reach the exact saved-plan apply.
assert_clean_checkout
sha256sum --check "$EVIDENCE_DIR/reviewed-artifacts.sha256"
python scripts/candidate-preflight-plan-receipt.py verify \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive "$ARCHIVE" \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
terraform -chdir=stacks/aws-candidate-preflight show -json "$EVIDENCE_DIR/candidate-preflight.tfplan" > "$EVIDENCE_DIR/preapply.show.json"
python scripts/assert-candidate-preflight-plan.py "$EVIDENCE_DIR/preapply.show.json"
cmp "$EVIDENCE_DIR/candidate-preflight.show.json" "$EVIDENCE_DIR/preapply.show.json"
terraform -chdir=stacks/aws-candidate-preflight apply "$EVIDENCE_DIR/candidate-preflight.tfplan"
