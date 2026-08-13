#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly TEMP_ROOT="$(mktemp -d)"
readonly MOCK_BIN="$TEMP_ROOT/bin"
readonly MOCK_LOG="$TEMP_ROOT/commands.log"
readonly MERGED_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
readonly APPLY_DIR="$TEMP_ROOT/apply"
trap 'rm -rf "$TEMP_ROOT"' EXIT
mkdir -p "$MOCK_BIN" "$APPLY_DIR"
printf '{}' > "$APPLY_DIR/final-evidence-manifest.json"

cat > "$MOCK_BIN/dispatcher" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
command_name="$(basename "$0")"
line="$command_name $*"
printf '%s\n' "$line" >> "$MOCK_LOG"
if [[ -n "${MOCK_FAIL_MATCH:-}" && "$line" == *"$MOCK_FAIL_MATCH"* ]]; then exit 71; fi
if [[ "$command_name" == git && "${1:-} ${2:-}" == "rev-parse --show-toplevel" ]]; then
  printf '%s\n' "$MOCK_ROOT"
elif [[ "$command_name" == git && "${1:-} ${2:-}" == "rev-parse HEAD" ]]; then
  printf '%s\n' "$MOCK_SHA"
elif [[ "$command_name" == python && "$line" == *"assert-candidate-preflight-ecr.py"* && "$line" == *"config-digest"* ]]; then
  printf '%s\n' 'sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f'
elif [[ "$command_name" == aws && "${1:-} ${2:-}" == "ecr get-download-url-for-layer" ]]; then
  printf '%s\n' 'https://example.invalid/config'
elif [[ "$command_name" == aws && "${1:-} ${2:-}" == "lambda invoke" ]]; then
  test "${AWS_MAX_ATTEMPTS:-}" = 1
  printf '%s\n' '{"StatusCode":200,"ExecutedVersion":"2"}'
elif [[ "$command_name" == sha256sum && "${1:-}" != --check ]]; then
  for path in "$@"; do printf '%064d  %s\n' 0 "$path"; done
fi
SH
chmod +x "$MOCK_BIN/dispatcher"
for command in git python sha256sum aws curl; do cp "$MOCK_BIN/dispatcher" "$MOCK_BIN/$command"; done
export PATH="$MOCK_BIN:$PATH"
export MOCK_LOG MOCK_ROOT="$ROOT" MOCK_SHA="$MERGED_SHA"

run_case() {
  local evidence="$1"
  bash "$ROOT/scripts/candidate-preflight-v2-invoke.sh" "$MERGED_SHA" "$evidence" "$APPLY_DIR"
}

: > "$MOCK_LOG"
run_case "$TEMP_ROOT/success"
test "$(grep -Fc 'aws lambda invoke ' "$MOCK_LOG")" -eq 1
grep -Fq 'arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:2' "$MOCK_LOG"
! grep -Fq 'terraform ' "$MOCK_LOG"
test -f "$TEMP_ROOT/success/invocation-attempt-v2.json"

if run_case "$TEMP_ROOT/success"; then echo "second invocation path unexpectedly passed" >&2; exit 1; fi
test "$(grep -Fc 'aws lambda invoke ' "$MOCK_LOG")" -eq 1

pre_failures=(
  "candidate-preflight-v2-governance-receipt.py create"
  "aws lambda get-function --function-name arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:2"
  "aws lambda get-alias"
  "aws ecr batch-get-image"
  "assert-candidate-preflight-v2-runtime.py create"
)
for failure in "${pre_failures[@]}"; do
  : > "$MOCK_LOG"; export MOCK_FAIL_MATCH="$failure"
  if run_case "$TEMP_ROOT/pre-${RANDOM}"; then echo "preinvoke failure passed: $failure" >&2; exit 1; fi
  ! grep -Fq 'aws lambda invoke ' "$MOCK_LOG"
done
unset MOCK_FAIL_MATCH

: > "$MOCK_LOG"; export MOCK_FAIL_MATCH="aws lambda invoke"
if run_case "$TEMP_ROOT/invoke-failure"; then echo "invoke failure passed" >&2; exit 1; fi
test "$(grep -Fc 'aws lambda invoke ' "$MOCK_LOG")" -eq 1
test "$(grep -Fc 'aws lambda get-function --function-name arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:2' "$MOCK_LOG")" -eq 2
test "$(grep -Fc 'aws lambda get-alias ' "$MOCK_LOG")" -eq 2
test "$(grep -Fc 'aws ecr batch-get-image ' "$MOCK_LOG")" -eq 2
echo "candidate-preflight v2 operator control flow: PASS"
