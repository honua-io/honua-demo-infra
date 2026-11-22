# Migrations for honua-server 99c4226 (demo.honua.io, Redis-free image)

## Why

The demo's Redis cache was removed (#132). The serving image pinned before
that change, `574c804176d9`, still keeps durable process jobs and package
drafts in Redis, so Studio package drafts answer 503. The replacement is the
Redis-free amd64 Lambda AOT build of honua-server trunk
`99c422646c4cd246a73e9b65efd00e9e36724587`, published by
"Build & Publish Platform Images" run 44958072387 (tag
`trunk-99c4226-lambda-aot-amd64`, digest `sha256:2ef00dc750c8222ed01694f52371bfd3e7b17cd12378ad6f2281efdde2bd2e76`,
runnable `linux/amd64` child of attested parent `sha256:13c99531...`).

The demo serves with `HONUA_SKIP_MIGRATIONS=true` and never migrates at
startup, so that image cannot go live first: it needs 69
migrations the demo database does not have, and without them
`/healthz/ready` answers 503 `schema-floor` on every route. The governed runner
applies them before the image moves.

## The pending set

The baseline is the journal after the 574c804 invocation: the 158 scripts the
574c804 manifest pinned plus the six it applied (164 names, digest
`b5bbb10c11ce58f33b6a1d92e4228544fa9728c30b75192f36770f4cf5dd8903`). Every baseline name is still discovered at 99c4226. The
pending set is 99c4226's discovery set (default `honua` schema, raster
migrations included, `109_AdoptConfiguredGuardedSchema` excluded) minus that
baseline, in the server's own order (69 `Honua.Server.Migrations` scripts,
`141` through `224`). Pending-set digest `0727b14c42422add2eaa88e45ee1f8be5e70762a1faf7405bd9fca7216197ecb`.

- **Classification.** Derived by honua-server's own `MigrationSafetyClassifier`
  at `99c4226`: 68 scripts classify `Expand` and 1 script classifies `Contract`
  (`Honua.Server.Migrations.183_RemoveUnusedFeatureIndexes.sql`, drop-index).
  The server's one-shot `contractApprovalNonce` for this exact pending set is
  `bc2202c7644b6d7f`; the operator supplies it explicitly as
  `APPROVE_CONTRACT_MIGRATIONS=bc2202c7644b6d7f` when invoking.
- **Reader compatibility.** Three scripts declare `reader-incompatible`
  (`142_CreateAiFinancialAccounting.sql`, `144_RenameGeoServicesRestSourceKind.sql`,
  `145_ProtectImportDependencies.sql`) and `183_RemoveUnusedFeatureIndexes.sql`
  declares `contract`; the remaining 65 declare `expand` (min reader schema 0).

## How the artifacts were derived and proved

`stacks/aws-db-migration-runner/derivation/RunnerArtifactHarness.cs` and
`RunnerEquivalenceHarness.cs` run inside `tests/dotnet/Honua.Server.Tests` of
a honua-server checkout at 99c4226 (not committed there). The artifact harness
uses `PostgresDatabaseMigrationRunner.BuildUpgrader`, DbUp's configured
preprocessor and PostgreSQL splitter, `MigrationReaderCompatibility.Declare`,
`MigrationSafetyClassifier` and `ComputeApprovalNonce`.
`derivation/emit_runner_artifacts.py` writes its output as
`runner/migrations/*.json` and `runner/migration-manifest.v2.json`.

Equivalence, one build-host job against a local `postgis/postgis:16-3.4`:

1. **Baseline.** DB A and DB B were each migrated with the 99c4226 DbUp setup
   restricted to the 164-script baseline (`baseline applied 164` on both).
2. **Runner.** This runner's `handler.py` was applied to A (connection swapped
   for local PostGIS, `approveContractMigrations=bc2202c7644b6d7f`) and passed
   (`appliedScriptCount: 69`). A second invocation on A was rejected with
   `migration-replay-rejected`.
