# demo.honua.io: upgrade to the 2026.1 rc server image (gate-upgrade dogfood)

Status: PHASE 1 PLAN, written 2026-10-22 from read-only evidence. Nothing in this
document has been executed. Phase 2 starts only on the coordinator's "go" with the
final server image. Refs honua-io/honua-release#376.

Purpose: upgrade demo.honua.io from the August server image to the 2026.1 rc
image using the customer-facing safe-upgrade steps (backup, unattended upgrade,
verify, rollback-ready), keep the published examples running, then use the demo
as the cloud target for the AI journey.

## 1. Current state (read-only evidence, 2026-10-22 08:10-08:30 UTC)

| Item | Value | Source |
|---|---|---|
| Serving function | `honua-demo-demo-honua`, arm64, 2048 MiB, 60 s timeout, image package | `aws lambda get-function` |
| `live` alias | `-> :42`, no weighted routing, description "rc.2 f897700" | `aws lambda list-aliases` |
| Other aliases | `dev -> :18`, `staging -> :22` (staging is the fault-injection target, never customer-facing) | same |
| Running image | `honua-server@sha256:d97baf44b17ba5b9537320281f721252ed12f1fb43bee001011d720f3ae7d622` = ECR tag `rc-2026.1-rc.2-lambda-aot-f897700-arm64`, pushed 2026-08-20 | `aws ecr describe-images` |
| Server revision | `f897700159e2791c9468c6ca85bb4e2a3a8d8433` (rc.2) | `stac-live-recovery-3384.md` boundary section |
| Migration mode | `HONUA_SKIP_MIGRATIONS=true`; deploy target `RequiresOutOfBandMigrations=true`. The runtime never migrates. | live env |
| `/healthz/live`, `/healthz/ready` | 200 `Healthy`, 200 `Ready` | curl |
| `/api/v1/metrics/health` (admin key) | `status=healthy`, `migration.status=skipped`, "Migrations skipped by configuration." | curl with `honua-demo-demo/admin-password` |
| Schema journal | Last proven 2026-09-16: `public.schema_versions` = exact 104-name journal through 091, no 092-105. Current position NOT confirmed (see section 6 risk R1): no DB inbound path, no migration-runner Lambda deployed, no `pre-092-105` snapshot exists. | `db-recovery-and-migrations-092-105.md`, `aws lambda list-functions`, `aws rds describe-db-snapshots` |
| Database | `honua-demo-demo-postgres`, db.t4g.small, PostgreSQL 15.17, 20 GB, single-AZ, `available`, backup retention 3 days, latest restorable time 2026-10-22T08:10Z | `aws rds` |
| RDS recovery points | automated daily 07:25 UTC (10-18 to 10-22) and manual `honua-demo-stac-3384-features-12cf791316b8` (2026-09-16) | `aws rds describe-db-snapshots` |
| Redis | NOT deployed. `enable_redis = true` in `demo.tfvars` but the ElastiCache group, subnet group, SG and `honua-demo-demo/redis-connection` secret do not exist yet (they are in the terraform plan as creates). | terraform plan |
| STAC | `/stac/collections/90810/items` 200 and `POST /stac/search` 200 (the 3384 outage is functionally recovered) | curl |
| Terraform state | S3 `honua-tfstate-585192672263`, key `demo/aws-demo/terraform.tfstate`; module `honua-iac` pinned at `7abdb07c85c7` | `versions.tf`, `main.tf` |

### Services the manifest promises (`manifest/demo-services.v1.json` v1.3.0 in trunk; the published copy at https://demo.honua.io/demo-services.v1.json is still v1.2.0)

12 services: seven feature layers (maui-parcels 51,245; maui-zoning 3,274; maui-roads 7,071; maui-flood-hazard 2,014; maui-sea-level-rise 3; maui-place-names 2,328; maui-buildings 42,674), each on FeatureServer + OGC Features + OGC Tiles; rasters `maui-hillshade`, `maui-imagery` (ImageServer tiles); `maui-terrain` (terrain-rgb); `maui-basemap` (PMTiles); `demo-stac` (STAC). v1.3.0 additionally publishes the governed `geometry.buffer` OGC process (needs Redis, see R4) and a planned (not live) WMS release contract for maui-flood-hazard (blocked on source governance).

### Consumers of demo.honua.io (checked in /home/mike/honua-io/honua-site @06d3ab8 and honua-samples @c6010e0)

