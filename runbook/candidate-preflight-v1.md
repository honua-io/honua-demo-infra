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
terraform -chdir=stacks/aws-candidate-preflight init -input=false
terraform -chdir=stacks/aws-candidate-preflight plan -refresh=false -input=false -out="$EVIDENCE_DIR/candidate-preflight.tfplan"
terraform -chdir=stacks/aws-candidate-preflight show -json "$EVIDENCE_DIR/candidate-preflight.tfplan" > "$EVIDENCE_DIR/candidate-preflight.show.json"
python scripts/assert-candidate-preflight-plan.py "$EVIDENCE_DIR/candidate-preflight.show.json"
test "$(sha256sum stacks/aws-candidate-preflight/candidate-preflight.zip | cut -d' ' -f1)" = "d9e47adc4d37dc6cdb2a05fdf9f14303dbe32d1cca24813b594ce22e88811874"
sha256sum stacks/aws-candidate-preflight/candidate-preflight.zip > "$EVIDENCE_DIR/candidate-preflight.zip.sha256"
```

Keep the saved plan, show JSON, generated ZIP, ZIP checksum, `MERGED_SHA`, and
plan-checker result together as one local review bundle. Plan JSON can contain
state values, so never upload it. The assertion requires an applyable supported
plan with no drift, deferral, Terraform actions/triggers/invocations, extra
outputs, child modules, or resources beyond the exact helper graph. It verifies
the exact role ARN, least-privilege IAM, environment, source member hashes,
ZIP `source_code_hash`, filename, and absence of layers/VPC/filesystems/DLQ.

Immediately before an authorized apply, repeat the clean-SHA checks and verify
the ZIP checksum against the recorded file. Apply only the exact saved plan;
do not re-plan or regenerate the ZIP:

```bash
test "$(git rev-parse HEAD)" = "$MERGED_SHA"
git diff --exit-code
git diff --cached --exit-code
test -z "$(git status --porcelain)"
test "$(sha256sum stacks/aws-candidate-preflight/candidate-preflight.zip | cut -d' ' -f1)" = "d9e47adc4d37dc6cdb2a05fdf9f14303dbe32d1cca24813b594ce22e88811874"
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

## Authorized invocation

Only after that authorization, invoke synchronously with tail logging disabled:

```bash
aws lambda invoke \
  --function-name "$(terraform -chdir=stacks/aws-candidate-preflight output -raw candidate_preflight_function_name)" \
  --cli-binary-format raw-in-base64-out \
  --invocation-type RequestResponse \
  --log-type None \
  --payload '{"operation":"candidate-preflight-v1"}' \
  candidate-preflight-result.json
```

Require `status` to equal `passed`, candidate and alias pins to match this
document, `migration.phase` to equal `Expand`, and `pendingScriptCount` to equal
`14`. Any `failed` response, Lambda `FunctionError`, timeout, or pin drift is a
hard stop. Never continue to migration, seed, alias movement, or promotion from
this wrapper alone.
