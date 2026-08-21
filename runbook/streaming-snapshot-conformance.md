# Streaming snapshot + controlled-conformance enablement on demo.honua.io

Operator procedure for making `demo.honua.io` serve baseline snapshot
subscriptions and lease controlled-conformance runs, so the scheduled SDK
live-conformance lane can retain `executed` evidence.

Tracking:

- honua-io/honua-server#3181 — snapshot subscriptions return HTTP 500 on demo
  (acceptance is behavioural on this deployment)
- honua-io/honua-server#3206 — the server-side byte budget
- honua-io/honua-server#3038 — the controlled-conformance mutation workflow
- honua-io/honua-demo-infra#67 — the buffered ~6 MB gateway response, which is
  the underlying cause and is broader than streaming
- honua-io/honua-sdk-js#818 — the consuming evidence lane

Everything here is **[OPERATOR]** work against the live account. The Terraform
side (`stacks/aws/streaming.tf`) is already in this repo; applying it and
provisioning the source are not.

---

## 1. What is wrong today, and what it is not

Re-verified against the live deployment on 2026-08-17:

```
GET /api/v1/streaming/features?serviceId=maui-parcels&layers=1&mode=snapshot
    Accept: text/event-stream
  -> HTTP 500, 35 bytes, {"message":"Internal Server Error"}

... &mode=snapshot-then-delta
  -> HTTP 500, 35 bytes, {"message":"Internal Server Error"}

... &mode=delta
  -> 0 bytes delivered in 45 s (the invoke buffers; nothing reaches the client)
```

**This is not a server defect.** Three independent facts decide it:

1. **The deployed build predates the fix.** The capability document advertises
   `deploymentRevision: 6ad71ac701ca709ec671afd09257217e8d17a149`
   (honua-server, 2026-08-03). `git merge-base --is-ancestor` puts *neither*
   `7dc233bdc` (`fix(streaming): bound snapshot baseline delivery`, 2026-08-13)
   nor `61b7038e1` (`feat(streaming): controlled-conformance mutation
   workflow`, 2026-08-05) in it. Consistently, the deployed capability document
   has no `maxSnapshotBytes` key — which current server trunk emits — so this
   deployment is running with no snapshot payload budget at all, and
   `/api/v1/streaming/conformance/runs` 404s because those routes do not exist
   in the deployed image.

2. **The untyped 500 is the gateway's, not the server's.** It is not
   streaming-specific — a plain non-streaming FeatureServer query reproduces it
   with no streaming code in the path, purely on encoded response size:

   | request | response |
   |---|---|
   | `/rest/services/maui-parcels/FeatureServer/1/query?…&resultRecordCount=7000` | HTTP 200, 5,183,111 bytes |
   | `…&resultRecordCount=9000` | HTTP 500, 35 bytes, `{"message":"Internal Server Error"}` |

   The cutoff sits at the ~6 MB buffered invoke-response limit and the body is
   byte-identical to the streaming failure.

3. **The server's own typed-error path still works through that hop.** On the
   same route, `layers=999` returns a proper RFC 7807 problem document
   (`"Layer 999 is not part of service 'maui-parcels'."`, HTTP 400) with the
   `Server: Kestrel` header. A gateway-manufactured 500 carries
   `apigw-requestid` / `x-cache: Error from cloudfront` and no `Server` header —
   that header pair is the fastest way to tell the two apart when triaging.

> **Triage rule.** Before filing a demo 500 as a server bug, check for
> `Server: Kestrel` and compare the response size against the ~6 MB ceiling.
> A 500 without that header was never emitted by Honua, and the server log for
> the same request will say `responded 200`.

---

## 2. Redeploy onto a build that carries the fixes

