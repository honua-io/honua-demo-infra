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
payload reporting a contract branch fails closed.

This probe does not move `live`, does not run migrations, does not seed data,
and does not produce a promotion receipt. A passing response is preflight
evidence only.

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
  --function-name "$(terraform output -raw candidate_preflight_function_name)" \
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
