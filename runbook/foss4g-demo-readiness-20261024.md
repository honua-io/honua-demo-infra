# demo.honua.io FOSS4G NA readiness, observed state (2026-10-24)

Read-only record taken before the FOSS4G NA booth (Nov 2-4). Serving:
honua-server 199bce77fe99 (rc.1, amd64 Lambda AOT), `live` = version 51,
Redis provisioned but unwired, module pin `7abdb07` before this change. No AWS
state was changed to produce it.

## Summary

| Surface | Observed | Cause | Fix |
|---|---|---|---|
| PMTiles `/api/v1/tiles/pmtiles/<id>` (all 8 archives) | **404**, through CloudFront (`X-Cache: Error from cloudfront`). S3 objects intact (`application/vnd.pmtiles`, metadata `operation=publish`, `maui-basemap` 7,089,102 bytes). | Server range proxy regression on rc.1 (server lane). | CloudFront serves the archives from S3 via OAC (`stacks/aws/cloudfront.tf`); invalidate `/api/v1/tiles/pmtiles/*` after apply. |
| MapServer `export` / ImageServer `exportImage`, `f=json` | `200` with an error body `{"error":{"code":503,…"Temporary export storage is currently at capacity…"}}`, no `href`. | Cloud-backed temp storage needs Redis for shared quotas; Redis unwired (honua-server#6500). | `runbook/exports-redis-prereq.md` (needs the key-ring certificate secret, not present). |
| MapServer `export` / ImageServer `exportImage`, `f=image` | `200 image/png`, non-empty (49 KB / 27 KB for a 256 px Maui view) at 07:15Z. Earlier reports of 0-byte PNGs did not reproduce with these parameters; clients that use `f=json` (esri-leaflet `dynamicMapLayer` default) still get no image. | | Same as above for `f=json`. |
| Licensing | `policies.currentEdition = "Pro"`, `licenseValidationState = "Valid"`, 41 of 69 entitlements active; inactive include `analytics.h3`, `editing.branch-versioning`, `raster.multidim-coverage`, `geocoding.batch`. | `enable_pro_license = true` (signed demo envelope). Shipped 2026.1 runs `Licensing__Mode=Disabled`. | `licensing_mode = "Disabled"`, module pin `e02ed05`. |
| OGC API Tiles MVT, OGC API Maps PNG, FeatureServer counts, OGC Features, STAC, processes | All 200 / counts non-zero. | | |

## CloudWatch evidence (log group `/aws/lambda/honua-demo-demo-honua`)

PMTiles 404 inside the server (request reached the Lambda, no exception logged):

    2026-10-24T06:21:07.722Z  HTTP "GET" "/api/v1/tiles/pmtiles/maui-basemap" responded 404 in 81.4544 ms   (trace 6adc4e5314babfd914e988cd626dd04d)
    2026-10-23T08:21:45.172Z  HTTP "HEAD" "/api/v1/tiles/pmtiles/maui-basemap" responded 404 in 245.8484 ms (trace 6adb19160eecb4485f01cb66494469f3)

Export temp-storage rejection:

    2026-10-24T05:40:31.946Z  Warning EventId 48370743 SharedWriteRejectedRedisRequired
      Honua.Infrastructure.Services.CloudBackedTemporaryFileService
      "Rejecting shared temporary file write because Redis coordination is required for cloud-backed storage quotas."
      RequestPath /rest/services/maui-buildings/MapServer/export   (trace 6adc44cf687fb7bb5d42c50013fa964c)
    2026-10-24T06:30:52.742Z  same, /rest/services/maui-zoning/MapServer/export (trace 6adc509c5d83240528297fa13c8f95dd)
    2026-10-24T06:33:09.501Z  same, /rest/services/maui-zoning/MapServer/export (trace 6adc5125590022b41a407d314004458a)

Pro license active:

    2026-10-23T07:17:00.331Z  EventId 10014 LicenseLoadedFromSecret "License loaded from secret reference."
    License loaded successfully. licenseId="lic-demo-pro-001" edition=Pro keyId="[REDACTED]" expiresAt=06/15/2027 17:33:38 +00:00 activeEntitlementCount=41

Commands (read-only):

    aws logs filter-log-events --region us-west-2 --log-group-name /aws/lambda/honua-demo-demo-honua \
      --start-time $(( ($(date +%s) - 86400) * 1000 )) --filter-pattern '"SharedWriteRejectedRedisRequired"'
    # likewise '"/api/v1/tiles/pmtiles/maui-basemap" "responded"' and '"License loaded"'

## Probe

`runbook/scripts/rc1-upgrade-probe.sh` now also checks the demo surfaces (PMTiles
ranged GET for every archive, exports as `f=image` and `f=json`, an OGC Tiles MVT
tile, an OGC Maps PNG, and licensing Disabled with every entitlement active).
Run 2026-10-24 07:15Z against the live demo, exit 2 (red, expected before the
apply):

```
foss4g-before-20261024 2026-10-24T07:15:14Z rev=199bce7 live=['51'] staging=['49']
migration: skipped | pending: 0 | first: [] | last: []
counts: {'maui-parcels': 51245, 'maui-zoning': 3274, 'maui-roads': 7071, 'maui-flood-hazard': 2014, 'maui-sea-level-rise': 1537, 'maui-place-names': 2328, 'maui-buildings': 42674}
families: {'ogc-features': '200', 'stac-items': '200', 'stac-root': '200', 'rest-services': '200', 'quickstart-layer': '200', 'geocode': '200', 'hillshade': '200', 'processes': '200', 'manifest': '200', 'stac-search': '200'}
demo pmtiles-maui-basemap: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-buildings-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-hillshade-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-imagery-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-parcels-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-roads-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-terrain-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo pmtiles-maui-zoning-static: {"status": 404, "bytes": 0, "type": "", "png": false, "cache": "Error from cloudfront"}
demo mapserver-export: {"status": 200, "bytes": 49108, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo mapserver-export-json: {"status": 200, "bytes": 353, "type": "application/json;", "png": false, "cache": "Miss from cloudfront", "href": false, "error": 503}
demo imageserver-export: {"status": 200, "bytes": 26934, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo imageserver-export-json: {"status": 200, "bytes": 353, "type": "application/json;", "png": false, "cache": "Miss from cloudfront", "href": false, "error": 503}
demo ogc-tiles-mvt: {"status": 200, "bytes": 208236, "type": "application/vnd.mapbox-vector-tile", "png": false, "cache": "Hit from cloudfront"}
demo ogc-maps-png: {"status": 200, "bytes": 49033, "type": "image/png", "png": true, "cache": "Miss from cloudfront"}
demo licensing: {"currentEdition": "Pro", "licenseValidationState": "Valid", "entitlements": 69, "inactive": ["admin.multi-tenancy", "ai.approval-workflows", "alerts.dwell", "alerts.threshold", "analytics.h3", "channels.aws-sns", "channels.azure-eventgrid", "channels.digest", "channels.email", "channels.slack", "channels.teams", "dr.backup-automation", "dr.cache-backup", "dr.failover", "dr.rto-rpo-reporting", "editing.branch-versioning", "geocoding.batch", "identity.claims-mapping", "identity.mtls-client-certificate", "identity.oidc-multi-provider", "identity.saml", "identity.scim", "import.geoserver", "import.geoservices", "plugin.sdk", "raster.multidim-coverage", "scene.bim-ingest", "scene.pointcloud-ingest"]}
FAILED: ['pmtiles-maui-basemap', 'pmtiles-maui-buildings-static', 'pmtiles-maui-hillshade-static', 'pmtiles-maui-imagery-static', 'pmtiles-maui-parcels-static', 'pmtiles-maui-roads-static', 'pmtiles-maui-terrain-static', 'pmtiles-maui-zoning-static', 'mapserver-export-json', 'imageserver-export-json', 'licensing-mode-disabled', 'entitlements-all-active']
```

Exit code: 2.

## What turns this green

1. **Done 2026-10-24** (`foss4g-demo-apply-20261024.md`: all green except the two
   `*-export-json` checks). Apply the PMTiles + licensing plan (this PR), then
   `aws cloudfront create-invalidation --distribution-id "$(terraform output -raw cloudfront_distribution_id)" --paths "/api/v1/tiles/pmtiles/*"`.
   Expected: the eight `pmtiles-*` checks 206, `licensing-mode-disabled` and
   `entitlements-all-active` pass.
2. Create the key-ring secret and apply the flip in
   `runbook/exports-redis-prereq.md`. Expected: `mapserver-export-json` and
   `imageserver-export-json` return an `href`.
3. Re-run `runbook/scripts/rc1-upgrade-probe.sh after-foss4g-apply`; exit 0.