Prerequisite for everything below. The image must contain honua-server
`7dc233bdc` (#3206) and `61b7038e1` (#3038).

Follow the existing image-redeploy procedure in
`demo-honua-io-capability-runbook.md` (mirror GHCR → account ECR with a
SHA-explicit tag, bump `honua_image` in `stacks/aws/terraform.tfvars`, apply,
and repoint the `live` alias).

**Pair it with the out-of-band DbUp run** if the new image carries new
`Honua.Server.Migrations` scripts — a versioned deploy does not migrate. See
the "As-applied update" notes in the capability runbook and
`db-recovery-and-migrations-092-105.md`.

Verify the redeploy landed:

```bash
curl -s https://demo.honua.io/api/v1/streaming/features/capabilities \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d["deploymentRevision"], d.get("maxSnapshotBytes"), bool(d.get("conformance")))'
```

Expect a revision at or after `7dc233bdc`, a non-null `maxSnapshotBytes`
matching `streaming_max_snapshot_bytes`, and a `conformance` block.

---

## 3. Apply the snapshot payload budget

`stacks/aws/streaming.tf` sets `FeatureStreaming__MaxSnapshotBytes` explicitly
(default 2 MiB via `streaming_max_snapshot_bytes`). Do **not** fall back to the
server's 4 MiB default here:

- the budget bounds **one baseline, not the response**. In
  `snapshot-then-delta` the delta frames keep appending to the same buffered
  response body, so a 4 MiB baseline plus enough deltas crosses the ~6 MB
  ceiling and reproduces the identical untyped 500;
- 2 MiB leaves roughly 4 MB of headroom for the delta stream and SSE framing.

After apply, confirm a large layer now serves a bounded baseline rather than a
gateway 500:

```bash
curl -sS -D- -o /tmp/snapshot.sse -m 60 \
  -H 'Accept: text/event-stream' \
  'https://demo.honua.io/api/v1/streaming/features?serviceId=maui-parcels&layers=1&mode=snapshot' \
  | grep -Ei '^(HTTP/|server:)'
grep -c '^event: snapshot-begin' /tmp/snapshot.sse
grep -c '^event: snapshot-end'   /tmp/snapshot.sse
```

Expect `HTTP/2 200`, `server: Kestrel`, and one of each envelope frame. A
large layer's baseline will be truncated (`"complete": false`) followed by a
terminating `status: error` frame naming the bound it hit — that is the
intended fail-closed contract, not a failure.

---

## 4. Provision the dedicated conformance source

`FeatureStreaming__Conformance__*` stays off until a **dedicated** source
exists. Two constraints decide whether the source can work at all:

1. **It must be a small layer.** A baseline that hits any bound is truncated,
   reported `complete: false`, and **ends the stream** — so a subscription on a
   large layer can never observe the correlated mutation afterwards, however
   the caps are tuned. On the current seed:

   | layer | rows | baseline |
   |---|---:|---|
   | `maui-parcels` | 51,245 | ~6.6 MB — truncated |
   | `maui-buildings` | 42,674 | truncated at the 5000-feature cap |
   | `maui-inspections` | 27 | completes |

2. **It must not be a layer the demo page serves.** Controlled runs insert and
   delete records in the configured layer. Provision a separate small layer
   (for example a `streaming-conformance` service seeded with a handful of
   points) rather than borrowing `maui-inspections`, so a swept or failed run
   can never perturb a demo surface. Size it like `maui-inspections`.

The layer must carry the run-ownership columns the server writes:
`conformance_run_id` (and `conformance_label` when labels are used). Seed it
through the in-VPC bootstrap Lambda the same way the other demo layers are
seeded (see `stacks/aws/SEED_MANIFEST.md`).

Then set, in `stacks/aws/terraform.tfvars`:

```hcl
streaming_conformance_enabled    = true
streaming_conformance_service_id = "streaming-conformance"
streaming_conformance_layer_id   = <layer id>
```

The variable validations fail the plan if the service id is empty or the layer
id is unset while the flag is on — a typo must not silently resolve to a shared
demo service.

---

## 5. Issue the conformance credential

**Owner decision.** The mutating routes are gated by the `ConformanceMutate`
authorization policy, which is configured as an **admin** policy. Enabling the
surface therefore also means issuing a credential to the SDK evidence lane
(honua-io/honua-sdk-js#818) that satisfies an admin-scoped policy on the public
demo. Decide deliberately whether that credential is acceptable on this
deployment, and store it as a repository secret on the consuming lane the same
way `HONUA_CLIENT_COMPAT_API_KEY` is handled for the client-compat canary — not
in Terraform.

Verify the surface is live:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  https://demo.honua.io/api/v1/streaming/conformance/runs
```

Expect `401`/`403` (route exists, caller unauthenticated) rather than `404`
(route absent — the deployed image predates #3038).

---

## 6. Known remaining blocker: buffered responses

Even after all of the above, an SSE subscription that stays open delivers
nothing to the client until the invoke ends, because the deployment buffers the
response. `mode=delta` returned 0 bytes in a 45 s read on 2026-08-17. That is
honua-io/honua-demo-infra#67 REQ-003 — whether demo keeps the buffered Lambda
path at all, or moves to a streaming-capable response path (a function URL with
response streaming, or a container target).

The steps above make snapshot delivery correct and bounded. Retaining
`executed` evidence for a *live* mutation observed on an open subscription also
needs #67.
