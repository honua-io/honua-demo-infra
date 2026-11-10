#!/usr/bin/env bash
# Plan one stack the way CI does: prebuild, init, saved plan, redacted report.
#
# Usage: scripts/stack-plan.sh STACK OUT_DIR [--no-lock]
#
# Reads stacks/drift-stacks.json for the stack's prebuild commands, var file
# and whether it needs TF_VAR_honua_admin_password (the caller exports it).
# Writes OUT_DIR/tfplan, plan.json, report.md and status (clean|changes).
# The plan's text output goes to OUT_DIR/plan.txt and is never printed: this
# repository's Actions logs are public. For stacks/aws it also runs
# scripts/check-lambda-env-budget.py on the plan (AWS rejects a Lambda
# environment over 4096 bytes at apply time, after other changes have landed).
#
# Exit 0 whether or not the plan has changes; non-zero on any error.
set -euo pipefail

stack="${1:?usage: stack-plan.sh STACK OUT_DIR [--no-lock]}"
out="${2:?usage: stack-plan.sh STACK OUT_DIR [--no-lock]}"
lock_flag="-lock-timeout=10m"
[[ "${3:-}" == "--no-lock" ]] && lock_flag="-lock=false"

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$repo/stacks/$stack"
get() { python3 "$repo/scripts/drift-stacks.py" --get "$stack" "$1"; }

mkdir -p "$out"
out="$(cd "$out" && pwd)"

cd "$repo"
get prebuild | python3 -c 'import json,sys; [print(c) for c in json.load(sys.stdin)]' | while IFS= read -r cmd; do
  echo "+ $cmd"
  bash -c "$cmd"
done

if [[ "$(get admin_password)" == "true" && -z "${TF_VAR_honua_admin_password:-}" ]]; then
  echo "::error::stacks/$stack needs TF_VAR_honua_admin_password; without it the plan replaces the admin secret version" >&2
  exit 1
fi

terraform -chdir="$root" init -input=false -no-color >"$out/init.txt" 2>&1 || {
  grep -E 'Error' "$out/init.txt" >&2 || true
  exit 1
}

args=(-input=false -no-color "$lock_flag" -detailed-exitcode -out="$out/tfplan")
var_file="$(get var_file)"
[[ -n "$var_file" ]] && args+=("-var-file=$var_file")

set +e
terraform -chdir="$root" plan "${args[@]}" >"$out/plan.txt"
code=$?
set -e
echo "terraform plan (stacks/$stack) exit code: $code"
if [[ "$code" != 0 && "$code" != 2 ]]; then
  grep -E '^(Error|│ Error)' "$out/plan.txt" >&2 || true
  exit 1
fi

terraform -chdir="$root" show -json "$out/tfplan" >"$out/plan.json"

if [[ "$stack" == "aws" ]]; then
  set +e
  python3 "$repo/scripts/check-lambda-env-budget.py" <"$out/plan.json" | tee "$out/env-budget.txt"
  budget=${PIPESTATUS[0]}
  set -e
  if [[ "$budget" == 1 ]]; then
    echo "::error::the serving Lambda environment exceeds the AWS 4096-byte limit; apply would fail part-way" >&2
    exit 1
  fi
fi

set +e
python3 "$repo/scripts/plan-report.py" --plan "$out/plan.json" --stack "$stack" \
  --title "stacks/$stack" --out "$out/report.md" >/dev/null
report=$?
set -e
case "$report" in
  0) echo clean >"$out/status" ;;
  2) echo changes >"$out/status" ;;
  *) exit 1 ;;
esac
echo "stacks/$stack: $(cat "$out/status")"
