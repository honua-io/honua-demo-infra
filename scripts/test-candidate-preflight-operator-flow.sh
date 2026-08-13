#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly TEMP_ROOT="$(mktemp -d)"
readonly MOCK_BIN="$TEMP_ROOT/bin"
readonly MOCK_LOG="$TEMP_ROOT/commands.log"
readonly MOCK_COUNT="$TEMP_ROOT/failure-count"
readonly MERGED_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
readonly DEPLOYMENT_SHA="3a00dfd36c298def8f8f49757dd56595d29097cb"
readonly DEPLOYMENT_ROOT="$TEMP_ROOT/candidate-preflight-plan-3a00dfd3"
trap 'rm -rf "$TEMP_ROOT"' EXIT
mkdir -p "$MOCK_BIN"
mkdir -p "$DEPLOYMENT_ROOT"

cat > "$MOCK_BIN/dispatcher" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
line = " ".join([command, *args])
log = Path(os.environ["MOCK_LOG"])
with log.open("a", encoding="utf-8") as stream:
    stream.write(line + "\n")

match = os.environ.get("MOCK_FAIL_MATCH", "")
if match and match in line:
    count_path = Path(os.environ["MOCK_COUNT"])
    count = int(count_path.read_text(encoding="utf-8")) + 1 if count_path.exists() else 1
    count_path.write_text(str(count), encoding="utf-8")
    if count == int(os.environ.get("MOCK_FAIL_AT", "1")):
        raise SystemExit(71)

if command == "git" and args[:2] == ["rev-parse", "--show-toplevel"]:
    print(os.environ["MOCK_REPO_ROOT"])
elif command == "git" and args[:2] == ["rev-parse", "HEAD"]:
    print(os.environ["MOCK_MERGED_SHA"])
elif command == "terraform" and "output" in args:
    if args[-1] == "candidate_preflight_qualified_arn":
        version = os.environ.get("MOCK_HELPER_VERSION", "1")
        print(f"arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:{version}")
    elif args[-1] == "candidate_preflight_version":
        print(os.environ.get("MOCK_HELPER_VERSION", "1"))
elif command == "terraform" and "show" in args:
    print("{}")
elif command == "python" and "assert-candidate-preflight-ecr.py" in line and "config-digest" in line:
    print("sha256:c57f3a4ad93a67b9d25c8c56b8f24a144191d2ce94be5de37e10f99ff774f63f")
elif command == "aws" and args[:2] == ["ecr", "get-download-url-for-layer"]:
    print("https://example.invalid/exact-config")
elif command == "sha256sum" and (not args or args[0] != "--check"):
    digest = os.environ.get("MOCK_ARCHIVE_SHA256", "4eebc158663051c270cf989bbd385581e2b75245b0ddd0f76fb01a90e7c99da0")
    for path in args:
        print(digest + "  " + path)
elif command == "sha256sum" and args and args[0] == "--check":
    content = sys.stdin.read()
    if content and "postapply-deployment-receipt.json" not in content:
        raise SystemExit(73)
elif command == "aws" and args[:2] == ["lambda", "invoke"]:
    if os.environ.get("AWS_MAX_ATTEMPTS") != "1":
        raise SystemExit(72)
    print('{"StatusCode":200,"ExecutedVersion":"1"}')
PY
chmod +x "$MOCK_BIN/dispatcher"
for command in git terraform python sha256sum cmp aws; do
  cp "$MOCK_BIN/dispatcher" "$MOCK_BIN/$command"
done
cp "$MOCK_BIN/dispatcher" "$MOCK_BIN/curl"

export PATH="$MOCK_BIN:$PATH"
export MOCK_LOG MOCK_COUNT
export MOCK_REPO_ROOT="$ROOT"
export MOCK_MERGED_SHA="$MERGED_SHA"

reset_case() {
  : > "$MOCK_LOG"
  rm -f "$MOCK_COUNT"
  unset MOCK_FAIL_MATCH MOCK_FAIL_AT MOCK_HELPER_VERSION MOCK_ARCHIVE_SHA256
}

run_script() {
  local script="$1"
  local evidence="$2"
  mkdir -p "$evidence"
  printf '%s' 'sealed-v1-deployment-receipt' > "$evidence/postapply-deployment-receipt.json"
  set +e
  if [[ "$script" == *candidate-preflight-invoke.sh ]]; then
    bash "$script" "$MERGED_SHA" "$DEPLOYMENT_SHA" "$evidence" "$DEPLOYMENT_ROOT"
  else
    bash "$script" "$MERGED_SHA" "$evidence"
  fi
  local status=$?
  set -e
  return "$status"
}

assert_no_apply() {
  ! grep -Fq "terraform -chdir=stacks/aws-candidate-preflight apply " "$MOCK_LOG"
}

assert_no_invoke() {
  ! grep -Fq "aws lambda invoke " "$MOCK_LOG"
}

reset_case
export MOCK_ARCHIVE_SHA256="b4715ea1256a9bf139088b2764d45d2859ed734d063fb4a0fe532bb68a61e299"
run_script "$ROOT/scripts/candidate-preflight-plan-apply.sh" "$TEMP_ROOT/plan-success"
grep -Fq "terraform -chdir=stacks/aws-candidate-preflight apply " "$MOCK_LOG"