- honua-site: about 45 pages and assets read the demo directly: `demos.html`, `demo-health.html`, `demo-two-protocols.html`, `demo-esri-leaflet.html`, `demo-geoprocessing.html`, `demo-editing.html`, `demo-public-safety.html`, `demo-planning-permitting.html`, `demo-analyst-workbench.html`, `demo-imagery-terrain.html`, `demo-maui-3d.html`, `demo-sdk-controls.html`, `samples.html`, `sample-*.html` (hello-webmap, geocoding, service-explorer, raster-overlay, control-search, control-legend, expr-builder, codemod), `connect.html`, `api-reference.html`, `capabilities.html`, `claims.html`, `proof-*.html`, `docs.html`. Endpoint families used: FeatureServer, WFS, WMS/WMTS/WCS, `/ogc/{features,tiles,maps,coverages,styles,records,processes}`, `/stac`, EDR, OData, `/rest/services/Geocode/GeocodeServer` (Amazon Location), `/rest/services/Utilities/PrintingTools/GPServer`, GeometryServer, NAServer (maui-roads), ImageServer, VectorTileServer, `/elevation/maui-terrain/value`, `/api/v1/streaming/features`, `/api/v1/capabilities/manifest`, `/openapi.json` family, and `/demo-services.v1.json`. Fixtures `assets/demos/two-protocols/fixtures/*.json` are static copies and do not break.
- honua-samples: nightly `run-samples.yml` (cron 17 5 * * *) with `HONUA_PUBLIC_BASE_URL=https://demo.honua.io` (REST/OGC/STAC/OData/geocode samples), and `pages.yml` bakes `https://demo.honua.io/rest/services/maui-buildings/FeatureServer/13` into the published maplibre quickstart at samples.honua.io.
- honua-demo-infra: `live-demo-canary` (schedule `17 */6 * * *`, section 7).

## 2. Constraints that shape the plan

1. There is no inbound path to RDS (private subnets, fck-nat is egress only). `pg_dump` cannot run from a workstation. The primary backup is therefore an RDS manual snapshot (what the governed runner uses). A logical `pg_dump` is an optional second copy and needs an in-VPC runner (decision D2).
2. The serving Lambda must stay `HONUA_SKIP_MIGRATIONS=true`. Concurrent cold starts that each migrate crash-looped the demo on 2026-06-12. Migrations run once, out of band (decision D1).
3. A `live` alias repoint is instant and atomic, and `:42` stays published, so image rollback is one command. Database rollback is not instant (section 5).
4. `stacks/aws` apply is not an image-only change today. The baseline plan on the CURRENT image already shows 7 add / 9 change / 1 replace (section 4). An apply deploys that backlog together with the image.
5. The governed migration runner (`stacks/aws-db-migration-runner`) is source-pinned to migrations 092-105 and requires Terraform 1.15.8. It cannot apply the rc migration set without a new reviewed runner build (decision D1).

## 3. Decisions the operator must make before Phase 2

- D1 Migration path. Option A (governed): new runner build for the rc source, PR + review + plan/apply + single invocation, as in `db-recovery-and-migrations-092-105.md`. Slow, strongest evidence. Option B (recommended for a demo-only DB, needs explicit OK): a one-off migration version of the serving function (same rc image, `HONUA_SKIP_MIGRATIONS=false`, timeout 900 s, published as a version, pointed at the `staging` alias only, invoked once directly with `aws lambda invoke`, then discarded). The repo's own comment in `main.tf` calls this pattern "run migrations as a one-off job".
- D2 Logical backup. Snapshot only (fast, restores the whole instance) or snapshot plus an ephemeral in-VPC `pg_dump` host (SSM-managed t4g.micro in a private subnet, dump to the demo data bucket under a `backups/` prefix, terminate after). The second option adds about 20-30 min and an instance to create and destroy.
- D3 Terraform scope. Full apply (accepts the Redis/streaming/secret-allowlist backlog in the same release, recommended, because the rc image and the v1.3.0 manifest expect Redis) versus targeted apply of the Lambda only.
- D4 Window. Demo traffic is low and CloudFront caches tiles for 24 h. A 60-minute window outside US-Pacific business hours is proposed.

## 4. Terraform plan evidence (placeholder image, NOT the rc)

`terraform 1.13.5` initialises the root (backend + `honua-iac` module fetch both work from this host); required range is `>= 1.10, < 2.0`. CI pins 1.15.8 and the migration-runner stack needs 1.15.8, so Phase 2 uses 1.15.8 if it can be installed (D3 does not need it, the migration runner does).

