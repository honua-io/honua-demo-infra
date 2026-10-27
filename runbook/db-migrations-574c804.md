# Migrations for honua-server 574c804 (demo.honua.io)

## Why

The demo now serves `live` = version 54, image `trunk-574c804-lambda-aot-amd64`
(`sha256:82cf0428…`), with `HONUA_SKIP_MIGRATIONS=true`. That build needs six
migrations the demo database does not have. Since the 2026-10-27 re-apply,
`/healthz/ready` answers 503 with `x-honua-readiness-reason: schema-floor`.
The version 54 log says:

    Database schema floor check failed for migration 'Honua.Postgres.Migrations.008_AddRasterSamplingFunctions.sql' (MigrationNotApplied) ...
    The process is starting WITHOUT a usable schema; /healthz/ready reports 503 schema-floor and
    control-plane event endpoints answer 503 until the migrations are applied

The audit-chain health check is unrelated to this 503: readiness never consults it.

## The pending set

| Script | Classification | Commands |
|---|---|---|
| `Honua.Postgres.Migrations.008_AddRasterSamplingFunctions.sql` | Expand | 14 |
| `Honua.Server.Migrations.136_CreateAdminApiKeys.sql` | Expand | 3 |
| `Honua.Server.Migrations.137_CaptureFeatureChangeCommittedImage.sql` | Expand | 12 |
| `Honua.Server.Migrations.138_CreateJobArtifactImportReceipt.sql` | Expand | 2 |
| `Honua.Server.Migrations.139_AddAdminApiKeyRestrictions.sql` | Expand | 4 |
| `Honua.Server.Migrations.140_CreateAiExecutionAdmission.sql` | Expand | 7 |

- **Classification.** It comes from honua-server's own `MigrationSafetyClassifier` at 574c804. 137 mentions "contract" only in comments. Its migration is Expand, and the server computes no contract-approval nonce for this set, so **you supply no approval value**.
- **Rollback.** All six reader-compatibility declarations are `expand` with minimum reader schema 0. An image rollback of `live` to version 52 (`199bce7`) therefore still accepts the journal.

## The baseline

The manifest pins 158 scripts: honua-server's 574c804 discovery set minus these six. That set excludes `109_AdoptConfiguredGuardedSchema`, which only applies to a non-default schema.

It matches the demo journal reconstructed from evidence:
- the 104-name journal through 091 (the earlier 092-105 manifest);
- the 51 scripts that version 45 applied on 2026-10-22 between 11:18:42 and 11:18:45Z;
- the 3 scripts that version 50 applied at 18:18Z (raster 007, 134, 135).

The last two come from CloudWatch "Executing Database Server script" lines. No migration has executed since then.

If the live journal differs, the runner changes nothing, rolls back, and reports the `missingScripts` and `unexpectedScripts` names.

## How the artifacts were derived and proved

`stacks/aws-db-migration-runner/derivation/RunnerArtifactHarness.cs` runs inside `tests/dotnet/Honua.Server.Tests` of a honua-server checkout at 574c804. It is not committed there. It uses the server's own code:
- `PostgresDatabaseMigrationRunner.BuildUpgrader`, with raster migrations included and the default `honua` schema;
- DbUp's configured preprocessor and PostgreSQL splitter;
- `MigrationReaderCompatibility.Declare`;
- `MigrationSafetyClassifier` and `ComputeApprovalNonce`, which returned `null`.

Its output is the six `runner/migrations/*.json` files.

`derivation/RunnerEquivalenceHarness.cs` and a local PostGIS 16 test gave these results:
1. **Baseline.** DB A was migrated with the server's DbUp setup minus the six scripts (158 applied).
2. **Runner.** This runner's `handler.py` was applied to A, with the connection swapped for a local one, and passed. A second invocation was rejected with `migration-replay-rejected`.
3. **Reference.** DB B was migrated by the real 574c804 `PostgresDatabaseMigrationRunner`: 164 applied, schema guard passed.
4. **Same schema.** `pg_dump --schema-only` of A and B is identical. The journals hold the same set, and the six declarations match.
5. **Server accepts A.** The real 574c804 runner against A planned **0 pending**, ran 0 scripts, and its schema guard (`PostgresCoreSchemaGuard.VerifyAsync`, the check behind `schema-floor`) passed.

## Apply

Apply from a clean checkout of the merge commit. Use the real backend credentials that `#102` used, with `AWS_REGION=us-west-2`.

```bash
git fetch origin && git switch --detach origin/trunk     # the merge commit of this PR
MERGED_SHA="$(git rev-parse HEAD)"
EV="$HOME/.honua-runtime-proof/db-migration-runner-plan-$MERGED_SHA"

# 1. Plan. This builds the ZIP twice, requires the pinned digest, and asserts a create-only scope:
#    5 to add (log group, role, role policy, security group, function), nothing else.
TERRAFORM=terraform-1.12 scripts/db-migration-runner-plan-apply.sh plan "$MERGED_SHA" "$EV"
#    Review "$EV/runner.show.json", then:
REVIEWED="$(sha256sum "$EV/reviewed-artifacts.sha256" | cut -d' ' -f1)"

# 2. Apply exactly the reviewed saved plan.
TERRAFORM=terraform-1.12 scripts/db-migration-runner-plan-apply.sh apply "$MERGED_SHA" "$EV" "$REVIEWED"

# 3. One governed invocation:
#    - audits the runner, serving :54 and live -> :54;
#    - takes manual snapshot honua-demo-pre-574c804-ed316a743591 and waits until it is available;
#    - invokes the qualified runner once with {"operation":"apply-574c804"};
#    - verifies the receipt.
scripts/db-migration-runner-invoke.sh "$MERGED_SHA" "$EV"
```

The invoke audit pins `honua-demo-demo-honua:54` (revision `8b14c978…`) and `live` (revision `e729b0ba…`). If anything publishes a new serving version or moves `live` before step 3, the audit stops before the snapshot. In that case, update the pins in a reviewed PR.

Any failure is terminal. Do not delete or reuse the snapshot, and do not invoke again. Read the sanitized receipt and fix forward in a new PR.

## Verify

```bash
curl -s -o /dev/null -D - https://demo.honua.io/healthz/ready | grep -iE '^HTTP|x-honua-readiness-reason'
# expect HTTP/2 200 and no x-honua-readiness-reason header
curl -s https://demo.honua.io/healthz/ready     # expect a ready body, not "Not Ready"
```

Warm containers that started in degraded mode re-check the schema in the background. Give them a minute, or let Lambda recycle them. In the version 54 log, the line to look for is the database-recovery success message ("database is reachable and migrations are up to date"). The `schema-floor` exceptions should stop.