plan_failures=(
  "terraform -chdir=stacks/aws-candidate-preflight init|1"
  "terraform -chdir=stacks/aws-candidate-preflight plan|1"
  "python scripts/assert-candidate-preflight-plan.py|1"
  "python scripts/candidate-preflight-plan-receipt.py create|1"
  "git diff --exit-code|2"
  "sha256sum --check|1"
  "python scripts/candidate-preflight-plan-receipt.py verify|1"
  "terraform -chdir=stacks/aws-candidate-preflight show|2"
  "python scripts/assert-candidate-preflight-plan.py|2"
  "cmp |1"
)
for fixture in "${plan_failures[@]}"; do
  reset_case
  export MOCK_ARCHIVE_SHA256="b4715ea1256a9bf139088b2764d45d2859ed734d063fb4a0fe532bb68a61e299"
  export MOCK_FAIL_MATCH="${fixture%|*}"
  export MOCK_FAIL_AT="${fixture##*|}"
  if run_script "$ROOT/scripts/candidate-preflight-plan-apply.sh" "$TEMP_ROOT/plan-failure"; then
    echo "expected plan/apply gate failure: $fixture" >&2
    exit 1
  fi
  assert_no_apply
done

reset_case
run_script "$ROOT/scripts/candidate-preflight-invoke.sh" "$TEMP_ROOT/invoke-success"
grep -Fq "aws lambda invoke " "$MOCK_LOG"
test "$(grep -Fc "aws lambda invoke " "$MOCK_LOG")" -eq 1
test "$(grep -Fc "aws lambda get-function --" "$MOCK_LOG")" -eq 3
test "$(grep -Fc "aws ecr batch-get-image " "$MOCK_LOG")" -eq 3
test "$(grep -Fc "aws iam list-attached-role-policies --no-paginate " "$MOCK_LOG")" -eq 3
test "$(grep -Fc "aws iam list-role-policies --no-paginate " "$MOCK_LOG")" -eq 3
test "$(grep -Fc "python scripts/candidate-preflight-governance-receipt.py verify " "$MOCK_LOG")" -eq 4
grep -Fq "sha256sum --check" "$MOCK_LOG"

reset_case
mkdir -p "$TEMP_ROOT/missing-historical"
if bash "$ROOT/scripts/candidate-preflight-invoke.sh" "$MERGED_SHA" "$DEPLOYMENT_SHA" "$TEMP_ROOT/missing-historical" "$DEPLOYMENT_ROOT"; then
  echo "expected missing exact historical receipt failure" >&2
  exit 1
fi
assert_no_invoke

reset_case
if bash "$ROOT/scripts/candidate-preflight-invoke.sh" "$MERGED_SHA" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" "$TEMP_ROOT/wrong-deployment" "$DEPLOYMENT_ROOT"; then
  echo "expected wrong deployment binding failure" >&2
  exit 1
fi
assert_no_invoke

reset_case
export MOCK_HELPER_VERSION=2
if run_script "$ROOT/scripts/candidate-preflight-invoke.sh" "$TEMP_ROOT/later-helper"; then
  echo "expected helper version 2 failure" >&2
  exit 1
fi
assert_no_invoke

reset_case
if bash "$ROOT/scripts/candidate-preflight-invoke.sh" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" "$DEPLOYMENT_SHA" "$TEMP_ROOT/wrong-governance" "$DEPLOYMENT_ROOT"; then
  echo "expected wrong governance binding failure" >&2
  exit 1
fi
assert_no_invoke

preinvoke_failures=(
  "python scripts/candidate-preflight-governance-receipt.py create|1"
  "python scripts/candidate-preflight-governance-receipt.py verify|1"
  "python scripts/materialize-candidate-preflight-archive.py|1"
  "python scripts/candidate-preflight-plan-receipt.py verify|1"
  "terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_qualified_arn|1"
  "aws lambda get-function --|1"
  "aws ecr batch-get-image|1"
  "python scripts/assert-candidate-preflight-ecr.py|1"
  "python scripts/assert-candidate-preflight-runtime.py create|1"
  "sha256sum --check|1"
  "python scripts/candidate-preflight-plan-receipt.py verify|2"
  "aws lambda get-function --|2"
  "aws ecr batch-get-image|2"
  "python scripts/assert-candidate-preflight-runtime.py verify|1"
)
for fixture in "${preinvoke_failures[@]}"; do
  reset_case
  export MOCK_FAIL_MATCH="${fixture%|*}"
  export MOCK_FAIL_AT="${fixture##*|}"
  if run_script "$ROOT/scripts/candidate-preflight-invoke.sh" "$TEMP_ROOT/preinvoke-failure"; then
    echo "expected pre-invocation gate failure: $fixture" >&2
    exit 1
  fi
  assert_no_invoke
done

postinvoke_failures=(
  "aws lambda invoke|1"
  "python scripts/assert-candidate-preflight-invocation.py|1"
  "aws lambda get-function --|3"
  "aws ecr batch-get-image|3"
  "python scripts/assert-candidate-preflight-runtime.py verify|2"
)
for fixture in "${postinvoke_failures[@]}"; do
  reset_case
  export MOCK_FAIL_MATCH="${fixture%|*}"
  export MOCK_FAIL_AT="${fixture##*|}"
  if run_script "$ROOT/scripts/candidate-preflight-invoke.sh" "$TEMP_ROOT/postinvoke-failure"; then
    echo "expected invocation/post-audit failure: $fixture" >&2
    exit 1
  fi
  grep -Fq "aws lambda invoke " "$MOCK_LOG"
  test "$(grep -Fc "aws lambda invoke " "$MOCK_LOG")" -eq 1
  test "$(grep -Fc "aws lambda get-function --" "$MOCK_LOG")" -eq 3
  test "$(grep -Fc "aws ecr batch-get-image " "$MOCK_LOG")" -eq 3
done

echo "candidate-preflight operator control flow: PASS"