Commands run (read-only, `-lock=false`, plan files kept out of the repo):

    export TF_VAR_honua_admin_password="$(aws secretsmanager get-secret-value --region us-west-2 \
      --secret-id honua-demo-demo/admin-password --query SecretString --output text)"
    ECR=585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server
    terraform init -input=false
    terraform plan -input=false -lock=false -var-file=demo.tfvars -var "honua_image=$ECR@sha256:d97baf44...b7522" # baseline, current image
    terraform plan -input=false -lock=false -var-file=demo.tfvars -var "honua_image=$ECR:nightly-lambda-aot-7a29ce0-arm64"  # placeholder

Both plans: **7 to add, 9 to change, 1 to replace (destroy)**, identical resource set. The only image-attributable difference is `aws_lambda_function.this.image_uri` and the new `version` the `live` alias follows. Placeholder note: the newest nightly in this account's ECR is `nightly-lambda-aot-7a29ce0-arm64` (2026-08-12), which is OLDER than the running rc.2 f897700 image, so it is a stand-in only. The newest image of any kind is the running one. The rc image must be mirrored to this ECR (arm64, `*-lambda-aot-*`) before Phase 2.

Create (7): ElastiCache replication group + subnet group + security group for Redis, `random_password.redis_auth`, secret `honua-demo-demo/redis-connection` and its version.
Change (9): `aws_lambda_function.this` (image + environment), `aws_lambda_alias.live` (new version, description reset), `aws_iam_policy.lambda_secrets`, `aws_security_group.lambda` (Redis egress), RDS parameter group (`rds.force_ssl` apply_method pending-reboot to immediate, no value change), `aws_lambda_function` postgis_bootstrap / stac_seed_manager / stac_seed_receipt (code hash), `aws_s3_object.demo_services_manifest` (publishes v1.3.0).
Replace (1): `terraform_data.postgis_bootstrap_build`.

Phase 2 re-runs the plan with the final digest-pinned image. Always pass the digest (`@sha256:`), never a mutable tag.

## 5. Procedure (Phase 2)

All commands from `stacks/aws` unless noted. `export AWS_PAGER=""; R=us-west-2; F=honua-demo-demo-honua; B=https://demo.honua.io`. Evidence goes under `$HOME/.honua-runtime-proof/rc1-upgrade-20261022/` (create it first, outside the repo).

### 5.0 Preconditions (stop if any fails)

1. Coordinator "go" with the final image reference. Mirror it to ECR if needed (`docker buildx imagetools create` or `aws ecr batch-get-image`/`put-image` from the source repo) and record the digest: `IMG=$ECR@sha256:<digest>`. Verify `aws ecr describe-images --repository-name honua-server --image-ids imageDigest=<digest>` and that the manifest is `linux/arm64`.
2. `git -C . pull --ff-only` at trunk, clean tree, `terraform version` 1.13.5+ (1.15.8 for the runner option).
3. Baseline capture (before):

       curl -s $B/healthz/live; curl -s $B/healthz/ready
       curl -s -H "X-API-Key: $K" $B/api/v1/metrics/health          # K from honua-demo-demo/admin-password, never printed
       curl -s $B/demo-services.v1.json | sha256sum
       ./runbook/scripts/demo-b-probes.sh                           # expect ALL PROBES OK
       node scripts/live-demo-canary.mjs   (needs the vars in section 7; otherwise run the per-family curls in section 5.6)
       aws lambda get-alias --function-name $F --name live --region $R   # record revision f1cbb8ce-...
       aws rds describe-db-instances --db-instance-identifier honua-demo-demo-postgres --region $R

   Save the 12 service `featureCount` values and `FeatureServer/<id>/query?returnCountOnly=true` for the seven feature layers; they are the data-intact check.
4. Confirm that no other lane is applying (S3 state lockfile `demo/aws-demo/terraform.tfstate.tflock` absent) and that the 06:17/12:17/18:17 UTC canary windows are understood (section 7).

### 5.1 Backup

    SNAP=honua-demo-pre-rc1-$(date -u +%Y%m%dT%H%MZ)
    aws rds create-db-snapshot --region $R --db-instance-identifier honua-demo-demo-postgres --db-snapshot-identifier $SNAP
    aws rds wait db-snapshot-available --region $R --db-snapshot-identifier $SNAP
    aws rds describe-db-snapshots --region $R --db-snapshot-identifier $SNAP --query 'DBSnapshots[0].[Status,Encrypted,Engine,EngineVersion,AllocatedStorage]'