3. **Reference.** DB B was migrated by the real 99c4226
   `PostgresDatabaseMigrationRunner` with approval nonce `bc2202c7644b6d7f`:
   planned 69 pending, applied 69 (`run ok=True applied=69`).
4. **Same schema, journal and declarations.** `pg_dump --schema-only` of A and
   B is identical (`schema IDENTICAL`); `public.schema_versions` is identical
   (233 rows on both, `journal IDENTICAL`);
   `public.schema_version_reader_compatibility` is identical (`reader IDENTICAL`).
5. **Server accepts A.** The real 99c4226 runner against A planned **0 pending**
   (`plan ok=True pending=0`) and `PostgresCoreSchemaGuard.VerifyAsync` passed
   (`guard ok`), matching B.

## Order (main applies; each step is a separate gate)

1. Merge this PR. It changes only the isolated runner root, its governance
   scripts and this runbook; `stacks/aws/demo.tfvars` keeps the 574c804 image.
2. Runner plan/apply from a clean checkout of the merge commit:

   ```bash
   git fetch origin && git switch --detach origin/trunk
   MERGED_SHA="$(git rev-parse HEAD)"
   EV="$HOME/.honua-runtime-proof/db-migration-runner-plan-$MERGED_SHA"
   TERRAFORM=terraform-1.12 scripts/db-migration-runner-plan-apply.sh plan "$MERGED_SHA" "$EV"
   # Review "$EV/runner.show.json": the four named runner resources are replaced (the applied
   # 574c804 runner is retired) and the security group is unchanged, nothing else.
   REVIEWED="$(sha256sum "$EV/reviewed-artifacts.sha256" | cut -d' ' -f1)"
   TERRAFORM=terraform-1.12 scripts/db-migration-runner-plan-apply.sh apply "$MERGED_SHA" "$EV" "$REVIEWED"
   ```

3. One governed invocation: `APPROVE_CONTRACT_MIGRATIONS=bc2202c7644b6d7f scripts/db-migration-runner-invoke.sh "$MERGED_SHA" "$EV"`.
   It resolves the version `live` routes to, requires that version to serve the
   pinned 574c804 image with `HONUA_SKIP_MIGRATIONS=true`, takes manual
   snapshot `honua-demo-pre-99c4226-0727b14c4242`, invokes the runner once with
   `approveContractMigrations=bc2202c7644b6d7f`, verifies the
   receipt, and re-audits that neither the runner nor `live` moved.
4. Only after a passing receipt: the follow-up demo-root PR repins
   `honua_image` in `stacks/aws/demo.tfvars` to the 99c4226 digest (and
   re-wires the preserved operator-owned operation key-ring secret independent
   of Redis) and is applied through `.github/workflows/terraform.yml` as usual.

Any failure is terminal: do not delete or reuse the snapshot, do not invoke
again; read the sanitized receipt and fix forward in a new PR.

Between steps 3 and 4 the 574c804 image reads a journal with names it does not
know. It qualifies them through the recorded reader-compatibility declarations
(`PostgresReaderCompatibilityJournal`); because scripts `142`, `144`, and `145`
declare `reader-incompatible`, the 574c804 image refuses the journal and answers
503 from step 3's invocation until step 4's repin apply completes. Rollback
after step 3 therefore means restoring the pre-invoke snapshot
`honua-demo-pre-99c4226-0727b14c4242`. Run steps 3 and 4 back to back.

## Verify

```bash
curl -s -o /dev/null -D - https://demo.honua.io/healthz/ready | grep -iE '^HTTP|x-honua-readiness-reason'
# expect HTTP/2 200 and no x-honua-readiness-reason header after step 4
```

Then run the live demo canary (`.github/workflows/live-canary.yml`) and the
Studio live demo canary (honua-studio `live-demo-canary.yml`) against the
deployed image.
