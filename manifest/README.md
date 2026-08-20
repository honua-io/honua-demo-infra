# demo-services.v1.json — generated demo service manifest (#19)

`demo-services.v1.json` is the machine-readable inventory of the **publicly
discoverable** services seeded into demo.honua.io. It is **generated, never
hand-maintained**: `generate-demo-services.py` derives it entirely from the
repo's seed definitions, and CI (`.github/workflows/manifest-drift.yml`) fails
any change where the committed manifest and the seeds disagree.

Consumers (honua-io/honua-sdk-js#825) vendor the published file byte-for-byte
and drift-check against the stable public URL, so the file must stay
deterministic — no timestamps, stable ordering, fixed formatting.

## Stable public URL

```
https://demo.honua.io/demo-services.v1.json
```

The schema version is part of the file name (and of the `format` field). A
breaking schema change ships as a **new** `demo-services.v2.json` at a new URL
while v1 keeps serving, so consumers never break on upgrade. Additive,
backward-compatible fields may land within v1 (bump `schemaVersion` minor).

### Publish status — live

The publish wiring in `stacks/aws/demo-services-manifest.tf` is live and
tracked in the shared Terraform state: an S3 object under the public
`manifest/` prefix, an API Gateway `GET /demo-services.v1.json` proxy route,
and the `PublicReadDemoServicesManifest` bucket-policy statement in
`stacks/aws/seed-data.tf`. It was applied as an isolated saved plan on
2026-07-31; every later apply republishes the current
committed file, keeping the deploy/seed pipeline and the publish step one
flow.

The sdk-js consumer can now drift-check the stable published URL directly.

## Generation sources

| Source | What it provides |
|---|---|
| `stacks/aws/SEED_MANIFEST.md` — "Vector layers" table | The 7 `maui-*` FeatureServer/OGC feature services: service id (= layer name = OGC collection id), FeatureServer layer id/path, seeded feature count, source attribution, vintage, license |
| `stacks/aws/SEED_MANIFEST.md` — "Raster layers" table | `maui-hillshade` / `maui-imagery` ImageServer tile routes and `maui-terrain` Terrain-RGB route, sources, licenses |
| `stacks/aws/SEED_MANIFEST.md` — "Basemap + glyphs" table | The `maui-basemap` PMTiles proxy service and the `/fonts/{fontstack}/{range}.pbf` glyph asset |
| honua-server `tests/seed/demo-stac-imagery-v1.sql` at the **pinned ref in the repo README** ("Seed data" section raw URL) | The `demo-stac` STAC service and its collections (`90810` Maui Reef Watch, `90820` Maui Coastal Change): ids, names, titles, descriptions, licenses, bboxes |
| `generate-demo-services.py` public-process allow-list | The single bounded `geometry.buffer` OGC API Processes contract, exact schema/auth/lifecycle/request-budget policy, and deterministic sync/async canary digest |

The generator reads the pinned honua-server ref **from README.md** — bumping
the seed fixture ref there is the single place that changes, and the drift
check forces the manifest to be regenerated in the same PR.

Endpoint shapes (OGC tiles template, MVT source-layer `layer`, terrain
`tile.json`) mirror the as-built demo page contract (honua-site
`assets/demo/layers.json`) and the honua-server endpoint registry; they are
constants in the generator, not per-service hand-maintained data.

### Deliberate exclusions

- **`test_service`** — the SDK client-compat target is no longer anonymously
  readable (`allowAnonymous: false` since 2026-07-23, see the capability
  runbook); the manifest lists public surface only.
- **`maui-*-static` PMTiles archives** — honua-site demo-page performance
  artifacts defined by honua-site's `assets/demo/layers.json`, not by this
  repo's seeds.
- **`maui_*_meta` raster availability stubs** — internal seed plumbing, not a
  demo service.
- Credential **values**, and all infra internals (bucket names, AWS resource
  ids, connection ids) — by construction the generator never emits them. The
  public process descriptor names its scoped `demo-process-execute` credential
  profile and header, but never the key; scheduled proof reads that key only
  from the `HONUA_DEMO_GP_API_KEY` Actions secret.

## Protected client-compat descriptor

`client-compat.v1.json` is a separate, non-secret governed descriptor for the
protected `test_service` certification fixture. It is generated from
`stacks/aws/client-compat-seed.v1.json`; it is not published as the anonymous
demo-services inventory and does not make a GA claim. Its drift validator also
fails if the seed policy becomes anonymous or if `test_service` appears in
`demo-services.v1.json`.

```bash
python3 manifest/generate-client-compat.py
python3 manifest/generate-client-compat.py --check
```

The descriptor stores only secret/variable names. Fresh authenticated
evidence comes from `scripts/client-compat-canary.mjs` with an exact server
commit and immutable image digest.

## Schema (`honua.demo-services.v1`)

Top level:

| Field | Type | Meaning |
|---|---|---|
| `format` | string | Constant `honua.demo-services.v1` |
| `schemaVersion` | string | Semver of the v1 schema (additive changes bump minor) |
| `description` | string | Human note incl. the generated-only rule |
| `baseUrl` | string | `https://demo.honua.io` — all `path`/`tileTemplate` values are relative to it |
| `publishUrl` | string | The stable public URL above |
| `sources` | object | Provenance: `seedManifest` (repo path), `stacSeed` (pinned raw URL), and `stacSeedSha256` (digest of those exact source bytes) |
| `services` | array | One entry per publicly discoverable service (below) |
| `processes` | array | Exact public process allow-list; 2026.1 contains only bounded, read-only `geometry.buffer` |
| `assets` | object | Non-service public assets; currently `glyphs` (`path` template + `fontstacks`) |

### Governed public process

`processes[0]` is the only AI/SDK-safe public geoprocessing contract:

- `geometry.buffer`, backed by the managed in-Lambda/local executor; AWS Batch
  remains disabled for this bounded path.
- Direct GeoJSON geometry only, SRID 4326, positive planar distance capped at
  one input-CRS unit. No layer/artifact references, custom code, geodesic mode,
  or mutating output is advertised.
- Synchronous (`Prefer: respond-sync`) and durable asynchronous
  (`Prefer: respond-async`) execution, with status/results paths. `DELETE` is
  documented truthfully as active-job cancellation; the manifest does not
  claim the full OGC dismiss conformance class for completed-job cleanup.
- `X-API-Key` using a separately rotated `demo-process-execute` key with only
  the canonical `process:*:execute` grant. The key is never published in this manifest or in canary
  receipts.
- A Redis-backed, per-subject global fixed window of 60 requests/minute. The
  canary requires the limit/remaining/reset response headers and binds both
  sync and async output bytes to the checked-in SHA-256.

Service entry (fields present depend on `type`):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Demo service id (vector: also the FeatureServer service name and OGC collection id) |
| `type` | string | `feature` \| `raster` \| `terrain` \| `basemap` \| `stac` |
| `layerName` | string | Vector only — equals `id` (seed invariant: service name == layer name == collection id) |
| `featureCount` | number | Vector only — seeded feature count from the seed manifest |
| `protocols` | object | Per-protocol access blocks, keyed by protocol family (below) |
| `title` | string | STAC only — catalog title from the seed SQL |
| `source` | string | Public source attribution from the seed tables |
| `vintage` | string | Vector only — data vintage from the seed table |
| `license` | string | License/attribution requirement from the seed tables |

Protocol blocks:

- `featureServer` — `layerId` (number), `path` (`/rest/services/{id}/FeatureServer/{layerId}`)
- `ogcFeatures` — `collectionId`, `path` (`/ogc/features/collections/{id}`)
- `ogcTiles` — `collectionId`, `tileMatrixSetId` (`WebMercatorQuad`), `tileTemplate` (MVT), `mvtSourceLayer` (constant `layer`)
- `imageServerTiles` — `tileTemplate` (`/rest/services/{id}/ImageServer/tile/{z}/{y}/{x}`)
- `terrainRgb` — `tileTemplate` (`/terrain/{id}/{z}/{x}/{y}.png`, note x/y order), `tileJson`, `encoding` (`mapbox-terrain-rgb`)
- `pmtiles` — `archiveId`, `path` (`/api/v1/tiles/pmtiles/{archiveId}` range proxy)
- `stac` — `path`, `collectionsPath`, `searchPath`, `collections[]` (each: `id`, `name`, `title`, `description`, `license`, `bbox` [west, south, east, north], `path`)

Example entry:

```json
{
  "id": "maui-parcels",
  "type": "feature",
  "layerName": "maui-parcels",
  "featureCount": 51245,
  "protocols": {
    "featureServer": { "layerId": 1, "path": "/rest/services/maui-parcels/FeatureServer/1" },
    "ogcFeatures": { "collectionId": "maui-parcels", "path": "/ogc/features/collections/maui-parcels" },
    "ogcTiles": {
      "collectionId": "maui-parcels",
      "tileMatrixSetId": "WebMercatorQuad",
      "tileTemplate": "/ogc/tiles/collections/maui-parcels/tiles/WebMercatorQuad/{z}/{y}/{x}",
      "mvtSourceLayer": "layer"
    }
  },
  "source": "ParcelsZoning/MapServer/30 \"Maui County Parcels\"",
  "vintage": "county data May 2025",
  "license": "Public domain (Hawaii Statewide GIS / County of Maui)"
}
```

## Regenerating

```bash
python3 manifest/generate-demo-services.py          # rewrite after a seed change
python3 manifest/generate-demo-services.py --check  # what CI runs
```

Generation fetches the pinned STAC seed SQL from raw.githubusercontent.com
(public, pinned ref — deterministic). Offline, pass
`--stac-sql /path/to/demo-stac-imagery-v1.sql` with a copy of the pinned
file. The generated source digest is also the value the trusted in-VPC
operator gate must observe in `honua.demo_seed_revisions`; URL equality alone
is not evidence that those bytes were applied.