Expected 5-15 min for 20 GB. Do not continue until `available`. Tag the snapshot `Purpose=rc1-upgrade-rollback`. If D2 selects `pg_dump`: launch the ephemeral SSM host, `pg_dump -Fc` the `honua` DB using the secret `honua-demo-demo/connection-string`, upload to `s3://honua-demo-data-585192672263/backups/`, record the sha256, terminate the host.

### 5.2 Read the schema journal and classify the pending set

The journal position is the biggest unknown. Read `public.schema_versions` through the chosen migration path's own preflight (option A runner preflight, or option B first invocation's `/api/v1/metrics/health`). Record the ordered list of pending migrations and whether any are `Contract` (not backward-compatible with image `:42`). Expect about 144 total migrations at the current nightly versus 104 (through 091) last proven; so roughly 40 pending if 092-105 were never applied, fewer if they were. If any pending migration is a Contract migration, image-only rollback to `:42` is NOT safe after it runs and section 5.7 (restore) becomes the only rollback; stop and escalate before running it.

### 5.3 Provision Redis first (no serving impact)

    terraform plan -var-file=demo.tfvars -var "honua_image=$ECR@sha256:d97baf44...b7522" \
      -target=module.honua.aws_elasticache_replication_group.redis -target=module.honua.aws_secretsmanager_secret_version.redis_connection \
      -target=module.honua.aws_security_group.redis -out=redis.tfplan
    # review: creates only
    terraform apply redis.tfplan

Expected 10-20 min. This does not touch the Lambda. Verify the replication group is `available` and the secret exists.

### 5.4 Migrate (option B shown; option A replaces this with the governed runner)

1. Publish a migration version from the rc image: `aws lambda update-function-configuration` is NOT used on the serving function. Instead create the version via terraform apply of the one-off variant, or (recommended) run `terraform apply` in 5.5 with a temporary `-var skip_migrations_override=false`-style change on a branch, publish, repoint only `staging` to it, then restore the setting before the live apply. The exact mechanism must be reviewed in the Phase 2 PR; do not improvise on `$LATEST` or `live`.
2. Set function reserved concurrency to 1 for the duration (`aws lambda put-function-concurrency --function-name $F --reserved-concurrent-executions 1`), invoke the staging version once, directly, with a synthetic HTTP event for `/healthz/ready` and timeout 900 s. Restore concurrency to 25 afterwards.
3. Verify: invoke the same version with `GET /api/v1/metrics/health` and the admin key, expect `migration.status` succeeded and no pending scripts; confirm the journal now ends at the rc head. Count of applied migrations goes into the evidence file.
4. `live` is still `:42`. Immediately run the section 5.6 smoke on the OLD image against the migrated schema (expand-only claim check). Any failure here triggers 5.7 row "Schema check failed".

Expected runtime: unknown; budget 5-20 min on db.t4g.small (110k features rows, 222k change-journal rows, 39 or so migrations; a backfill or index build on `honua.features` is the long pole).

### 5.5 Deploy the rc image

    terraform plan -input=false -var-file=demo.tfvars -var "honua_image=$IMG" -out=rc1.tfplan
    terraform show -no-color rc1.tfplan | grep -E '^Plan:|will be|must be'     # expect only the section 4 set, image = digest
    terraform apply rc1.tfplan
    aws lambda get-alias --function-name $F --name live --region $R --query FunctionVersion      # new version N, was 42

Terraform publishes version N and moves `live` to it. Cold start of N: 2-4 s on first request. The `live` alias description is reset by terraform; set it back afterwards for the record: `aws lambda update-alias --function-name $F --name live --description "rc1 dogfood <short-sha>"` (note: this creates drift against the plan; fold it into `main.tf` in a follow-up).

