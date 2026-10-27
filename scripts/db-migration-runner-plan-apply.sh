#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: $0 plan MERGED_SHA EVIDENCE_DIR | $0 apply MERGED_SHA EVIDENCE_DIR REVIEWED_MANIFEST_SHA256" >&2
  exit 64
fi
readonly PHASE="$1" MERGED_SHA="$2" EVIDENCE_DIR="$3" STACK="stacks/aws-db-migration-runner"
readonly CANONICAL_ARCHIVE="$STACK/db-migration-runner.zip"
readonly ARCHIVE_SHA256="2a8be2ef98433c526876aef2c7bbf28fc31ba4a39835c340879f24873e639511"
[[ "$MERGED_SHA" =~ ^[0-9a-f]{40}$ ]]
readonly TERRAFORM="${TERRAFORM:-terraform}"
readonly ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
test "$(git rev-parse HEAD)" = "$MERGED_SHA"
test -z "$(git status --porcelain)"

if [[ "$PHASE" == "plan" ]]; then
  [[ $# -eq 3 ]]
  test ! -e "$EVIDENCE_DIR"
  mkdir -p "$EVIDENCE_DIR"
  python3 scripts/assert-db-migration-runner-source.py
  python3 "$STACK/runner/build.py" --source "$STACK/runner" --output "$EVIDENCE_DIR/runner-a.zip"
  python3 "$STACK/runner/build.py" --source "$STACK/runner" --output "$EVIDENCE_DIR/runner-b.zip"
  cmp "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip"
  test "$(sha256sum "$EVIDENCE_DIR/runner-a.zip" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
  cp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
  cmp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
  test "$(sha256sum "$CANONICAL_ARCHIVE" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
  "$TERRAFORM" -chdir="$STACK" init -input=false -lockfile=readonly
  "$TERRAFORM" -chdir="$STACK" plan -input=false -out="$EVIDENCE_DIR/runner.tfplan"
  "$TERRAFORM" -chdir="$STACK" show -json "$EVIDENCE_DIR/runner.tfplan" > "$EVIDENCE_DIR/runner.show.json"
  python3 scripts/assert-db-migration-runner-plan.py "$EVIDENCE_DIR/runner.show.json"
  sha256sum "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip" "$CANONICAL_ARCHIVE" "$EVIDENCE_DIR/runner.tfplan" "$EVIDENCE_DIR/runner.show.json" > "$EVIDENCE_DIR/reviewed-artifacts.sha256"
  test "$(git rev-parse HEAD)" = "$MERGED_SHA" && test -z "$(git status --porcelain)"
  echo "Plan prepared. Review runner.show.json, then record: sha256sum $EVIDENCE_DIR/reviewed-artifacts.sha256"
  exit 0
fi

if [[ "$PHASE" != "apply" || $# -ne 4 ]]; then
  echo "phase must be plan or apply" >&2
  exit 64
fi
readonly REVIEWED_MANIFEST_SHA256="$4"
[[ "$REVIEWED_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]
test -d "$EVIDENCE_DIR"
test "$(sha256sum "$EVIDENCE_DIR/reviewed-artifacts.sha256" | cut -d' ' -f1)" = "$REVIEWED_MANIFEST_SHA256"
sha256sum --check "$EVIDENCE_DIR/reviewed-artifacts.sha256"
cmp "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip"
cmp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
test "$(sha256sum "$CANONICAL_ARCHIVE" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
"$TERRAFORM" -chdir="$STACK" show -json "$EVIDENCE_DIR/runner.tfplan" > "$EVIDENCE_DIR/preapply.show.json"
cmp "$EVIDENCE_DIR/runner.show.json" "$EVIDENCE_DIR/preapply.show.json"
python3 scripts/assert-db-migration-runner-plan.py "$EVIDENCE_DIR/preapply.show.json"
"$TERRAFORM" -chdir="$STACK" apply "$EVIDENCE_DIR/runner.tfplan"
"$TERRAFORM" -chdir="$STACK" output -json > "$EVIDENCE_DIR/postapply-output.json"
"$TERRAFORM" -chdir="$STACK" state pull > "$EVIDENCE_DIR/postapply-state.json"
sha256sum "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/final-artifacts.sha256"
