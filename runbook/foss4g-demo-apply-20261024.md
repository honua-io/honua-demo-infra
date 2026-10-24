# demo.honua.io FOSS4G apply record (2026-10-24)

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
