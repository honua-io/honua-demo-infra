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
`MERGED_SHA` to that immutable commit. The executable operator procedure uses
`set -euo pipefail` across plan creation, validation, receipt binding, every
pre-apply recheck, and the exact saved-plan apply. Any failed command exits
before `terraform apply` is reachable:

```bash
EVIDENCE_DIR="$HOME/.honua-runtime-proof/candidate-preflight-$MERGED_SHA"
scripts/candidate-preflight-plan-apply.sh "$MERGED_SHA" "$EVIDENCE_DIR"
```

Keep the saved plan, show JSON, generated ZIP, ZIP checksum, `MERGED_SHA`, and
plan-checker result together as one local review bundle. Plan JSON can contain
state values, so never upload it. The assertion requires an applyable supported
plan with no drift, deferral, Terraform actions/triggers/invocations, extra
outputs, child modules, or resources beyond the exact helper graph. It verifies
the exact role ARN, least-privilege IAM, environment, source member hashes,
ZIP `source_code_hash`, filename, and absence of layers/VPC/filesystems/DLQ.

The procedure hashes the saved plan and show JSON immediately after assertion,
then repeats the clean-SHA check, receipt/hash verification, exact saved-plan
show, plan assertion, and byte comparison immediately before applying that same
file. It never replans or regenerates the ZIP between review and apply.

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
unqualified helper-name output. After explicit release-owner authorization,
run the second executable procedure:

```bash
scripts/candidate-preflight-invoke.sh "$MERGED_SHA" "$EVIDENCE_DIR"
```

The procedure uses `set -euo pipefail` from post-apply receipt verification
through all pre-invocation gates. The runtime audit requires the exact qualified `FunctionArn`, numeric `Version`,
ZIP `CodeSha256`, `RevisionId`, runtime, architecture, handler, role, environment,
layers, VPC, filesystem, dead-letter configuration, reserved concurrency, role
trust, inline policy, and attachment sets. Before invocation, re-read everything
and require exact agreement with the deployment receipt. Invoke only the
qualified ARN with tail logging disabled (`--log-type None`). Any post-apply or pre-invocation gate
failure exits before invoke. Controlled failure aggregation begins only at the
invoke: invoke or semantic assertion failure still attempts the complete
post-invocation runtime/IAM audit, and the procedure then exits nonzero.

The invocation assertion requires transport `StatusCode=200`, no
`FunctionError`, `ExecutedVersion` equal to the exact published helper version,
the exact result schema and operation, `status=passed`, exact candidate and
alias pins, `migration.phase=Expand`, `pendingScriptCount=14`, the exact pending
script digest, and the exact ordered check set. Any mismatch is a hard stop.
Never continue to migration, seed, alias movement, or promotion from this
wrapper alone.