### 5.6 Verify (after)

    curl -s -o /dev/null -w '%{http_code}\n' $B/healthz/live $B/healthz/ready       # 200 / 200
    curl -s -H "X-API-Key: $K" $B/api/v1/metrics/health | jq .status,.migration     # healthy, no pending
    ./runbook/scripts/demo-b-probes.sh                                                # ALL PROBES OK
    # data intact: the seven returnCountOnly values equal the section 5.0 baseline
    # per-family: FeatureServer query, /ogc/features items, /ogc/tiles tile fetch (z/y/x inside Maui), /stac/collections/90810/items, POST /stac/search,
    #             /rest/services/maui-hillshade/ImageServer, /rest/services/Geocode/GeocodeServer findAddressCandidates, /ogc/processes (geometry.buffer present), /api/v1/capabilities/manifest
    curl -s $B/demo-services.v1.json | jq '.schemaVersion,(.services|length)'      # 1.3.0, 12
    aws logs filter-log-events --region $R --log-group-name /aws/lambda/$F --start-time $(( ($(date +%s)-900)*1000 )) --filter-pattern '"License"'   # EventId 10014 = Pro loaded
    node scripts/live-demo-canary.mjs   # after the section 7 variable fixes; dispatch live-canary.yml with deployment_revision=<40-char rc server commit>
    Dispatch honua-samples run-samples.yml (workflow_dispatch) and run honua-site `scripts/site-demo-smoke.mjs` plus load demos.html, demo-health.html, demo-two-protocols.html, demo-geoprocessing.html, samples.html in a browser against the live site.

Success criteria: health 200/200, no pending migrations, all 12 services answer, counts match baseline, license 10014, canary green on dispatch, samples nightly green, story pages render data. Record outputs; report only what was observed.

### 5.7 Rollback trigger and command

Trigger (any one, within 30 min of the repoint): `/healthz/ready` not 200 for 3 consecutive probes 30 s apart; any of the seven feature layers returning 5xx or a count differing from baseline; 5xx rate above 2% on the CloudFront origin for 5 min; a Lambda init error or crash loop in the log group; the schema check against `:42` fails (5.4 step 4).

Image rollback (seconds, DB untouched, valid only while every applied migration is Expand):

    aws lambda update-alias --function-name honua-demo-demo-honua --name live --function-version 42 --region us-west-2
    aws lambda get-alias --function-name honua-demo-demo-honua --name live --region us-west-2   # confirm 42
    curl -s $B/healthz/ready

Then `terraform` state is ahead of reality: do not apply until `honua_image` is reset to the `d97baf44...` digest in a reviewed change. The Redis resources stay (harmless).

Database rollback (only if a Contract migration ran or data is corrupted; 30-60 min, real outage): restore `$SNAP` to a NEW instance (`aws rds restore-db-instance-from-db-snapshot`), switch the `honua-demo-demo/connection-string` secret and the Lambda environment to the restored endpoint through a reviewed terraform change (or a targeted `terraform import` + apply), keep the old instance until verified, then repoint `live` to `:42`. Point-in-time restore to just before 5.4 is the alternative (latest restorable time is within about 5 min of now).

## 6. Known risks

- R1 Journal position unknown. The last proof (2026-09-16) was 104 names through 091. The 092-105 governed runner was never deployed (no runner Lambda, no `honua-demo-pre-092-105-*` snapshot), yet STAC items now serve, which means the features-table recovery happened by some route not recorded here. Confirm the real journal in 5.2 before promising downtime.
- R2 Migration volume. About 144 migrations at the current nightly. Schema changes that rewrite `honua.features` or its indexes lock the table. Mitigation: snapshot first, run outside the window peak, CloudFront serves cached tiles meanwhile.
- R3 Contract migrations break image rollback to `:42` (older reader against newer schema). The kind-upgrade smoke in gate-upgrade has a `migration-boundary` scenario for exactly this; its result should be read before Phase 2. Classify in 5.2.
- R4 Redis. The rc image and the v1.3.0 manifest expect Redis (readiness durable store, GP job queue, rate limits). Provisioning is new infrastructure (about 12 USD/month, cache.t3.micro default). If the Lambda boots before Redis is reachable (security group egress rule is part of the same apply), `/healthz/ready` may return 503. Order in 5.3 avoids this.
- R5 Terraform backlog. Apply also rolls the unapplied trunk changes in (secret reference allowlist, streaming snapshot budget `FeatureStreaming__MaxSnapshotBytes`, Redis env, manifest v1.3.0 publish). Any of them could change behaviour the story pages rely on. They are reviewed in the plan; the stack was last applied before commits 87a6b33, a168cd0, 15282e9.
- R6 Terraform resets the `live` alias description; the control plane normally manages it. Cosmetic.
- R7 Hosted-state drift: the plan shows `rds.force_ssl` parameter apply_method flipping to immediate. Value unchanged, but it is an RDS parameter group modification; confirm no reboot is scheduled (`aws rds describe-db-instances ... PendingModifiedValues`).
- R8 Mutable image tag in the placeholder plan; Phase 2 uses the digest only.
- R9 The postgis bootstrap Lambda is replaced (terraform_data build) and re-run; it is idempotent (`CREATE EXTENSION IF NOT EXISTS`), but it needs python3 and pip on the apply host to vendor `pg8000`.
- R10 `gh-tf-test` is `AdministratorAccess`. That is more than the plan needs (apply scope is Lambda, ElastiCache, EC2 SG, IAM policy, Secrets Manager, S3, RDS parameter group). Use it for Phase 2 but flag the over-grant to the operator.
- R11 Lambda `$LATEST` shows state `Inactive` (idle function, only the published version serves). A direct invoke of another function (`stac-seed-receipt`) during Phase 1 triggered a restore; expected and harmless.

