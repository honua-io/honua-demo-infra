# Candidate preflight v1

This is an **operator-invoked**, credential-safe read-only preflight for one
immutable Honua Lambda candidate. Terraform creates the wrapper but never
invokes it. The wrapper accepts exactly:

```json
{"operation":"candidate-preflight-v1"}
```

It is pinned to candidate version `40`, candidate revision
`0326e209-4231-4acd-9bb4-d3cb89402db0`, source
`7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad`, and image digest
`sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861`.
It also guards the pre- and post-probe `live` alias at version `39`, revision
`4f73dd76-0294-44d3-8362-c6f8606f034e`, with no routing configuration.

## Safety boundary

The wrapper reads only the exact module-owned admin-password secret, reads the
exact qualified candidate and `live` alias metadata, invokes only candidate
`:40`, and writes only its own CloudWatch log group. It has no VPC or security
group, database, SSM, Step Functions, bootstrap, seed, unqualified Lambda,
publish, update, or alias-mutation permissions.

The password stays in Lambda memory. It is placed only into the two internal
admin request events as `X-API-Key`. The handler never logs or returns its input,
the generated events, headers, secret, raw candidate bodies, or AWS exception
text.

The four fixed GETs are:

1. `/healthz/live`
2. `/healthz/ready`
3. `/api/v1/admin/deploy/preflight?includeDiagnostics=true`
4. `/api/v1/admin/observability/migrations`

Every transport response must be successful. Liveness must be `Healthy`,
readiness must be `Ready`, both admin bodies must be valid JSON, and the runtime
pending set must exactly match migrations 092 through 105 in
`classification.v1.json`. Every classified script is `Expand`; either runtime
payload reporting a contract branch fails closed. Migration observability must
report `status=skipped`, `isReady=true`, and `isFailed=false`; the deploy
diagnostic must independently report `migration.lifecycleStatus=skipped`.

This probe does not move `live`, does not run migrations, does not seed data,
and does not produce a promotion receipt. A passing response is preflight
evidence only.

## Terraform and source boundary

The helper is owned by the dedicated `stacks/aws-candidate-preflight` root and
state key `demo/aws-demo/candidate-preflight.tfstate`. It never reads the
primary Terraform state. The exact module-owned name
`honua-demo-demo/admin-password` is unique within the fixed AWS account and
region; Terraform resolves it with metadata-only `DescribeSecret`. This returns
the authoritative generated-suffix ARN but never `SecretString`.

The deployment archive contains exactly `handler.py` and
`classification.v1.json`. Their SHA-256 values are pinned in Terraform and the
plan checker; the deterministic ZIP hash is also pinned. Never add a directory
source, third file, layer, filesystem, dead-letter destination, VPC attachment,
or copied/derived secret ARN.

After PR merge, use a clean checkout at the exact reviewed merge SHA. Set
`MERGED_SHA` to that immutable commit and require every command below to pass:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-$MERGED_SHA"
mkdir -p "$EVIDENCE_DIR"
test "$(git rev-parse HEAD)" = "$MERGED_SHA"
git diff --exit-code
git diff --cached --exit-code
test -z "$(git status --porcelain)"
terraform -chdir=stacks/aws-candidate-preflight init -input=false -lockfile=readonly
terraform -chdir=stacks/aws-candidate-preflight plan -refresh=false -input=false -out="$EVIDENCE_DIR/candidate-preflight.tfplan"
terraform -chdir=stacks/aws-candidate-preflight show -json "$EVIDENCE_DIR/candidate-preflight.tfplan" > "$EVIDENCE_DIR/candidate-preflight.show.json"
python scripts/assert-candidate-preflight-plan.py "$EVIDENCE_DIR/candidate-preflight.show.json"
test "$(sha256sum stacks/aws-candidate-preflight/candidate-preflight.zip | cut -d' ' -f1)" = "52e879d531b3fc94cf08921b2fb140c6d02c8e5e18e36bb5284c7b52da2c8554"
python scripts/candidate-preflight-plan-receipt.py create \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive stacks/aws-candidate-preflight/candidate-preflight.zip \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
sha256sum \
  "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  "$EVIDENCE_DIR/candidate-preflight.show.json" \
  stacks/aws-candidate-preflight/candidate-preflight.zip \
  "$EVIDENCE_DIR/plan-receipt.json" \
  > "$EVIDENCE_DIR/reviewed-artifacts.sha256"
