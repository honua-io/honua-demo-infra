# Inspection & Editing demo: anonymous writes on the scratch layer

Operator procedure for making Save on <https://honua.io/demo-editing.html>
write to demo.honua.io. It also covers turning writes off again and resetting
the synthetic inspection points to their seed. **Nothing in this file has been
run against demo.honua.io.** The coordinator runs it after the operator's go.

## What is actually blocking writes (verified 2026-10-24)

The access policy is **not** the blocker. A read-only admin
`GET /api/v1/admin/services/maui-inspections/settings` already returns
`"accessPolicy":{"allowAnonymous":true,"allowAnonymousWrite":true}`. That was
set at seeding time (2026-06-12).

The blocker is how the layer was published. `maui-inspections` (OData layer
12) is published `storageMode: source` over the typed table
`public.maui_inspections` through the `demo-rds` connection. For a layer like
that, honua-server answers:

| Surface | Anonymous answer today | Why |
| --- | --- | --- |
| OData `PATCH /odata/Layers(12)/Features(…)` | `501 ProviderWriteNotSupported` | `ODataFeatureProviderResolver.CheckWriteSupportAsync`: no binding-aware writer for connection-bound layers |
| OGC API Features `PATCH …/collections/12/items/…` | `405` | the publication declares only `Query`; declaring `Update` is refused for source mode, and OGC writes then 501 (`ValidateWriteRoutingAsync`) |
| capability manifest `edit.features` | `available:false, insufficient-policy` | informational only; reported for every anonymous caller whatever the layer allows |

Probe used (it changes nothing, because ObjectId 1 does not exist and the
provider check runs first):

```sh
curl -s -w '\nHTTP %{http_code}\n' -X PATCH -H 'Content-Type: application/json' \
  -d '{"Attributes":{"note":"probe"}}' 'https://demo.honua.io/odata/Layers(12)/Features(1)'
# -> 501 {"error":{"code":"ProviderWriteNotSupported",...}}
```

**The fix:** publish a **managed-store editable copy** of the same table as a
separate service, `maui-inspections-scratch`, with edit capabilities. Then set
its access policy to allow anonymous writes.
- The copy lands in the managed `features` table under its own layer id. Edits never touch `public.maui_inspections`.
- The original `maui-inspections` service is left exactly as it is.
- The honua-site page (`config.json` `odata.layerNames`) prefers `maui-inspections-scratch` when it is published. It falls back to `maui-inspections`, which still answers 501. The page classifies that honestly.

