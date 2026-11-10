# demo.honua.io FOSS4G apply record (2026-10-24)

> **Historical record (superseded 2026-10-27).** Its `aws lambda update-alias` and local `terraform apply` steps are no longer allowed. Changes go through `.github/workflows/terraform.yml`, and rollback is a one-line PR (`runbook/rollback-by-pr.md`).

The operator authorised the apply and the coordinator ran it. It applies honua-demo-infra
#91 (PMTiles from S3, licensing disabled, staged Redis plumbing, probe) and #92 (tile
CORS `*`, PostgreSQL pool caps). Before-state: `foss4g-demo-readiness-20261024.md`.

## Inputs

| Item | Value |
|---|---|
| Source | honua-demo-infra trunk `0196d304` (merge of #92), `stacks/aws` |
| Command | `terraform plan`/`apply` with `-var-file=demo.tfvars`; admin password from Secrets Manager (rc1 runbook §4) |
| Module | honua-iac `aws-serverless?ref=e02ed058bdb6` (licensing_mode); providers from the committed lock file (aws 6.31.0) |
| Server image | unchanged: honua-server 199bce77fe99 amd64 Lambda AOT, `sha256:24bb5279…e7d3` |
| Licensing | `enable_pro_license = false`, `licensing_mode = "Disabled"` |
| Redis | provisioned, still **unwired** (`wire_redis_connection = false`) |

## Plan and apply

Planned and applied: **`4 added, 10 changed, 2 destroyed`** (`Apply complete! Resources: 4 added, 10 changed, 2 destroyed.`).

| Action | Resource | What |
|---|---|---|
| create | `aws_cloudfront_origin_access_control.demo_data` (`E3QBFO0ZYY3FDO`) | OAC for the data bucket |
| create | `aws_cloudfront_cache_policy.pmtiles_s3` | 24 h, nothing forwarded |
| create | `aws_cloudfront_function.pmtiles_rewrite` | `/api/v1/tiles/pmtiles/<id>` → `/<id>`, `*` preflight |
| replace | `terraform_data.postgis_bootstrap_build` | standing (rebuilds the bootstrap zip every plan) |
| update | `aws_cloudfront_distribution.demo` (`E88FYGJVRJF6L`) | S3 origin + `/api/v1/tiles/pmtiles/*` behavior first |
| update | `aws_cloudfront_response_headers_policy.demo_cors` | tile origins → `*` |
| update | `aws_s3_bucket_policy.demo_data_fonts_public` | `CloudFrontReadPMTilesArchives` (8 keys, this distribution) |
| update | `module.honua.aws_lambda_function.this` | env `+Licensing__Mode=Disabled`, `+Limits__Connections__MaxConnectionPoolSize=3`, `+Limits__Connections__MaxConcurrentQueries=3`, `-Licensing__LicenseContentSecretRef`, `-Licensing__TrustedKeys__honuademo2026q2` (3814 of 4096 bytes) |
| update | `module.honua.aws_lambda_alias.live` | `live` 51 → **52** |
| update | `module.honua.aws_iam_policy.lambda_secrets` | GetSecretValue no longer on `license-pro` |
| update | `aws_lambda_function.postgis_bootstrap`, `stac_seed_manager`, `stac_seed_receipt` | standing code-hash churn |
| update | RDS parameter group | standing (`apply_method` only, no value change, no reboot) |
| destroy | `module.honua.terraform_data.pro_license_validation[0]` | no AWS object; the `license-pro` secret is untouched |

Outputs: `licensing_mode = "Disabled"`, `pro_license_enabled = false`, `cloudfront_distribution_id = "E88FYGJVRJF6L"`.
Read back after apply: `live` = version 52 with `Licensing__Mode=Disabled` and `Limits__Connections__MaxConnectionPoolSize=3`.

CloudFront invalidation **`I3L9Y7GJGAFL3L7QNBGM1UXFDX`** on `/api/v1/tiles/pmtiles/*`; distribution `Deployed`.

## Post-apply probe

`runbook/scripts/rc1-upgrade-probe.sh after-apply` at 2026-10-24T09:37:20Z. The coordinator verified the
`Range` preflight and `access-control-expose-headers` separately. The full log stays outside the repo (it
contains the admin key in request URLs); summary, admin key redacted:

```
after-apply 2026-10-24T09:37:20Z rev=199bce7 live=['52'] staging=['49']
migration: skipped | pending: 0 | first: [] | last: []
counts: {'maui-parcels': 51245, 'maui-zoning': 3274, 'maui-roads': 7071, 'maui-flood-hazard': 2014, 'maui-sea-level-rise': 1537, 'maui-place-names': 2328, 'maui-buildings': 42674}
families: {'ogc-features': '200', 'stac-items': '200', 'stac-root': '200', 'rest-services': '200', 'quickstart-layer': '200', 'geocode': '200', 'hillshade': '200', 'processes': '200', 'manifest': '200', 'stac-search': '200'}
demo pmtiles-maui-basemap: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo pmtiles-maui-buildings-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo pmtiles-maui-hillshade-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Miss from cloudfront", "acao": "*"}
demo pmtiles-maui-imagery-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Miss from cloudfront", "acao": "*"}
demo pmtiles-maui-parcels-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo pmtiles-maui-roads-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Miss from cloudfront", "acao": "*"}
demo pmtiles-maui-terrain-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo pmtiles-maui-zoning-static: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Miss from cloudfront", "acao": "*"}
demo mapserver-export: {"status": 200, "bytes": 49108, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo mapserver-export-json: {"status": 200, "bytes": 353, "type": "application/json;", "png": false, "cache": "Miss from cloudfront", "href": false, "error": 503}
demo imageserver-export: {"status": 200, "bytes": 26934, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo imageserver-export-json: {"status": 200, "bytes": 353, "type": "application/json;", "png": false, "cache": "Miss from cloudfront", "href": false, "error": 503}
demo ogc-tiles-mvt: {"status": 200, "bytes": 208236, "type": "application/vnd.mapbox-vector-tile", "png": false, "cache": "Hit from cloudfront"}
demo ogc-maps-png: {"status": 200, "bytes": 49033, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo cors-ogc-tiles: {"status": 200, "bytes": 208236, "type": "application/vnd.mapbox-vector-tile", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo cors-imageserver-tile: {"status": 200, "bytes": 23492, "type": "image/png", "png": true, "cache": "Hit from cloudfront", "acao": "*"}
demo cors-pmtiles: {"status": 206, "bytes": 16384, "type": "application/vnd.pmtiles", "png": false, "cache": "Hit from cloudfront", "acao": "*"}
demo licensing: {"currentEdition": "Enterprise", "licenseValidationState": "Disabled", "entitlements": 69, "inactive": []}
FAILED: ['mapserver-export-json', 'imageserver-export-json']
```

Green: all 8 PMTiles ranged GETs (206, `acao: *`); tile CORS `*` on OGC tiles, ImageServer tiles
and PMTiles; licensing `Enterprise` / `Disabled` with 69/69 entitlements active; MapServer and
ImageServer `f=image` PNGs; OGC Tiles MVT and OGC Maps PNG; counts and families unchanged.

Red (expected): `mapserver-export-json`, `imageserver-export-json` (503). These need Redis; see below.

`DatabaseConnections` after the apply (5-min max): 13, 43, 67, 59 between 09:25Z and 09:45Z.
Compare 175 at 07:06–07:15Z. Re-check under a gallery-deploy burst: the expected ceiling is 150,
with no `53300` in `/aws/lambda/honua-demo-demo-honua`.

## New finding: H3 returns 501

With every entitlement active, `analytics.h3` passes the licensing gate:

    GET https://demo.honua.io/ogc/features/collections/maui-buildings/h3?resolution=7
    501 {"type":"about:blank","title":"Not Implemented","status":501,
         "detail":"The configured data provider does not support H3 aggregation for this collection.",
         "correlationId":"00-6adc7db00d1537700bb124f332beed1d-035caf1b07ed0527-00"}

The RDS PostgreSQL 15.17 database has `postgis` and `postgis_raster` 3.4.3 (bootstrap output) and no
`h3` extension, so the provider reports no H3 support. The licensing acceptance item
"`…/maui-buildings/h3?resolution=7` returns 200" is therefore **not met**. This is a data-provider
gap, not a licensing gap.

Before promising H3 at the booth, someone has to decide one of:
- install the `h3`/`h3_postgis` extensions through the in-VPC bootstrap Lambda, if RDS offers them
  on this engine version (check `SELECT * FROM pg_available_extensions WHERE name LIKE 'h3%'`
  through the bootstrap Lambda; that is an operator invoke, not done here);
- if the server has a non-extension H3 path, find out why this provider does not use it (server lane);
- or keep the analytics flagship on `clusters`, `spatial-join`, `buffer-aggregate` and `density`.

## Remaining prerequisite: exports (Redis)

Unchanged from `exports-redis-prereq.md`. The operator must:
1. create `honua-demo-demo/operation-key-ring` (operation key-ring PKCS#12);
2. land the flip diff (`wire_redis_connection = true` plus the moved-block swap);
3. plan (expected Lambda env 4065 of 4096 bytes) and apply.

Until then `f=json` exports return 503. `f=image` works.

## Second apply round (2026-10-24 21:40–22:10Z): CORS headers, Redis + key ring, editing layer

Operator go: "do 1 2 and 3" (Redis apply, CORS apply, editing publish). All three done by the
coordinator from fresh worktrees on trunk; every plan was re-run against current state first.

| # | Change | Plan | Apply | Verified |
|---|---|---|---|---|
| 2 | #96 edge CORS: both `aws_cloudfront_response_headers_policy` allow `If-Match`, `Prefer`, `OData-Version`, `OData-MaxVersion` | `0 add, 3 change, 0 destroy` (two policies + the standing `rds.force_ssl` normalization) | `0 added, 3 changed, 0 destroyed` | `OPTIONS /odata/Layers(68824)/Features(1)` with `Access-Control-Request-Headers: content-type,prefer,if-match,odata-version` → 204, `access-control-allow-headers` lists all four |
| 3 | editing scratch layer (`editing-scratch-layer-anonymous-writes.md`) | n/a (admin API) | publish `201` layerId 68824 managed; policy `200` | anonymous PATCH 200 + re-read; source table untouched; reset `--apply` → `27 rows, seed state` |
| 1 | #98 Redis + operation key ring (`exports-redis-prereq.md`) | `1 add, 5 change, 0 destroy`; six moved blocks; env 4065/4096 | `1 added, 5 changed, 0 destroyed`; `live` 52 → 53 | below |

Redis apply verification (`rc1-upgrade-probe.sh after-redis`, 22:09Z): **`FAILED: none`**.
`mapserver-export-json` and `imageserver-export-json` now return `200` with an `href`;
`f=image` PNGs 49 KB / 27 KB; PMTiles, tile CORS, MVT, OGC Maps, licensing unchanged and green.
CloudWatch: zero `SharedWriteRejectedRedisRequired` events after the apply. Boot: ~45 s of
`Runtime.ExitError` cold starts (Secrets Manager 400 from `ResolveKeyRingMaterialAsync`) while
the new IAM policy propagated, then clean; also an informational
`Redis durability attestation: unverified (… ERR unknown command 'CONFIG' …)` line, because
ElastiCache does not expose `CONFIG GET`; the manifest publishes that outcome.

Capability manifest after: `jobs.runner` and `operations.proposals` report
`available:false, reasonCode: insufficient-policy` for anonymous callers (previously
`dependency-unavailable`), i.e. the dependency is satisfied.

### Findings from this round

- **H3 answers 501 for every source-mode layer, by design in rc.1.** The h3/h3_postgis
  extensions are installed (4.2.3) and the function environment was replaced, yet
  `…/maui-buildings/h3` and `…/maui-inspections/h3` still answer 501. Cause:
  `PostgresStorageMappedFeatureReader.QueryH3Async` throws
  `NotSupportedException("H3 queries are not supported for source-backed PostGIS layers yet.")`.
  All seven Maui layers and `maui-inspections` are source-mode. The managed copy published in
  step 3 answers: `GET /ogc/features/collections/maui-inspections-scratch/h3?resolution=5` →
  200 GeoJSON (cells with `cellIndex`, `count`) in 0.4 s. To show H3 over the 42k buildings, a
  managed-store copy of `maui_buildings` would have to be published (same admin call as the
  inspections copy, no anonymous writes). Not done; operator decision.
- **Managed process execution is refused on Lambda regardless of Redis** (see
  `exports-redis-prereq.md` status). `POST /ogc/processes/processes/geometry.buffer/execution`
  with the admin key, sync or async → `503 capability-unavailable`, `missingDependency:
  long-lived-host`. Needs a long-lived host or `enable_gp_batch` with a managed-profile workload.
- **Pre-existing: the `audit-chain-integrity` health check has been Unhealthy continuously since
  at least 2026-10-21 22:04Z** ("entry_hash is missing at audit_id 287: an unhashed row follows a
  hashed row"), every ~30 s in the Lambda log. `/healthz/ready` still answers 200, so it is not
  readiness-gating. Not caused by today's work (first event predates every change); worth a
  server/ops ticket before the booth, because any audit-trail story would show it.
- The honua.io pages that describe these states (endpoints.html rows `mapserver-export-json`
  and `analytics-h3`, the analytics flagship's H3 button, the geoprocessing page's "no Redis job
  store" copy) are being updated in honua-site to match (lane O).

## Rollback of the Redis flip (2026-10-24 22:34Z)

Decision by the coordinator on evidence, per `exports-redis-prereq.md` step 7:
`aws lambda update-alias --function-name honua-demo-demo-honua --name live --function-version 52`.
Version 52 is the pre-Redis environment (`HONUA_SKIP_MIGRATIONS=true`). Verified after: `/healthz/ready`
200, Lambda `Errors` 0/min from 22:35Z, no adapter panics, 24-request parallel bursts all 200,
`maui-inspections-scratch` reads/writes and its H3 still 200, exports `f=json` back to the 503 body.

Why (all from `/aws/lambda/honua-demo-demo-honua` and CloudWatch, version 53 only):

| Signal | Before the flip (08:00–21:55Z) | After (21:55–22:34Z) |
|---|---|---|
| Lambda `Errors` | 0 every hour, including two 25-concurrency bursts (238 throttles at 17:00Z) | 97 in 21:xx, 13 + 9 + 27 in 22:17–22:20, 5/5 at 22:26 and 22:27 (Googlebot, concurrency 1), 34/41 at 22:28 |
| Adapter panics `lambda_runtime::layers::panic: … tcp connect error … Connection refused` | 0 | 4, 9, 27, 2, 5, 5, 33, 16 per minute 22:17–22:31 |
| 36-request parallel burst (12 × export+OData+MVT) at 22:28Z | n/a | 33 × HTTP 500 in ~0.5 s, 2 × 200, 1 × 400 |
| `RedisConnectionException` / `RedisTimeoutException` | n/a | 86 / 10 (ConnectTimeout to the cache.t3.micro TLS endpoint during the 25-container bursts; cache CPU 2 %) |

Mechanism, from the zombie container's stream `2026/10/24/[53]c8edb36280fa465aa0812e681b01afdf`
(alive since 22:13:04Z, served requests normally until 22:20:41Z):

    22:20:40  MapServer export requested: "maui-zoning" with size 64x64
    22:20:40  MapServer export completed: "maui-zoning" rendered 189 features in 205 ms
    22:20:41  [INF] Beginning transaction / Checking whether journal table exists / Fetching list of already executed scripts   (x4, DbUp)
    22:20:41  fail: MapServer export failed: "maui-zoning": "Distributed import progress state is unavailable while attempting to set progress."
    22:20:41  HTTP "GET" "/rest/services/maui-zoning/MapServer/export" responded 200 in 499 ms
    22:20:41  REPORT … Duration: 610 ms
    22:20:41  START … ; ERROR Lambda runtime invoke … lambda_runtime::layers::panic: hyper_util … ConnectError("tcp connect error", … ConnectionRefused)
    (every later invocation of this container: the same panic in ~1.5 ms, until 22:34Z)

The server process left no managed exception, no `Application is shutting down`, no segfault
text: it was simply gone between two invocations. Lambda kept the sandbox and kept routing to it,
so one dead container answered 500 to everything it was given for up to 14 minutes. Related
warnings in the same period: `Print orphan sweep failed` and `Redis {Operation} failed for
distributed import queue` (same "Distributed import progress state is unavailable"), `Error in
migration batch advance loop` (57P05 idle-session timeout), `Leadership … error`
(ObjectDisposedException on a SafeWaitHandle). Filed against honua-server; the issue link is in
the PR that adds this section.

What stays from the round: the key-ring secret (operator-owned, never delete), the CORS headers,
the editing scratch layer. Terraform: this PR swaps the moved blocks back and sets
`wire_redis_connection = false` so the next apply builds a Redis-free version instead of
re-pointing `live` at 53. Planned (not applied): `0 to add, 5 to change, 1 to destroy`
(`aws_iam_role_policy.lambda_operation_key_ring[0]` destroyed; Lambda env 60 variables, 3814 bytes;
`live` alias drift from the manual repoint is detected and will be re-pointed to the new version).
