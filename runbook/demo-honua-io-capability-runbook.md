# demo.honua.io full-capability runbook (server#1688)

Operator runbook to bring `demo.honua.io` to full demonstrable capability across the
four product pillars. It covers two separable tracks:

1. **Seed STAC collections** so the Imagery & Terrain Studio shows a live catalog.
2. **Apply a Pro license** so geocoding, realtime streaming, and FeatureServer editing
   light up.

> Scope note: the underlying server features already exist. This is **operational
> enablement** on the deployed demo. The only server code change shipped alongside this
> runbook is a fix for the `release-packages` admin API (see *Prerequisites*).

Account context (from #1688 recon): AWS `585192672263`, `us-west-2`, compute is Lambda
`honua-demo-demo-honua`; the demo DB is reachable only in-VPC via the bootstrap Lambda
`honua-demo-demo-postgis-bootstrap` (accepts `{"statements":[...]}` / `{"query":"..."}`).

Everything below is **operator-supplied where it touches secrets or the live env** —
those steps are marked **[OPERATOR]**. No secrets are committed in this repo.

---

## As-applied update (2026-07-24 — image redeploy, geocoding live, schema 089)

The 2026-07-24 ops round supersedes the two remaining open items in the table below:

- **Demo image redeployed**: Lambda version **36** now serves via the terraform-managed
  `live` alias, image `nightly-lambda-aot-6b65376-amd64` (trunk `6b65376` — includes
  server PRs #2993, #3005–#3007, #3013, #3015; image mirrored GHCR → account ECR, tag
  kept SHA-explicit to avoid date-tag collisions with the pre-merge morning nightly).
- **Schema at 091** (2026-08-12): out-of-band DbUp run per the frozen-version model (flip
  `HONUA_SKIP_MIGRATIONS` on `$LATEST` only → unqualified invoke → verify "Upgrade
  successful" → restore env byte-identically, verified). Migrations 083–089 applied
  earlier; 090 (`AddStudioContentItemOwner`) + 091 (`RenormalizeGeocodeReferenceSearchText`)
  applied 2026-08-12 after the serving v39 code (2026-08-09 deploy) shipped ahead of the
  schema — `POST /api/v1/studio/package-drafts` had been 500ing on the missing
  `owner_id` column (42703), failing the honua-studio nightly live smoke for 9 nights.
  Verified fixed: live-demo-smoke run 31610198250 green. Lesson repeated: a versioned
  deploy does NOT run migrations; pair every image publish that carries new
  `Honua.Server.Migrations` scripts with this out-of-band DbUp invoke.
- **Geocoding LIVE end-to-end** via Amazon Location + `geo.places` PrivateLink:
  `findAddressCandidates?singleLine=Kahului Airport, Maui` → 200 in **1.3s**, 5
  candidates, top `Kahului Airport, Kahului, HI, USA`. (Known cosmetic gap unchanged:
  `DescribePlaceIndex` has no PrivateLink service, so the provider *health* probe
  reports unreachable; request routing does not gate on it.)
- **ImageServer `maui-imagery` metadata**: 200 in ~20.6s on first (cold-cache) call —
  the #2993 statistics budget (20s) degrading gracefully instead of hanging past the
  platform timeout. No longer hangs.
- **PMTiles `maui-basemap`**: bare no-Range GET → fast `413` (by design, #2993);
  `Range: bytes=0-16383` → `206`. Real PMTiles clients (always ranged) unaffected.
- `/api/scenes` → 200 (0.7s); `/rest/services`, `/stac/collections`,
  `/ogc/features/collections` all 200 post-deploy.
- Redis remains **off** (`enable_redis = false`) until a demo image contains the
  server-side `aws:secretsmanager:` Redis-ref fix (server#3011, PR #3021).

## As-verified live state (2026-07-23, server#2948)

A fresh, read-only probe of `https://demo.honua.io` on 2026-07-23 found this runbook's
Tracks 1 and 2 **already applied and serving correctly**, plus an elevation/terrain
dataset that the 2026-07-20 probe (server#2948) reported missing but which is in fact
live. The table below is the current source of truth; the track sections further down
are kept for the mechanism/history but where they disagree with this table, this table
wins.

| Area | State | Evidence |
|---|---|---|
| Track 1 — STAC seed | **Live.** `/stac/collections` returns `90810` + `90820`; `/stac/search` against `90810` returns 4 features; both collections also appear in `/api/v1/streaming/features/capabilities` layer list. | Direct probe, 2026-07-23 |
| Track 2 — Pro license | **Live.** `/api/v1/capabilities/manifest` → `policies.currentEdition = "Pro"`, `licenseValidationState = "Valid"`. `geocoding.forward`/`geocoding.reverse`/`geocoding.failover`/`streaming.feature-subscriptions`/`editing.featureserver-edits` all `active: true`. The 14/29 `available:false` capability entries seen on an **anonymous** manifest call are RBAC (`insufficient-policy`, expected with no caller identity) or correctly `minimumEdition: Enterprise` gates (we're Pro) — not a licensing gap. | Direct probe, 2026-07-23 |
| Elevation / terrain | **Live and fully functional**, contradicting the 2026-07-20 probe. `maui-terrain` (layerId 8) is a registered raster dataset; `/elevation/maui-terrain/{value,profile,viewshed,line-of-sight,sun-shadow}` all return real computed results over real Maui terrain (elevations in a sane 4–590 m range), and `/terrain/maui-terrain/tile.json` + `.png` tiles serve `terrain-rgb` encoded tiles. No remediation needed. | Direct probe, 2026-07-23 |
| Catalog cleanup — layer 68823 | **Done.** Not present in `/rest/services` (25 services, none named `test_service`/68823) or `/ogc/features/collections` (11 collections). Direct probe of `/rest/services/test_service/FeatureServer` returns `499 Unauthorized` (`allowAnonymous: false`), consistent with the recorded remediation (`PUT /api/v1/admin/services/test_service/access-policy {"allowAnonymous": false}`). | Direct probe, 2026-07-23 |
| Geocoding | **Not working — categorical failure, not a cold-start.** The locator is published as `World` (not `maui`, which the original probe and this runbook's Step 4 assumed — `GET /rest/services/World/GeocodeServer?f=json` returns valid metadata with `Provider: nominatim`). But every `findAddressCandidates` call — first and repeated — fails after ~15.8s with `500 "Geocoding service error"`. Two consecutive calls both took ~15.8s (15.790s, 15.854s): this rules out a cold-start/keep-warm fix. Root cause (per the 2026-07-20/21 coordinator note, reconfirmed): the demo Lambda's VPC subnets have no NAT/IGW egress route, so the external Nominatim endpoint is unreachable from inside the VPC on every call. **Operator-decided fix (2026-07-23): switch to Amazon Location Service via a VPC interface endpoint (PrivateLink) — no NAT gateway.** IaC in `honua-io/honua-iac#127` (not applied); see *Remediation plan* item 1. | Direct probe, 2026-07-23 |
| SensorThings | **Confirmed intentional.** `/sta/v1.1/*` → 404. `Experimental:Features:SensorThings` defaults `false` in `src/Honua.Server/appsettings.json` with no environment override, and there is no SensorThings entry in the capabilities manifest at all (not even a gated one). This matches honua-io/honua-server#2434 ("Promote SensorThings to GA" — `roadmap:later`, still experimental). No action needed for this issue; GA promotion is tracked separately in #2434. | Direct probe + code read, 2026-07-23 |
| `/api/scenes` (adjacent, server#2991) | **Fixed live.** Returns `200` with the scene listing — confirms the out-of-band migration run (073→082) documented in #2991 has landed on the shared demo DB. | Direct probe, 2026-07-23 |
| ImageServer `maui-imagery` metadata (adjacent, server#2991/#2993) | **Still hangs** (>70s, no response) — expected: the code fix is in PR #2993, which has not been deployed to the demo image yet. Will be resolved by the next demo image redeploy (see remediation plan). | Direct probe, 2026-07-23 |

**Deployment model correction (learned 2026-07-23, applies to every step below that
touches Lambda env or the DB):** `demo.honua.io` serves a **published Lambda version**
via the `live` alias (currently **v33**), and that published version's **environment is
frozen** — editing environment variables on `$LATEST` has **no effect on what's
actually serving traffic**. The only way DB-side changes (seeds, migrations) reach the
live site is because they mutate the **shared RDS database**, which every Lambda
version/alias reads from — so a DB write done by invoking `$LATEST` directly *does*
show up on `demo.honua.io` even though `$LATEST` itself never serves a request. An
**environment variable** change (e.g. `Geocoding__LocatorName`, `Licensing:LicenseContent`)
does **not** reach `demo.honua.io` this way — it only takes effect once a **new Lambda
version is published and the `live` alias is repointed to it** (i.e. a real deploy).
This is why Track 1 (a DB write) and Track 2 (apparently an env change, but see below)
show up as live today while an env-only fix would not have. The out-of-band DB
migration procedure used in #2991 (invoke `$LATEST` directly, which runs
`HONUA_SKIP_MIGRATIONS=false`-equivalent startup migrations against the shared DB, then
leave `$LATEST`'s config untouched) is the template for any future schema-only fix; it
does **not** work for anything that must be visible to the *serving* environment
(license content, geocoding locator/provider config) — those need an actual versioned
deploy.

---

## Prerequisites (deploy a current image first)

Both tracks require a demo image built from `trunk` at or after the commits below, then
deployed to `honua-demo-demo-honua`:

- **`release-packages` 42P08 fix** — `GET /api/v1/admin/metadata/release-packages` must
  return 200 (it 500'd with `42P08: could not determine data type of parameter $1` on the
  prior image). Fixed in `PostgresMetadataReleasePackageStore.ListAsync` (typed text
  parameters). Required only if you drive metadata admin discovery; the STAC seed below
  does not depend on it, but the demo's admin/console metadata views do.
- **License-from-content** (#1698) — `Licensing:LicenseContent` must be honored. Confirm
  with `grep` in the image or by setting it and checking the capabilities manifest.

Health gate (Redis is required for a healthy server; STAC needs a current v2 snapshot —
the seed below activates one):

> Open `https://demo.honua.io/healthz/live`, `https://demo.honua.io/healthz/ready` in a browser.

---

## Track 1 — Seed STAC collections (REQ-001)

### Why a metadata-v2 seed, not a `honua.services`/`honua.layers` seed

The live catalog (`/stac/collections`, `/odata/Layers`, `/rest/services`) is materialized
from the **Metadata v2 graph snapshot** (`metadata.honua.io/v2alpha1`) stored in
`honua.metadata_v2_snapshots` + `honua.metadata_v2_current` — **not** from the legacy
`honua.services`/`honua.layers` tables. Raw INSERTs into the legacy tables are inert for
the catalog (confirmed on the deployed demo image). The STAC read path
(`Honua.Protocols.Stac.Services.StacV2Lookups`) only surfaces a collection when a
publication of type `stac-collection` exists on a service whose `protocols` include `Stac`,
backed by a resolvable resource + storage binding.

The seed handles all of this: it appends a STAC service + two `stac-collection`
publications (Maui Reef Watch `90810`, Maui Coastal Change `90820`), their resources and
storage bindings, into the **current active snapshot**, writes a new revision, and
activates it. Existing graph entities (the 11 `maui-*` layers) are preserved.

### Assets

- `tests/seed/demo-stac-imagery-v1.sql` — the idempotent seed (features + v2 graph merge).
- `tests/seed/apply-demo-stac-seed.sh` — psql wrapper.

### Run it

> **Do not derive the metadata environment from `server.deploymentEnvironment`.**
> The manifest field reports `IWebHostEnvironment.EnvironmentName` (`Production` on
> the demo), while the metadata graph independently reads `Metadata__Environment`,
> then `Environment`, with a `default` fallback. Operator records say the successful
> 2026-07-20/21 seed apply used `HONUA_SEED_ENV=Production`, and the live catalog
> confirms those rows are active, but the manifest alone does not prove that mapping.
> Before any repeat apply, inspect `Metadata__Environment` / `Environment` on the
> serving Lambda version or query the active environment in `metadata_v2_current`.

**[OPERATOR]** Set mandatory `SEED_ENV` to the env id the serving Lambda is configured with —
this MUST match the server's `Metadata__Environment` / `Environment` setting (it defaults
to `default`; confirm against the serving Lambda's environment variables or the active
`metadata_v2_current` row — the capabilities manifest's host-environment field is not
the metadata environment):

```bash
# Local / direct-psql target:
: "${SEED_ENV:?Set SEED_ENV from the serving Lambda configuration or active metadata_v2_current row}"
PGHOST=... PGPORT=5432 PGUSER=honua PGDATABASE=honua PGPASSWORD=... \
HONUA_SEED_ENV="$SEED_ENV" HONUA_SEED_SCHEMA=honua \
  tests/seed/apply-demo-stac-seed.sh
```

For `demo.honua.io` the DB is in-VPC only. Fetch the immutable server seed and use the
checked-in renderer to remove psql directives and safely substitute all `env`/`schema`
variables. The renderer emits the bootstrap Lambda's supported `statements` payload;
do not send the raw psql file. Because this Lambda accepts arbitrary SQL and executes it
with the database bootstrap credential, invoking it is effective database-administrator
privilege. Only an already-authorized break-glass DBA operator/role may perform this step.
Its IAM policy must scope `lambda:InvokeFunction` to this exact bootstrap function ARN,
but that AWS permission alone is not sufficient authorization; do not grant it to routine
deploy or application roles.

```bash
# [OPERATOR] render and invoke the in-VPC seed transaction
: "${SEED_ENV:?Set SEED_ENV from the serving Lambda configuration or active metadata_v2_current row}"
seed_ref=c5b9ffaf47a8b7dad25c5546b973eb427665fde1
curl --fail --location \
  "https://raw.githubusercontent.com/honua-io/honua-server/${seed_ref}/tests/seed/demo-stac-imagery-v1.sql" \
  --output /tmp/demo-stac-imagery-v1.sql
python3 stacks/aws/scripts/render-demo-stac-seed.py \
  --seed-file /tmp/demo-stac-imagery-v1.sql \
  --environment "$SEED_ENV" \
  --schema honua \
  > /tmp/demo-stac-seed-payload.json
aws lambda invoke --function-name honua-demo-demo-postgis-bootstrap \
  --payload fileb:///tmp/demo-stac-seed-payload.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/demo-stac-seed-response.json
jq -e '.statements | length == 1 and all(.[]; .ok == true)' \
  /tmp/demo-stac-seed-response.json

# Prove the physical table exists through the same in-VPC path.
jq -n --arg query "SELECT to_regclass('honua.features')::text" '{query: $query}' \
  > /tmp/demo-stac-check-payload.json
aws lambda invoke --function-name honua-demo-demo-postgis-bootstrap \
  --payload fileb:///tmp/demo-stac-check-payload.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/demo-stac-check-response.json
jq -e '.rows == [["honua.features"]]' /tmp/demo-stac-check-response.json
```

The seed is **idempotent**: re-running advances the snapshot revision and re-applies the
two collections without duplicating features or publications.

### Verify (REQ-001)

Open `https://demo.honua.io/stac/collections` in a browser and confirm it contains at least two collections, including `90810` and `90820`. Then use the [API explorer workflow](../../reference/openapi-and-explorer.md) for `POST /stac/search` with `{"bbox":[-156.70,20.60,-156.30,20.96],"collections":["90810"]}` and confirm the response contains features.

Acceptance: Imagery & Terrain Studio (`demo-imagery-terrain.html`) shows a live STAC
catalog instead of the bundled sample lane.

### Post-deploy semantic gate **[OPERATOR]**

This repository cannot safely produce a `demo-deployed` event: the production alias
promotion and database seed happen outside its GitHub OIDC trust. After the seed command
above succeeds and the generated `demo-services.v1.json` is published through the normal
Terraform deployment, bind the public canary to the exact runtime SHA from that deployment:

```bash
# Set this from the successful deployment output, not from an untrusted public response.
DEPLOYMENT_REVISION=<exact-40-character-runtime-sha>
test "$(printf '%s' "$DEPLOYMENT_REVISION" | wc -c)" -eq 40
test "$(curl --fail --silent https://demo.honua.io/api/v1/capabilities/manifest \
  | jq -r '.server.deploymentRevision')" = "$DEPLOYMENT_REVISION"
gh workflow run live-canary.yml \
  --repo honua-io/honua-demo-infra \
  --ref trunk \
  -f deployment_revision="$DEPLOYMENT_REVISION" \
  -f wms_admission=optional-live
```

The workflow derives the expected STAC seed URL, server commit, and manifest SHA-256
from the checked-out contract. It passes only when those exact bindings and a non-empty
collection `90810` item/search result are all present on the deployed runtime.

---

## Track 2 — Apply a Pro license (REQ-002/003/004/005)

A Pro license unblocks the three remaining Pro-gated demo areas. Entitlement keys
(from `FeatureCatalog`):

| Demo area            | Entitlement key                       | Min edition |
|----------------------|---------------------------------------|-------------|
| Realtime streaming   | `streaming.feature-subscriptions`     | Pro         |
| FeatureServer edits  | `editing.featureserver-edits`         | Pro         |
| Forward geocoding    | `geocoding.forward`                   | Pro         |
| Reverse geocoding    | `geocoding.reverse`                   | Pro         |
| Geocoding failover   | `geocoding.failover`                  | Pro         |

> Editing note: only the **Esri GeoServices FeatureServer** write surface
> (`applyEdits`) is Pro (`editing.featureserver-edits`). Editing via the **open
> protocols** (OGC API Features mutations, WFS-T, OData CRUD/`$batch`, gRPC) is
> Community and ungated. The editing demo exercises the FeatureServer write path, so it
> needs the Pro license.

### Step 1 — Mint the Pro license (offline, publisher-only) **[OPERATOR]**

Use the in-repo `honua-license-mint` CLI (`src/Honua.LicenseMint`). The signing key is the
trust root — **never commit it**.

```bash
# (a) one-time: generate the signing key pair
dotnet run --project src/Honua.LicenseMint -- keygen \
  --key-id honua-demo-2026q2 \
  --private-out ./honua-demo-2026q2.private    # chmod 600; store in Secrets Manager only

# (b) mint a Pro envelope (defaults to all entitlements at/below Pro)
dotnet run --project src/Honua.LicenseMint -- mint \
  --key-id honua-demo-2026q2 \
  --license-id lic-honua-demo-2026q2 \
  --licensed-to "Honua Demo (demo.honua.io)" \
  --edition Pro \
  --expires 2027-06-15T00:00:00Z \
  --private-key-file ./honua-demo-2026q2.private \
  --out ./honua-demo-license.json
```

`keygen` prints the runtime trusted-key setting:
`Licensing__TrustedKeys__honua-demo-2026q2=<base64url>`. Record it for Step 3.

> Per #1688 recon, a Pro envelope + signing key may already be staged in Secrets Manager
> (`honua-demo-demo/license`, `honua-demo-demo/license-signing-key`) with keyId
> `honua-demo-2026q2`. Reuse those rather than re-minting if present.

### Step 2 — Store the envelope in Secrets Manager **[OPERATOR]**

```bash
aws secretsmanager put-secret-value \
  --secret-id honua-demo-demo/license \
  --secret-string file://honua-demo-license.json --region us-west-2
```

Do **not** put the license envelope or signing key in the repo, in Terraform state, or in
client pages.

### Step 3 — Wire the license onto the demo Lambda **[OPERATOR]**

`Licensing:LicenseContent` accepts either the raw envelope JSON **or** a secret reference
(`aws:secretsmanager:<arn>`) which is resolved at startup — prefer the secret reference so
no secret material lands in the Lambda config:

```
Licensing__LicenseContent=aws:secretsmanager:arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/license
Licensing__TrustedKeys__honua-demo-2026q2=base64url:<public-key-from-keygen>
```

`LicenseContent` takes precedence over `LicensePath`, so this works on the read-only
Lambda filesystem with no bootstrap file write.

### Step 4 — Enable geocoding (resolve the 404, REQ-003) **[OPERATOR]**

> **2026-07-23 status: the 404 is already resolved, but geocoding still doesn't work.**
> `Geocoding:Enabled=true` with `Provider: nominatim` is live today, published under the
> **default `World` locator** (`GET /rest/services/World/GeocodeServer?f=json` returns
> valid metadata) — the `maui` locator name this step originally called for was never
> applied and is not needed; **update any demo probe/smoke script to call
> `/rest/services/World/GeocodeServer`, not `/rest/services/maui/GeocodeServer`.**
>
> The real, still-open problem is downstream of the locator: every
> `findAddressCandidates` call — first or repeated — fails after a consistent ~15.8s
> with `500 "Geocoding service error"`. Two back-to-back calls both took ~15.8s
> (15.790s, then 15.854s on a supposedly "warm" second call), which rules out a
> cold-start explanation. Per the 2026-07-21 coordinator finding, reconfirmed
> 2026-07-23: **the demo Lambda's VPC subnets have no NAT gateway / internet gateway
> route**, so the external Nominatim endpoint is categorically unreachable from inside
> the VPC — not slow, unreachable, timing out at whatever the outbound HTTP client
> timeout is configured to (~15–16s). A scheduled keep-warm probe cannot fix this: it
> would just be another call that times out the same way.
>
> **Operator decision (2026-07-23): fixed via Amazon Location Service over a VPC
> interface endpoint (PrivateLink), not a NAT gateway.** No new compute, no general
> egress opened up. IaC: `honua-io/honua-iac#127` (place index + IAM grant +
> `com.amazonaws.us-west-2.geo.places`
> interface endpoint — not applied). See *Remediation plan* item 1 below for the exact
> env keys, sequencing, and rollback; a NAT-gateway + Nominatim fallback is kept at item
> 1a if Amazon Location is ever rejected later.

Geocoding is published when `Geocoding:Enabled=true` with a configured provider and locator
name. On the demo Lambda, this is already configured (Nominatim/`World`, currently
broken per the callout above):

```
Geocoding__Enabled=true
Geocoding__LocatorName=World              # confirmed live; do not change without also
                                           # updating every demo probe/smoke script
Geocoding__DefaultProvider=nominatim       # confirmed live; PLANNED to become
                                           # amazon-location — see Remediation plan item 1
# provider config (endpoint/key) per Geocoding__Providers__* — not independently
# re-verified here; the failure is at the network layer (see callout above), before
# provider request construction would matter.
```

The forward/reverse geocoding **operations** are Pro-gated (`geocoding.forward` /
`geocoding.reverse`), so the Pro license from Steps 1–3 must be active (it is — see
*As-verified live state* above). This holds for either provider — `amazon-location` and
`nominatim` are both entitled the same way once Pro is active.

### Step 5 — Streaming + editing need no extra config

`streaming.feature-subscriptions` and `editing.featureserver-edits` are unlocked purely by
the active Pro license. Streaming additionally requires Redis (already a server health
prerequisite) for the change-feed/replay backend. The editing demo writes to a writable
OData/FeatureServer layer with CORS/If-Match/ETag already fixed (#1629/#1653).

### Step 6 — Redeploy and verify

`terraform apply` (or update the Lambda env + `update-function-code`) and re-run the
probes:

Open these demo URLs in a browser and inspect the named fields:

- `/api/v1/streaming/features/capabilities` — `enabled: true`, `edition: "Pro"`.
- `/rest/services/World/GeocodeServer?f=json` — `currentVersion` is present. The
  deployed locator is `World`, not `maui` (see the Step 4 callout).
- `/rest/services/World/GeocodeServer/findAddressCandidates?f=json&singleLine=Kahului`
  — `candidates` is non-empty once the VPC egress fix lands; as verified on
  2026-07-23, this request still returns 500 after about 15.8 seconds.
- `/api/v1/capabilities/manifest` — `policies.currentEdition` is `"Pro"`; the
  manifest has no top-level `license` object.

For REQ-005, make an authenticated edit with the `@honua/sdk-js` FeatureLayer
client and confirm it persists on re-read; keep write credentials server-side.

Acceptance (#1688): Public Safety Ops connects a live incident feed; dispatch geocoding
resolves live; Inspection & Editing persists an edit; the four probes pass from the
`honua.io` origin.

---

## End-to-end validation

With both tracks applied, re-run the SDK smoke from `honua-sdk-js`:

```bash
node scripts/site-demo-smoke.mjs    # the four affected pages leave their fixture/replay lanes
```

## Runnable now vs. needs live env / creds

| Item                                          | State                                   |
|-----------------------------------------------|-----------------------------------------|
| STAC seed SQL + apply script                  | **Already applied and live** (2026-07-23 confirmed) |
| `release-packages` 42P08 fix + tests          | **Merged, live**                        |
| License mint CLI commands                      | N/A — Pro license **already applied and live** (2026-07-23 confirmed) |
| Elevation/terrain dataset (`maui-terrain`)    | **Already registered and fully working** (2026-07-23 confirmed) — no action needed |
| Layer 68823 catalog cleanup                   | **Already applied and live** (2026-07-23 confirmed) |
| Geocoding — Amazon Location + PrivateLink (PRIMARY) | IaC ready, **not applied**: `honua-io/honua-iac#127` — **[OPERATOR]** review/apply, then fold `Geocoding__*` env into the image redeploy below |
| Geocoding — NAT gateway + Nominatim (fallback, not chosen) | **[OPERATOR + honua-terraform]** only if Amazon Location is rejected later |
| SensorThings GA promotion                     | Out of scope for this issue — tracked in #2434 |
| Demo image redeploy (picks up #2993 + migrations 083-088) | **[OPERATOR]** — live env, standard versioned-alias deploy |

## Verification workflow (read-only, run against `https://demo.honua.io`)

Use the `honua` CLI for the protocol surfaces it supports. The `jq -e` assertions
fail closed if a response is missing or has the wrong shape.

```bash
set -euo pipefail
export HONUA_BASE_URL=https://demo.honua.io

# Track 1 — STAC
honua stac collections --json \
  | jq -e '([.collections[].id] | index("90810")) != null and
           ([.collections[].id] | index("90820")) != null'
honua stac search --bbox=-156.70,20.60,-156.30,20.96 \
  --collections 90810 --limit 25 --json \
  | jq -e '.features | type == "array" and length > 0'

# Catalog cleanup — test_service must not be publicly discoverable.
honua services --json \
  | jq -e '(.services | type == "array") and
           ([.services[].name | ascii_downcase] | index("test_service") | not)'

# Geocoding is the one known-broken CLI check until the VPC egress fix lands.
if time honua geocode "Kahului" --locator World --limit 1 --json \
  | jq -e 'type == "array" and length > 0'; then
  echo "OK: geocoding returned a candidate"
else
  echo "KNOWN: geocoding still fails after about 15.8 seconds; see remediation item 1"
fi
```

There is no released CLI command for capability manifests, elevation analysis,
SensorThings, scene catalogs, or ImageServer metadata. Do not invent one. Verify
those surfaces through their native clients:

- Read the deployment manifest with
  `HonuaControlPlaneClient.getCapabilityManifest()` from `@honua/sdk-js/control-plane`.
  Require `policies.currentEdition == "Pro"`, `licenseValid == true`, and
  `licenseValidationState == "Valid"`.
- Use `SceneView.elevationProfile()`, `SceneView.viewshed()`, and
  `SceneView.lineOfSight()` from `@honua/sdk-js/scene-workspace` for
  `maui-terrain`. Use the generated API explorer for point elevation and
  sun-shadow until typed SDK methods ship. The expected results are 10 profile
  samples, a numeric `visibleSampleCount`, a boolean `visible`, and a boolean
  `shadowCast`.
- Use `SceneView.listScenes()` for the scene-catalog regression check; it must
  return a scene list.
- Open `/sta/v1.1/` in a browser and confirm 404 (intentional, #2434).
- Open `/rest/services/test_service/FeatureServer?f=json` in a browser and
  confirm 499 or 403, never 200.
- Open `/rest/services/maui-imagery/ImageServer?f=json` in an ArcGIS-compatible
  client or browser. It is expected to time out before #2993 is deployed and to
  return metadata within the bounded budget afterward.

## Remediation plan for open items (operator approval required before any step below)

Everything in *As-verified live state* above marked "Live"/"Already applied" needs **no
further action**. The items below are the only ones still open as of 2026-07-23.

### 1. Geocoding — Amazon Location Service via PrivateLink (PRIMARY, operator-decided 2026-07-23)

**Decision:** the operator chose Amazon Location Service reached over a VPC interface
endpoint (PrivateLink) as the fix — **not** a NAT gateway. No new compute, no general
egress opened up; the server's built-in `amazon-location` geocoding provider
(`Honua.Geocoding.Features.Geocoding.Providers.AmazonLocationGeocodeProvider`) talks to
Amazon Location's classic Places API (`SearchPlaceIndexForText` /
`SearchPlaceIndexForPosition` / `SearchPlaceIndexForSuggestions`) against a named place
index, authenticating via the Lambda execution role (`UseIamRole=true`, the default —
no access keys). IaC PR (not applied): **honua-io/honua-iac#127** — adds an
`enable_amazon_location_geocoding` toggle to the `aws-serverless` module (place index +
least-privilege IAM grant) and wires a
`com.amazonaws.us-west-2.geo.places` interface endpoint
into the demo's `vpc-endpoints.tf` (single-AZ, same pattern as the existing Secrets
Manager/Bedrock endpoints — see that PR for the exact resources and a read-only AWS
audit of the account's current VPC/endpoint state, including one piece of *unrelated*
pre-existing drift it flagged: the live `bedrock-runtime` endpoint spans all 3 private
subnets despite being documented as single-AZ).

**Why this is the primary fix, not the NAT+Nominatim fallback below:** no NAT gateway
to provision or pay for (~$33/mo saved), no general internet egress opened on a public
demo Lambda, and it reuses a provider the server already ships — this is config +
one new AWS resource, not new infrastructure surface area.

**This requires the versioned-alias deploy below, not a standalone env change.** All of
the following are plain (non-secret) Lambda environment variables — per the
*Deployment model correction* above, environment variables only take effect on a
**newly published Lambda version with the `live` alias repointed to it**; there is no
way to apply them to what's actually serving `demo.honua.io` short of a real deploy.
Fold them into the same deploy that picks up PR #2993 (item 2 below) rather than
attempting a separate env-only change:

```
Geocoding__Enabled                                   = true
Geocoding__DefaultProvider                           = amazon-location
Geocoding__EnableFailover                            = false
Geocoding__Providers__Nominatim__Enabled             = false
Geocoding__Providers__AmazonLocation__Enabled        = true
Geocoding__Providers__AmazonLocation__Region         = us-west-2
Geocoding__Providers__AmazonLocation__PlaceIndexName = <honua-iac output: amazon_location_place_index_name>
Geocoding__Providers__AmazonLocation__UseIamRole     = true
Geocoding__Providers__AmazonLocation__MaxResults     = 10
```

`Geocoding__EnableFailover=false` is the effective safeguard against the existing
Nominatim timeout. The current server registration path always registers Nominatim for
backward compatibility, even when
`Geocoding__Providers__Nominatim__Enabled=false`; with failover enabled, the coordinator
would therefore still try it after any Amazon Location error (for example, a place-index
typo) and hang for another ~15.8 seconds. Keep the provider-specific flag false to record
the intended provider set, but do not rely on it until runtime registration honors that
flag. For normal requests that omit the optional `provider` query parameter, this demo
deployment deliberately attempts only Amazon Location. An explicit
`provider=nominatim` request can still select the registered Nominatim provider and
incur the unreachable-path timeout; demo smoke tests and clients must not send that
override. Re-enable failover only after every registered fallback has a working network
path.

**Sequencing:**
1. **[OPERATOR]** Review and apply `honua-io/honua-iac#127` (`terraform plan` then
   `apply`, scoped to `enable_amazon_location_geocoding = true` plus the other toggles
   this environment already runs) — creates the place index, the IAM grant, and the
   `com.amazonaws.us-west-2.geo.places` VPC interface endpoint. This alone does **not**
   change what's serving
   `demo.honua.io` (no Lambda env change yet, and the place index/endpoint are inert
   until referenced).
2. **[OPERATOR]** Fold the `Geocoding__*` env block above into the image-redeploy
   step (item 2 below) — same publish-new-version-and-repoint-alias operation, so the
   code fix (#2993), the migrations (083–088), and the geocoding provider switch all
   land in the one deploy that actually changes live behavior.
3. **Expected verification** (after the deploy in item 2 completes): open the
   `World` GeocodeServer metadata in the generated API explorer and confirm
   `locatorProperties.Provider` is `amazon-location`, then run:
   ```bash
   export HONUA_BASE_URL=https://demo.honua.io
   time honua geocode "Kahului" --locator World --limit 1 --json \
     | jq -e 'type == "array" and length > 0'
     # expect a non-empty candidate array in well under 1s
   ```
4. **Rollback:** the `Geocoding__*` env change rolls back the same way any other part
   of this deploy does — repoint the `live` alias back to the prior version (see item
   2's rollback). The place index and VPC endpoint from `honua-iac#127` are additive
   AWS resources with no coupling to STAC/license/catalog state; `terraform destroy
   -target` (or setting `enable_amazon_location_geocoding = false` and applying) removes
   them cleanly once nothing references them, but there is no urgency to tear them down
   just because the Lambda alias was rolled back — they cost nothing extra idle beyond
   the endpoint's fixed per-hour charge.

**Data-source note:** results come from **Esri** (the iac PR's default; `Here` is the
other option), not OpenStreetMap — this is a full provider swap, not a drop-in
replacement with identical results. Coverage, address formatting, and attribution
differ from Nominatim.

**Cost:** `com.amazonaws.us-west-2.geo.places` VPC interface endpoint ~$7–8/month
(single-AZ, same pricing as the
existing Secrets Manager endpoint) + Amazon Location's per-request Esri pricing tier
(low single dollars/month at demo traffic volumes). No NAT gateway (~$33/mo avoided),
no new compute.

### 1a. Fallback — NAT gateway + Nominatim (not the chosen path; kept for reference)

If Amazon Location is ever rejected (e.g. Esri/HERE data-licensing concerns, or the
place index proves unreliable), the fallback is what this runbook originally proposed:

- **NAT Gateway egress.** Add a public NAT Gateway with an Elastic IP in a public
  subnet whose route table sends `0.0.0.0/0` to the VPC Internet Gateway, then update
  each private Lambda subnet's route table to send `0.0.0.0/0` to that NAT Gateway (or
  use a correctly routed NAT instance, cost-dependent). Lambda ENIs remain in the
  private subnets; placing the NAT Gateway there, or merely adding an Internet Gateway
  route to those private subnets, does not provide internet egress —
  `enable_nat_gateway = true` in the `aws-serverless` module call handles this routing
  correctly, but a hand-rolled NAT setup should double-check both route-table hops.
  Costs ~$33/mo + data, the exact cost the Amazon Location path avoids.
- **Rollback:** additive (new NAT route); can be torn down without touching license,
  STAC, or catalog state.
- Once reachable, Nominatim itself is fast — the current ~15.8s is purely the egress
  timeout, not provider latency — so *only in this fallback path* would a scheduled
  keep-warm probe be a meaningful mitigation for genuine cold-start latency (it is not
  a fix for the current failure mode either way, which is unreachability, not
  slowness).

### 2. Demo image redeploy (picks up PR #2993 + migrations 083–088 + the Amazon Location env switch)

Routine versioned-alias deploy. See the *Deployment model correction* note above for
why this must be a real publish-and-repoint, not an env edit on `$LATEST` — and why
item 1's `Geocoding__*` env variables are folded into this same step rather than
attempted separately.

- **[OPERATOR]** Once PR #2993 merges to `trunk` and `honua-io/honua-iac#127` is
  applied (place index + `com.amazonaws.us-west-2.geo.places` endpoint exists): build
  and push a new demo image from
  `trunk` HEAD, set the `Geocoding__*` env block from item 1 above on the new version,
  publish it, and repoint the `live` alias from v33 to the new version
  (`aws lambda update-alias --function-name honua-demo-demo-honua
  --name live --function-version <new-version>` or the equivalent `terraform apply`).
- **Expected verification:**
  - `GET /rest/services/maui-imagery/ImageServer?f=json` returns within the 20s
    statistics budget (not a 70s+ hang).
  - A bare `GET /api/v1/tiles/pmtiles/maui-basemap` (no `Range` header) returns `413`
    with the byte-limit message, not an opaque 500.
  - `GET /api/v1/admin/observability/migrations` (authenticated) shows migrations
    through `088_CreateNetworkTopologyPromotions`.
  - Geocoding: see item 1's verification block (`locatorProperties.Provider ==
    "amazon-location"`, `findAddressCandidates` in well under 1s).
  - Everything else in the *As-verified live state* table above still holds (STAC, Pro
    license, elevation, catalog cleanup, SensorThings 404) — this is a regression
    check, not expected to change any of those.
- **Rollback:** repoint the `live` alias back to v33
  (`aws lambda update-alias --function-name honua-demo-demo-honua --name live
  --function-version 33`) — this reverts the geocoding provider switch along with
  everything else in the deploy, back to Nominatim/`World` (broken, as today) until a
  fixed version is published. No DB rollback needed — migrations 083–088 are additive
  (`CreateNetworkDataset*`/`CreateOpsHealth*`/network-topology tables) and unrelated to
  any table the demo currently reads from.

### 3. SensorThings

No action for this issue. `Experimental:Features:SensorThings=false` (default, no
override) is the intended state; GA promotion is tracked in #2434. Documented here to
close out this issue's acceptance criterion ("confirmed as intentionally gated ... and
recorded in the runbook").

---

## Studio AI (Bedrock BYOM) — pilot rehearsal enablement (#9, honua-server#3000)

Live AI generation in Honua Studio on the demo, backed by Amazon Bedrock in the demo's
own account (`585192672263`) — bring-your-own-model, no third-party API key: the
server's Studio AI proxy `bedrock` provider authenticates via the Lambda execution
role, so no secret is staged anywhere for this feature.

**What it enables**: the Studio pilot journey's AI beats (compose assistance /
generation via `POST /api/v1/studio/ai/chat`, capabilities via
`GET /api/v1/studio/ai/capabilities`) run against a real model instead of being
unconfigured (the proxy is `Enabled=false` by compiled default).

**How**: `enable_studio_ai = true` in `stacks/aws/terraform.tfvars`, then the normal
plan/apply flow (`stacks/aws/README.md` → "Studio AI proxy on Bedrock"). Terraform
adds the least-privilege `bedrock:InvokeModel(+WithResponseStream)` grant scoped to
the inference-profile + foundation-model ARNs of the active model **and** the declared
fallbacks, and injects the `StudioAiProxy__*` env block (model
`us.anthropic.claude-opus-5`, region `us-west-2`). Bedrock is reached through the
fck-nat egress (see "Cost & network architecture" below) — no dedicated VPC endpoint.
A deployed image containing honua-server#3000's Studio AI proxy is required — check the deployed image's trunk SHA includes that feature
before expecting the endpoints to exist.

**Model access state (as of 2026-07-24)**:

- `us.anthropic.claude-opus-5` (the default): the account's foundation-model
  agreement for `anthropic.claude-opus-5` was accepted on 2026-07-24 via
  `aws bedrock create-foundation-model-agreement` (`agreementAvailability:
  AVAILABLE`); the cross-region inference profile is ACTIVE. **Runtime entitlement
  can lag the agreement** — immediately after acceptance, `converse` still returned
  `AccessDeniedException: anthropic.claude-opus-5 is not available for this account`.
  Run the Opus smoke below until it succeeds before relying on it in a rehearsal.
- `us.anthropic.claude-sonnet-4-6` (the fallback, covered by the same IAM grant):
  verified invocable end-to-end on 2026-07-24, from us-east-1 and from us-west-2.
  If Opus 5 entitlement has not landed by rehearsal time, set
  `studio_ai_model = "us.anthropic.claude-sonnet-4-6"` and re-apply — env-only
  change, no IAM edit needed.

**Validate** (smoke the exact model the proxy will use, in the region it calls):

```bash
# Primary — Claude Opus 5 (succeeds once entitlement propagation completes)
aws bedrock-runtime converse --region us-west-2 \
  --model-id us.anthropic.claude-opus-5 \
  --messages '[{"role":"user","content":[{"text":"Reply with the single word: ok"}]}]' \
  --inference-config '{"maxTokens":16}'

# Fallback — Claude Sonnet 4.6 (verified working 2026-07-24)
aws bedrock-runtime converse --region us-west-2 \
  --model-id us.anthropic.claude-sonnet-4-6 \
  --messages '[{"role":"user","content":[{"text":"Reply with the single word: ok"}]}]' \
  --inference-config '{"maxTokens":16}'
# -> {"output":{"message":{"content":[{"text":"ok"}]}}, "stopReason":"end_turn", ...}
```

Then end-to-end after apply: `GET https://demo.honua.io/api/v1/studio/ai/capabilities`
should list the `bedrock` provider, and a Studio chat turn should stream a completion.

**[OPERATOR] Enabling other Anthropic models later** (e.g. Opus 4.8): same shape as
the Opus 5 step above — accept the model's foundation-model agreement (Bedrock console
→ **Model access**, or `aws bedrock create-foundation-model-agreement`; Anthropic
models require the EULA, and the grant is account+region-scoped, so cover each `us.`
member region: us-east-1, us-east-2, us-west-2), wait out entitlement propagation,
smoke with `converse`, then either add the profile id to `studio_ai_fallback_models`
or set it as `studio_ai_model` and re-apply — the IAM grant is derived from those two
variables and re-scopes automatically.

**Rollback**: `enable_studio_ai = false` + apply — removes the env block and the IAM
inline policy; the shared Bedrock VPC endpoint remains while `enable_bedrock_ai` is
on. Additive, no coupling to seeds, license, or geocoding.


---

## Cost & network architecture (2026-07-24 cost round; RDS correction 2026-07-31)

Fixed spend was cut from ~$140/mo to ~$46–51/mo. Two changes, both in
`stacks/aws` (see `stacks/aws/README.md` → "fck-nat NAT instance" and "Estimated
monthly cost" for the full tables):

1. **fck-nat NAT instance replaces ALL interface VPC endpoints.** The no-NAT design
   reached AWS services through interface endpoints — 12 module deploy-control ENIs
   (lambda/sts/monitoring/logs, ~$88/mo) + 3 demo single-AZ ENIs
   (secretsmanager/bedrock-runtime/geo.places, ~$22/mo). A single fck-nat t4g.nano +
   EIP in one public subnet (`nat-instance.tf`, ~$7.5/mo all-in) now provides the
   private subnets' default route; only the free S3 gateway endpoint remains (it also
   keeps tile/import S3 bytes off the NAT). Side effect: the VPC has true internet
   egress for the first time — Nominatim/OIDC/webhooks are network-possible again.
2. **Lambda reserved concurrency remains 25; RDS is db.t4g.small.** The attempted
   small → micro cost cut was reversed on 2026-07-31 after the real Console browser
   journey reproduced PostgreSQL 53300 failures at only 16 concurrent environments
   (72 reported DB connections). The small instance is the public-demo reliability
   floor; the lower Lambda cap supplies additional connection headroom.

**Redis / ElastiCache stays as-is**: `enable_redis` remains `false` in Terraform
(nothing applied to remove) — honua-server hard-requires a durable feature-change
event store in Production, and the toggle stays available for when the server-side
`aws:secretsmanager:` Redis-ref fix (server#3011 / PR #3021) ships in a deployed
image. Do not delete the toggle to save the ~$9/mo — that breaks `/healthz/ready`
the day Redis is wired.

**Budget tripwire**: `aws_budgets_budget` (`cost-controls.tf`) — monthly $150 limit,
email alerts to `mike@honua.io` at 100% and 200% (i.e. $150 and $300), both actual
and forecasted spend.

### NAT instance: SPOF, recovery, resize [OPERATOR]

- **Accepted SPOF** (demo posture): one instance in one AZ. If it (or its AZ) dies,
  private-subnet egress is down — the public site keeps serving (CloudFront → API
  Gateway → Lambda invoke is not routed through the NAT, and CloudFront keeps
  serving cached tiles), but the Lambda loses Secrets Manager (cold starts fail),
  Bedrock, Amazon Location, and deploy-control until the NAT is back.
- **Recreate** (~2 min): `terraform apply -replace=aws_instance.nat` from
  `stacks/aws`. The EIP and the private-route entries re-associate automatically.
- **Resize**: set `nat_instance_type` (t4g.nano → t4g.micro/small) in tfvars and
  apply. t4g.nano sustains ~5 Gbps burst — far beyond demo traffic; resize only on
  measured saturation (CloudWatch `NetworkOut` on the instance).
- **AMI updates**: the config pins the AMI via `ignore_changes` so a new fck-nat
  release never replaces the live NAT on an unrelated apply. To take an update
  deliberately: `terraform apply -replace=aws_instance.nat` (picks up the latest
  `fck-nat-al2023-*-arm64` AMI at that moment).
- **Orphan check after the migration apply**: if any of the old interface endpoints
  were never imported into state (see "Pro + AI demo drift"), Terraform cannot
  destroy them — verify with `aws ec2 describe-vpc-endpoints
  --filters Name=vpc-endpoint-type,Values=Interface` and delete leftovers by hand,
  or they keep billing ~$7.5/mo each.
