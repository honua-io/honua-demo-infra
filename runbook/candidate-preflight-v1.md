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

## Terraform state boundary and output handoff

The helper is owned by the dedicated `stacks/aws-candidate-preflight` root and
state key `demo/aws-demo/candidate-preflight.tfstate`. Its plan cannot include
resources from the primary demo root. It reads three persisted primary-state
outputs; `admin_password_secret_arn` is declared there as exactly
`module.honua.admin_password_secret_arn`. Never replace this handoff with a
copied ARN, a derived random-suffix ARN, or an AWS name lookup.

Before the first helper plan, require this to succeed from the primary root:

```bash
terraform -chdir=stacks/aws output -raw admin_password_secret_arn
```

If the output is absent, stop. Its declaration must be materialized by a
separately reviewed, state-only primary-root operation. Create a saved
`terraform plan -refresh-only`, inspect its JSON, and require zero non-no-op
resource actions and exactly one non-no-op output action: creation of
`admin_password_secret_arn`. Any other resource or output action is a hard
stop. Applying that exact reviewed plan writes Terraform state but does not
change AWS; it still requires explicit release-owner authorization. Do not use
`terraform state` editing, `-target`, `ignore_changes`, a copied secret ARN, or
an unsaved plan as a shortcut.

After the output exists, create the helper plan from its own root and gate its
local show JSON:

```bash
terraform -chdir=stacks/aws-candidate-preflight init -input=false
terraform -chdir=stacks/aws-candidate-preflight plan -input=false -out=candidate-preflight.tfplan
terraform -chdir=stacks/aws-candidate-preflight show -json candidate-preflight.tfplan > candidate-preflight.show.json
python scripts/assert-candidate-preflight-plan.py candidate-preflight.show.json
```

Keep the plan JSON local because it can contain state values. The assertion
requires a complete plan with exactly four helper creates and the helper output;
it rejects application Lambda/alias/environment, RDS/database, secret-version,
seed/bootstrap, CloudFront, deferred, and unrelated output actions. Apply only
the exact saved plan after independent review and explicit authorization.

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