The whole sequence below was proven against a local server. See
[Local rehearsal](#local-rehearsal).

## Prerequisites

- AWS credentials with `secretsmanager:GetSecretValue` on
  `honua-demo-demo/admin-password`. This is the same key `scripts/demo-b-probes.sh` uses.
- `curl` and `python3`.

```sh
export BASE=https://demo.honua.io
export HONUA_DEMO_API_KEY="$(aws secretsmanager get-secret-value \
  --secret-id honua-demo-demo/admin-password --query SecretString --output text)"
# demo-rds connection id (read-only; expect 4f468b76-9937-4835-9e64-ddc8012b30c1)
curl -s -H "X-API-Key: $HONUA_DEMO_API_KEY" "$BASE/api/v1/admin/connections" \
  | python3 -c 'import json,sys;[print(c["connectionId"],c["name"]) for c in json.load(sys.stdin)["data"]]'
export CONNECTION_ID=4f468b76-9937-4835-9e64-ddc8012b30c1
```

## Enable: publish the scratch copy and allow anonymous writes

1. Publish the managed copy. The 27 rows are copied from
   `public.maui_inspections` at publish time.

   ```sh
   curl -s -w '\nHTTP %{http_code}\n' -X POST \
     -H "X-API-Key: $HONUA_DEMO_API_KEY" -H 'Content-Type: application/json' \
     "$BASE/api/v1/admin/connections/$CONNECTION_ID/layers" -d '{
       "schema": "public", "table": "maui_inspections",
       "layerName": "maui-inspections-scratch", "serviceName": "maui-inspections-scratch",
       "description": "Synthetic inspection points: editable scratch copy for honua.io/demo-editing.html (anonymous writes, reset to seed)",
       "geometryColumn": "geometry", "geometryType": "Point", "srid": 4326, "primaryKey": "id",
       "fields": ["id","name","category","status","note","reported_at"],
       "storageMode": "managed",
       "capabilities": ["Query","Extract","Create","Update","Delete"],
       "enabled": true }'
   ```

   Expect `201` with `"storageMode":"managed"` and
   `"capabilities":["Query","Extract","Create","Update","Delete"]`. Note the
   returned `data.layerId`; you need it to remove the layer later.

2. Set the access policy. Until this runs, the new service is not anonymously
   readable and is absent from anonymous `GET /odata/Layers`.

   ```sh
   curl -s -w '\nHTTP %{http_code}\n' -X PUT \
     -H "X-API-Key: $HONUA_DEMO_API_KEY" -H 'Content-Type: application/json' \
     -d '{"allowAnonymous": true, "allowAnonymousWrite": true}' \
     "$BASE/api/v1/admin/services/maui-inspections-scratch/access-policy"
   ```

3. Verify the result anonymously. These probes do not mutate anything.

   ```sh
   curl -s "$BASE/odata/Layers" | python3 -c 'import json,sys;print([(r["Id"],r["Name"]) for r in json.load(sys.stdin)["value"] if "insp" in r["Name"]])'
   # -> [(12, 'maui-inspections'), (<new id>, 'maui-inspections-scratch')]
   SCRATCH_ID=<new id>
   curl -s "$BASE/odata/Layers($SCRATCH_ID)/Features?\$top=500" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["value"]))'   # -> 27
   curl -s -o /dev/null -w '%{http_code}\n' -X PATCH -H 'Content-Type: application/json' \
     -d '{"Attributes":{"note":"probe"}}' "$BASE/odata/Layers($SCRATCH_ID)/Features(1)"
   # -> 404 (write admitted, no such row). 501 = wrong storage mode; 401 = policy not applied.
   ./runbook/scripts/reset-inspections-scratch.sh          # dry run -> "already in seed state"
   ```

   Then open <https://honua.io/demo-editing.html> after the honua-site PR is
   deployed:
   - The status pill reads `… · Save sends a live OData write`.
   - Save turns the card result into `saved on the server · HTTP 200 · re-read from the server`.
   - The writes chip turns into `writes: live (OData CRUD)`.

   CORS needs no change. The page sends only `Content-Type` and `Accept`, both
   already admitted. honua-demo-infra#96 adds `If-Match`/`OData-Version` for
   optimistic concurrency later.

## Reset the scratch layer to its seed

`runbook/scripts/reset-inspections-scratch.sh` reconciles the scratch layer
against `runbook/scripts/maui-inspections.seed.geojson`. That file is a byte
copy of honua-site `assets/demos/editing/inspections.geojson`, sha256
`293a93734f5dbd8af69ba9e1ebf548fa48f17ef154a1646c9f0e2da09ee69d0e`. The same
27 points are live in `public.maui_inspections`; a dry run against layer 12
keeps all 27.

The script works over the server's own OData API with the admin key:
- An identical row is kept.
- A row with a seed point's name is PATCHed back to that point.
- Any other row is DELETEd.
- A missing seed point is POSTed.

It is a **dry run by default**. `--apply` executes the plan, then re-reads the
layer and exits 1 unless exactly the 27 seed points remain. A second run plans
nothing. It refuses any layer whose fields are not the inspection fields. It
only targets `LAYER_NAME`, which defaults to `maui-inspections-scratch`.

```sh
./runbook/scripts/reset-inspections-scratch.sh            # dry run: prints keep/patch/delete/post
./runbook/scripts/reset-inspections-scratch.sh --apply    # reset + verify
```

ObjectIds of kept and patched rows stay stable. Re-posted points get new
ObjectIds, which the page resolves at load time.

The script is not scheduled anywhere yet; see the recommendation below. The
honua-site page says only "reset to its 27-point seed by the demo operators".
It does not say "nightly".

## Revert

- **Writes off, reads stay:** the page then shows "this server's access policy
  does not allow anonymous writes on maui-inspections-scratch (HTTP 401)".

  ```sh
  curl -s -w '\nHTTP %{http_code}\n' -X PUT \
    -H "X-API-Key: $HONUA_DEMO_API_KEY" -H 'Content-Type: application/json' \
    -d '{"allowAnonymousWrite": false}' \
    "$BASE/api/v1/admin/services/maui-inspections-scratch/access-policy"
  ```

- **Remove the scratch layer:** use the `layerId` from enable step 1. The page
  falls back to `maui-inspections`, and Save shows the 501 message.

  ```sh
  curl -s -w '\nHTTP %{http_code}\n' -X PUT \
    -H "X-API-Key: $HONUA_DEMO_API_KEY" -H 'Content-Type: application/json' \
    -d '{"enabled": false}' \
    "$BASE/api/v1/admin/connections/$CONNECTION_ID/layers/<layerId>/enabled?serviceName=maui-inspections-scratch"
  ```

  Without `?serviceName=`, this endpoint answered 404 for the managed layer in
  the local rehearsal.

## Abuse surface and request budget

Anonymous visitors can PATCH, POST and DELETE rows on one synthetic layer.
Live settings, read with `aws lambda get-function-configuration` on
2026-10-24, plus the server defaults at honua-server trunk `0ffe349d78`:

| Control | Value | Applies to anonymous writes? |
| --- | --- | --- |
| `RateLimiting__Enabled` / `RateLimiting__GlobalRequestsPerMinute` | `true` / `60` | Yes. It is per subject, and anonymous callers are keyed `ip:{client ip}` (IPv6 by /64). Counters are shared through Redis (`UseDistributedRateLimiting` default `true`). Reads and writes share the one counter. |
| Per-endpoint `[RateLimit(n)]` | admin-auth and Studio AI only | No write route has its own budget |
| API Gateway throttle (`api_throttle_rate_limit` / `_burst_limit`) | 50 rps / 200 burst | Yes. It is account-wide for the stage. |
| Lambda reserved concurrency | 25 | Yes |
| `Limits__Edits__MaxFeaturesPerEdit` / `MaxEditsPerTransaction` / `MaxPayloadSize` | 500 / 2500 / 25 MB (defaults) | Yes. They cap one OData `$batch` changeset, and `$batch` is also anonymous. |

What a visitor can do:
- About 60 requests per minute per IP. Through `$batch`, a single request can carry many inserts.
- Add free-text notes and names (≤ the field lengths) that other visitors then see.
- Write enum values outside `trail|park|harbor|facility` / `ok|needs_attention|urgent`.
  - The managed copy does not inherit the typed table's CHECK constraints.
  - In the local rehearsal, `category: "zzz"` was accepted with 201.
  - The page itself only sends valid values.
- Nothing outside the layer is reachable. The source table and every other service are untouched (verified locally: `public.maui_inspections` unchanged after edits on the copy).

**Recommendation:**
- Keep the existing 60/min per-IP budget as the cap and do **not** add a separate write budget for this.
  - The server has no per-route write budget to configure.
  - Lowering the global budget would hurt the read-heavy demo pages that share it.
- The real control is the reset. Run `reset-inspections-scratch.sh --apply`:
  - before and after each FOSS4G session;
  - on a nightly schedule. The schedule is not created here: a small EventBridge-scheduled job or an ops-host cron with the same secret.
- If abuse shows up, use the first revert, which is a single PUT.
- Follow-ups worth filing:
  - publish the copy with `fieldDomains` for `category`/`status`. Whether domains are enforced on OData writes is unverified.
  - add a per-route `[RateLimit]` on feature writes in honua-server.

## Local rehearsal

Run 2026-10-24 against the local image
`honua-server:certification-jit-local-native-download-20261024` (git sha
3db6308) with `postgis/postgis:16-3.4` and `redis:7-alpine` on a private
Docker network.
- The setup used `Licensing__Mode=Disabled` and `Cors__AllowedOrigins__0=http://127.0.0.1:8791` for the page check.
- A `public.maui_inspections` typed table (with the CHECK constraints) was seeded from the seed file at ids 9000001+. A connection was then created with `POST /api/v1/admin/connections`.

Results:
1. Publishing the table `storageMode: source` with `allowAnonymousWrite: true` reproduced the demo exactly. Anonymous OData PATCH returned `501 ProviderWriteNotSupported`.
2. Enable steps 1 and 2 above: `201`, `storageMode: managed`, then `200`. `GET /odata/Layers` listed `maui-inspections-scratch` with 27 rows.
3. Anonymous writes on the copy:
   - PATCH → `200` (with `@odata.etag`)
   - POST → `201` (ObjectId returned)
   - DELETE → `204`
   - The source table was unchanged.
4. `reset-inspections-scratch.sh`:
   - The dry run planned keep 25 / patch 1 / delete 2 / post 1.
   - `--apply` returned PATCH 200, DELETE 204 ×2 and POST 201, then `verify: 27 rows, seed state`.
   - A second `--apply` reported `already in seed state`.
5. The honua-site page with `?apiBase=http://localhost:18388` resolved the scratch layer. Save sent `PATCH …/Layers(2)/Features(29)` → 200, then `GET` re-read → 200. The card showed "saved on the server", and the chip read `writes: live (OData CRUD)`.
6. First revert above: anonymous PATCH → `401 Unauthorized`, while anonymous reads stayed 200. Second revert with `?serviceName=` removed the layer from `/odata/Layers`.

To repeat it:
- boot any 2026.1 image the same way, or use honua-samples' compose stack, and give it a CORS origin;
- create the table and connection;
- run this runbook with `BASE=http://localhost:<port>` and `HONUA_DEMO_API_KEY=<HONUA_ADMIN_PASSWORD>`.