```

Keep the saved plan, show JSON, generated ZIP, ZIP checksum, `MERGED_SHA`, and
plan-checker result together as one local review bundle. Plan JSON can contain
state values, so never upload it. The assertion requires an applyable supported
plan with no drift, deferral, Terraform actions/triggers/invocations, extra
outputs, child modules, or resources beyond the exact helper graph. It verifies
the exact role ARN, least-privilege IAM, environment, source member hashes,
ZIP `source_code_hash`, filename, and absence of layers/VPC/filesystems/DLQ.

Immediately before an authorized apply, repeat the clean-SHA checks and verify
the plan, show JSON, ZIP, and receipt hashes. Re-render and re-assert that same
saved plan, then require the new show JSON to be byte-identical. Apply only the
exact saved plan; do not re-plan or regenerate the ZIP:

```bash
test "$(git rev-parse HEAD)" = "$MERGED_SHA"
git diff --exit-code
git diff --cached --exit-code
test -z "$(git status --porcelain)"
sha256sum --check "$EVIDENCE_DIR/reviewed-artifacts.sha256"
python scripts/candidate-preflight-plan-receipt.py verify \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive stacks/aws-candidate-preflight/candidate-preflight.zip \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
terraform -chdir=stacks/aws-candidate-preflight show -json "$EVIDENCE_DIR/candidate-preflight.tfplan" > "$EVIDENCE_DIR/preapply.show.json"
python scripts/assert-candidate-preflight-plan.py "$EVIDENCE_DIR/preapply.show.json"
cmp "$EVIDENCE_DIR/candidate-preflight.show.json" "$EVIDENCE_DIR/preapply.show.json"
terraform -chdir=stacks/aws-candidate-preflight apply "$EVIDENCE_DIR/candidate-preflight.tfplan"
```

Any mismatch is a hard stop. Do not use `terraform state` editing, `-target`,
`ignore_changes`, a copied secret ARN, an unsaved plan, or a dirty/different
checkout as a shortcut.

## Required re-audit

Before an authorized apply or invocation, repeat read-only `get-function`,
`get-function-configuration`, `get-alias`, and ECR image-config inspection.
Stop if any candidate version, revision, package, architecture, skip-migration
mode, ArtifactReference, image digest, source label, alias version, alias
revision, or alias routing value differs from the pins above.

Do not invoke this wrapper until the infrastructure PR has independent review
and the release owner explicitly authorizes the exact candidate check. Do not
use the wrapper after any pin changes; update and re-review the manifest and
code instead.

## Published-helper receipt and authorized invocation

The apply publishes an immutable helper version. There is deliberately no
unqualified helper-name output. Capture the qualified ARN and numeric version,
then record a deployment receipt after auditing the exact function, concurrency,
role, inline policy, and absence of managed-policy attachments:

```bash
QUALIFIED_ARN="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_qualified_arn)"
HELPER_VERSION="$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_version)"
test "$QUALIFIED_ARN" = "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-candidate-preflight:$HELPER_VERSION"

capture_helper_audit() (
  set -e
  prefix="$1"
  aws lambda get-function --function-name "$QUALIFIED_ARN" > "$EVIDENCE_DIR/$prefix-function.json"
  aws lambda get-function-concurrency --function-name honua-demo-demo-candidate-preflight > "$EVIDENCE_DIR/$prefix-concurrency.json"
  aws iam get-role --role-name honua-demo-demo-candidate-preflight-role > "$EVIDENCE_DIR/$prefix-role.json"
  aws iam get-role-policy --role-name honua-demo-demo-candidate-preflight-role --policy-name credential-safe-candidate-preflight-v1 > "$EVIDENCE_DIR/$prefix-role-policy.json"
  aws iam list-attached-role-policies --role-name honua-demo-demo-candidate-preflight-role > "$EVIDENCE_DIR/$prefix-attached-policies.json"
  aws iam list-role-policies --role-name honua-demo-demo-candidate-preflight-role > "$EVIDENCE_DIR/$prefix-inline-policies.json"
)

