#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then echo "usage: $0 MERGED_SHA EVIDENCE_DIR" >&2; exit 64; fi
readonly MERGED_SHA="$1" EVIDENCE_DIR="$2" STACK="stacks/aws-db-migration-runner"
readonly CANONICAL_ARCHIVE="$STACK/db-migration-runner.zip"
readonly ARCHIVE_SHA256="ea935421413a4029e775cb26346a30f38b202fe6fdc341af47de328ee16d256a"
[[ "$MERGED_SHA" =~ ^[0-9a-f]{40}$ ]]
readonly ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
test "$(git rev-parse HEAD)" = "$MERGED_SHA"
test -z "$(git status --porcelain)"
test ! -e "$EVIDENCE_DIR"
mkdir -p "$EVIDENCE_DIR"
python scripts/assert-db-migration-runner-source.py
python "$STACK/runner/build.py" --source "$STACK/runner" --output "$EVIDENCE_DIR/runner-a.zip"
python "$STACK/runner/build.py" --source "$STACK/runner" --output "$EVIDENCE_DIR/runner-b.zip"
cmp "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip"
test "$(sha256sum "$EVIDENCE_DIR/runner-a.zip" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
cp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
cmp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
test "$(sha256sum "$CANONICAL_ARCHIVE" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
terraform -chdir="$STACK" init -input=false -lockfile=readonly
terraform -chdir="$STACK" plan -input=false -out="$EVIDENCE_DIR/runner.tfplan"
terraform -chdir="$STACK" show -json "$EVIDENCE_DIR/runner.tfplan" > "$EVIDENCE_DIR/runner.show.json"
python scripts/assert-db-migration-runner-plan.py "$EVIDENCE_DIR/runner.show.json"
sha256sum "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip" "$CANONICAL_ARCHIVE" "$EVIDENCE_DIR/runner.tfplan" "$EVIDENCE_DIR/runner.show.json" > "$EVIDENCE_DIR/reviewed-artifacts.sha256"
test "$(git rev-parse HEAD)" = "$MERGED_SHA" && test -z "$(git status --porcelain)"
sha256sum --check "$EVIDENCE_DIR/reviewed-artifacts.sha256"
cmp "$EVIDENCE_DIR/runner-a.zip" "$EVIDENCE_DIR/runner-b.zip"
cmp "$EVIDENCE_DIR/runner-a.zip" "$CANONICAL_ARCHIVE"
test "$(sha256sum "$CANONICAL_ARCHIVE" | cut -d' ' -f1)" = "$ARCHIVE_SHA256"
terraform -chdir="$STACK" show -json "$EVIDENCE_DIR/runner.tfplan" > "$EVIDENCE_DIR/preapply.show.json"
cmp "$EVIDENCE_DIR/runner.show.json" "$EVIDENCE_DIR/preapply.show.json"
python scripts/assert-db-migration-runner-plan.py "$EVIDENCE_DIR/preapply.show.json"
terraform -chdir="$STACK" apply "$EVIDENCE_DIR/runner.tfplan"
terraform -chdir="$STACK" output -json > "$EVIDENCE_DIR/postapply-output.json"
terraform -chdir="$STACK" state pull > "$EVIDENCE_DIR/postapply-state.json"
sha256sum "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/final-artifacts.sha256"