## 7. What alerts during the upgrade

`live-canary.yml` (honua-demo-infra): cron `17 */6 * * *` (00:17, 06:17, 12:17, 18:17 UTC; runs lag 5-30 min in practice), trunk-push on manifest, canary scripts, `demo.tfvars`, STAC seed files, and `workflow_dispatch` (requires a 40-char `deployment_revision`). It reads a trusted in-VPC seed receipt via `HONUA_DEMO_STAC_RECEIPT_ROLE_ARN` / `HONUA_DEMO_STAC_RECEIPT_FUNCTION_NAME`, binds the receipt to the manifest-pinned STAC seed and to the ACTIVE metadata revision, probes every family in the manifest, then `client-compat-canary.mjs`.

- **It is already red.** All of the 12 most recent scheduled runs (2026-10-19 to 2026-10-22, trunk 15282e9) fail at step "Require managed seed receipt binding" because the repo variables `HONUA_DEMO_STAC_RECEIPT_ROLE_ARN` and `HONUA_DEMO_STAC_RECEIPT_FUNCTION_NAME` are unset (only the WMS and client-compat variables exist). So no canary alert is a signal of the upgrade until those two variables are set (values are terraform outputs of `stac-seed-gate.tf`, which requires the stack to be applied) and `HONUA_DEMO_GP_API_KEY` exists. Part of Phase 2 is to set them and get one green dispatched run BEFORE the image change, so the after-run means something.
- It will go red after a correct upgrade unless these are updated: `HONUA_CLIENT_COMPAT_SERVER_COMMIT` (now `e585321...`, rc.1) and `HONUA_CLIENT_COMPAT_SERVER_IMAGE` (now the rc.1 digest `4fb503be...`) must be set to the rc image; `HONUA_DEMO_WMS_SERVER_*` pin the `6ad71ac` digest and only matter if `wms_admission=planned`. The metadata revision binding fails if the rc boot bumps the active Metadata v2 revision (re-run the managed STAC seed, `stac-live-recovery-3384.md` step 10, to re-bind the receipt).
- `manifest-drift.yml` runs on PRs and trunk pushes only, needs no AWS and does not watch the live demo, so it will not alert on the upgrade. It will fail any PR that changes seeds without regenerating `manifest/demo-services.v1.json` (`python3 manifest/generate-demo-services.py --check`).
- `drift-plan.yml` is dispatch-only and disabled (no role ARN), so no drift alert exists either.
- Outside this repo: honua-samples nightly `run-samples.yml` at 05:17 UTC will report any broken sample if the window overlaps it; samples.honua.io pages rebuild only on honua-samples pushes.

## 8. Downtime estimate

- Redis create (5.3), snapshot (5.1), terraform plan/review: no serving impact.
- Migration (5.4): the old image keeps serving during an expand-only run; table locks from DDL could cause a few minutes of slow or failing writes and queries. Estimate 0-5 min of degraded service, up to about 10 min worst case if a lock-heavy rewrite of `honua.features` is pending. Unknown until 5.2 lists the pending set.
- Cutover (5.5): alias repoint is atomic; the first request after it pays a 2-4 s cold start per execution environment (reserved concurrency 25). Estimate under 1 min of elevated latency, no hard downtime expected if `/healthz/ready` is 200 on the new version beforehand (smoke it through the `staging` alias first).
- Image rollback: under 1 min. DB restore rollback: 30-60 min outage.
- Whole window: about 60-90 min wall clock, 0-10 min user-visible impact for the expand-only case.