python scripts/candidate-preflight-plan-receipt.py verify \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive stacks/aws-candidate-preflight/candidate-preflight.zip \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
capture_helper_audit postapply
python scripts/assert-candidate-preflight-runtime.py create \
  --function "$EVIDENCE_DIR/postapply-function.json" \
  --concurrency "$EVIDENCE_DIR/postapply-concurrency.json" \
  --role "$EVIDENCE_DIR/postapply-role.json" \
  --role-policy "$EVIDENCE_DIR/postapply-role-policy.json" \
  --attached-policies "$EVIDENCE_DIR/postapply-attached-policies.json" \
  --inline-policies "$EVIDENCE_DIR/postapply-inline-policies.json" \
  --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
  --merged-sha "$MERGED_SHA" \
  --receipt "$EVIDENCE_DIR/deployment-receipt.json"
sha256sum "$EVIDENCE_DIR/deployment-receipt.json" > "$EVIDENCE_DIR/deployment-receipt.sha256"
```

The runtime audit requires the exact qualified `FunctionArn`, numeric `Version`,
ZIP `CodeSha256`, `RevisionId`, runtime, architecture, handler, role, environment,
layers, VPC, filesystem, dead-letter configuration, reserved concurrency, role
trust, inline policy, and attachment sets. Before invocation, re-read everything
and require exact agreement with the deployment receipt. Invoke only the
qualified ARN with tail logging disabled:

```bash
sha256sum --check "$EVIDENCE_DIR/deployment-receipt.sha256"
python scripts/candidate-preflight-plan-receipt.py verify \
  --merged-sha "$MERGED_SHA" \
  --plan "$EVIDENCE_DIR/candidate-preflight.tfplan" \
  --show "$EVIDENCE_DIR/candidate-preflight.show.json" \
  --archive stacks/aws-candidate-preflight/candidate-preflight.zip \
  --receipt "$EVIDENCE_DIR/plan-receipt.json"
capture_helper_audit preinvoke
python scripts/assert-candidate-preflight-runtime.py verify \
  --function "$EVIDENCE_DIR/preinvoke-function.json" \
  --concurrency "$EVIDENCE_DIR/preinvoke-concurrency.json" \
  --role "$EVIDENCE_DIR/preinvoke-role.json" \
  --role-policy "$EVIDENCE_DIR/preinvoke-role-policy.json" \
  --attached-policies "$EVIDENCE_DIR/preinvoke-attached-policies.json" \
  --inline-policies "$EVIDENCE_DIR/preinvoke-inline-policies.json" \
  --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
  --merged-sha "$MERGED_SHA" \
  --receipt "$EVIDENCE_DIR/deployment-receipt.json"

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

# Always attempt the post-invocation audit, including after invoke/assert failure.
if ! capture_helper_audit postinvoke; then
  invocation_status=1
fi
if ! python scripts/assert-candidate-preflight-runtime.py verify \
  --function "$EVIDENCE_DIR/postinvoke-function.json" \
  --concurrency "$EVIDENCE_DIR/postinvoke-concurrency.json" \
  --role "$EVIDENCE_DIR/postinvoke-role.json" \
  --role-policy "$EVIDENCE_DIR/postinvoke-role-policy.json" \
  --attached-policies "$EVIDENCE_DIR/postinvoke-attached-policies.json" \
  --inline-policies "$EVIDENCE_DIR/postinvoke-inline-policies.json" \
  --plan-receipt "$EVIDENCE_DIR/plan-receipt.json" \
  --merged-sha "$MERGED_SHA" \
  --receipt "$EVIDENCE_DIR/deployment-receipt.json"; then
  invocation_status=1
fi
if ! sha256sum \
  "$EVIDENCE_DIR/invocation-metadata.json" \
  "$EVIDENCE_DIR/invocation-payload.json" \
  > "$EVIDENCE_DIR/invocation-artifacts.sha256"; then
  invocation_status=1
fi
test "$invocation_status" -eq 0
```

The invocation assertion requires transport `StatusCode=200`, no
`FunctionError`, `ExecutedVersion` equal to the exact published helper version,
the exact result schema and operation, `status=passed`, exact candidate and
alias pins, `migration.phase=Expand`, `pendingScriptCount=14`, the exact pending
script digest, and the exact ordered check set. Any mismatch is a hard stop.
Never continue to migration, seed, alias movement, or promotion from this
wrapper alone.
