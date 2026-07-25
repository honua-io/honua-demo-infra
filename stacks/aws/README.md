# aws-demo — Phase A: demo.honua.io

> **Extracted from honua-io/honua-iac** (honua-iac#126, 2026-07). This root
> module used to live at `infrastructure/terraform/examples/aws-demo` in
> honua-iac; it is the live `demo.honua.io` environment, not a sample, so it
> now lives here in its own private repo. The `module "honua"` source in
> `main.tf` is pinned to a git ref of honua-iac
> (`git::https://github.com/honua-io/honua-iac.git//infrastructure/terraform/modules/aws-serverless?ref=<sha>`)
> instead of the old relative `../../modules/aws-serverless` path. **honua-iac
> is private**, so `terraform init` needs git credentials that can fetch it
> (e.g. a `gh auth login` HTTPS credential helper, or a machine token with
> `repo` scope) — see the repo-root README for details. Everything below is
> otherwise unchanged from the original example doc.

Terraform root config that deploys the **Phase A** hosted demo environment for
[demo.honua.io](https://demo.honua.io) using the `aws-serverless` module
(Lambda container + API Gateway HTTP API + RDS PostgreSQL), fronted by a
CloudFront distribution that caches tile traffic at the edge (`cloudfront.tf`).

## Why serverless for the demo

- **Scales to zero between visitors** — no idle Fargate task cost; Lambda billing
  is request-duration based.
- **Demonstrates the story** — the demo itself runs on Honua's serverless
  deployment path, so visitors can see the same architecture they'd deploy.
- **Cold starts are acceptable** — the AOT Lambda image (`vX.Y.Z-lambda-aot`)
  compiles to native code and typically cold-starts in 200–400 ms, which is
  imperceptible for a read-mostly API demo.
- **Lower operational overhead** — no ECS cluster, no ALB, no NAT gateway
  data-transfer bill for an always-on task.

## Scope

**Phase A** — a single shared, read-mostly demo instance backed by curated
public datasets. Anyone can browse the API and STAC catalog without an account.

**Phase B** (future work, not in this config) — self-service sandbox tenants on
`*.demo.honua.io`. That is a separate project and is **not** the same as
honua-server issue #346 (schema-per-tenant Enterprise multi-tenancy), which is
a data-isolation feature for production deployments.

## What this provisions

| Resource | Sizing / configuration |
|---|---|
| CloudFront distribution | Tile caching at the edge — see "CDN layer" below |
| Lambda function | `x86_64`, 1024 MiB RAM, no provisioned concurrency (arm64 blocked on cross-build, see main.tf) |
| Lambda image | `*-lambda-aot` tag (AOT build); cold starts ~200–400 ms |
| RDS PostgreSQL | `db.t4g.micro`, version 15, 20 GB gp3, PostGIS + PostGIS Raster enabled |
| ElastiCache | Off by default; `enable_redis = true` provisions `cache.t3.micro` in-VPC for the Production feature-change event store (see "Pro + AI demo drift") |
| API Gateway | HTTP API (`protocol_type = "HTTP"`) with `$default` stage |
| ACM certificate | Auto-provisioned and DNS-validated for `demo.honua.io` |
| Route53 A/AAAA records | `demo.honua.io` → CloudFront distribution (alias; → API Gateway custom domain and no AAAA while `route_demo_dns_to_cloudfront=false`) |
| API Gateway custom domain | `demo.honua.io`, TLS 1.2, regional endpoint |
| VPC | New VPC, **no managed NAT gateway** — a fck-nat t4g.nano instance provides egress (see below) |
| NAT | fck-nat instance (`nat-instance.tf`): t4g.nano + EIP in one public subnet, default route for the private subnets (~$7.5/mo all-in; replaced ~$110/mo of interface-endpoint ENIs, 2026-07-24) |
| VPC endpoints | Free S3 gateway endpoint only (keeps tile/import S3 bytes off the NAT). All interface endpoints removed — see "fck-nat NAT instance" below |
| Budget | `aws_budgets_budget` monthly cost alarm (`cost-controls.tf`): alerts at 100%/200% of $150, actual + forecasted, to the ops contact |
| PostGIS bootstrap | One-shot in-VPC Lambda enables `postgis` + `postgis_raster` during apply |
| CloudWatch Logs | 90-day retention for Lambda and API Gateway |
| Secrets Manager | DB connection string, admin password, master key |

### CDN layer — CloudFront tile caching

`cloudfront.tf` puts a CloudFront distribution in front of the API Gateway
endpoint and repoints the `demo.honua.io` A/AAAA alias at it. Path-scoped
behaviors split the traffic:

| Routes | Behavior |
|---|---|
| `/api/v1/tiles/*` (PMTiles range proxy), `/ogc/tiles/*`, `/rest/services/*/MapServer/tile/*`, `/rest/services/*/ImageServer/tile/*`, `/fonts/*`, `/terrain/*` | Cached 24 h **enforced via `min_ttl`** (range requests cached natively); edge CORS response-headers policy; viewer `Cache-Control: public, max-age=86400, stale-while-revalidate=86400` |
| Everything else (queries, OData, admin, `/healthz`) | Pass-through, no caching, all headers/query strings forwarded |

Details that matter when changing this:

- **`min_ttl = 86400` is load-bearing** (2026-06-12): honua-server stamps
  `Cache-Control: public, max-age=3600` on tile responses, and with
  `min_ttl = 0` that origin header silently capped the edge cache at **1
  hour** — every hour each tile went cache-cold again, and a county-wide pan
  fanned out into Lambda scale-out + database connection bursts (the 48 h
  metrics autopsy showed 53300 connection-slot exhaustion behind exactly this
  pattern). `min_ttl >= default_ttl` makes CloudFront keep tile bytes the
  full 24 h regardless of the origin's `max-age`. Error responses are not
  pinned: 4xx/5xx follow the error-caching TTLs (10 s for 5xx via
  `custom_error_response`).
- **Viewer caching + stale-while-revalidate**: the response-headers policy
  overrides the viewer-facing `Cache-Control` on the cached behaviors to
  `public, max-age=86400, stale-while-revalidate=86400`, so returning
  visitors render tiles from the browser cache and revalidate in the
  background instead of re-fetching the whole viewport every hour.
- **Origin Shield (us-west-2)** is enabled on the API Gateway origin: the
  regional edge caches collapse their misses onto one shield cache in the
  origin's own region, so a cache-cold tile is rendered **once**, not once
  per POP — directly shrinking the Lambda scale-out (and its database
  connection demand) behind a burst.

- **Origin** is the regional `execute-api` endpoint, *not* `demo.honua.io`
  (that record now points at CloudFront — using it as origin would loop) and
  *not* the API Gateway custom-domain target (it only serves the
  `demo.honua.io` cert, which fails CloudFront's origin TLS validation).
  The `Host` header is deliberately **not** forwarded to the origin
  (API Gateway routes by Host); `Managed-AllViewerExceptHostHeader` forwards
  the rest, including `Range` and `Origin`.
- **Edge CORS**: a response-headers policy stamps the demo's CORS headers
  (origins `https://honua.io`, `https://www.honua.io`, `http://localhost:8123`
  — mirror of `Cors__AllowedOrigins__*` in main.tf) on the cached behaviors,
  because cached responses are shared across requesting origins. This also
  masks honua-server#1627 (missing CORS headers on error responses) for the
  tile routes.
- **Viewer cert** lives in **us-east-1** (CloudFront requirement, hence the
  `aws.us_east_1` provider alias). It validates against the same Route53
  CNAME the regional cert already created — ACM emits identical validation
  records for the same domain in every region.
- **DNS swap sequencing** for a fresh distribution: apply with
  `-var=route_demo_dns_to_cloudfront=false`, validate via the
  `cloudfront_domain_name` output (tile 200 + `X-Cache: Hit from cloudfront`
  on the second hit, CORS headers present, `/healthz/live` 200 pass-through),
  then apply with the default (`true`) to repoint `demo.honua.io`.
- **HTTP/2 + HTTP/3**, compression on, `PriceClass_100` (NA + EU edges).
- Transient origin 5xx are only cached for 10 s (`custom_error_response`)
  so a Lambda cold-start hiccup doesn't pin an error tile for 5 minutes.

**Invalidation after reseeds** — tiles are cached for 24 h, so after
re-uploading PMTiles archives or reimporting datasets, flush the tile paths:

```bash
aws cloudfront create-invalidation \
  --distribution-id "$(terraform output -raw cloudfront_distribution_id)" \
  --paths "/api/v1/tiles/*" "/ogc/tiles/*" "/rest/services/*" \
          "/fonts/*" "/terrain/*"
```

(Or invalidate just the reseeded prefix, e.g.
`/api/v1/tiles/pmtiles/maui-basemap*`. The first 1,000 invalidation paths
per month are free; a wildcard counts as one path.)

**Cost**: effectively free at demo traffic — no monthly base fee; pay
per-request/per-GB only (~$0.085/GB + $0.0075–0.01 per 10k HTTPS requests in
PriceClass_100, well under a dollar a month at current volumes), and cached
tile hits *reduce* Lambda/API Gateway/RDS load.

### fck-nat NAT instance — interface endpoints removed (2026-07-24)

The original no-NAT design reached every AWS service through interface VPC
endpoints — which quietly became the dominant fixed cost: the module's
deploy-control endpoints (lambda/sts/monitoring/logs across 3 AZs, ~$88/mo)
plus the demo's secretsmanager/bedrock-runtime/geo.places single-AZ
endpoints (~$22/mo). A single **fck-nat** instance (`nat-instance.tf`,
https://fck-nat.dev — purpose-built minimal NAT AMI) replaces all of them:

- **t4g.nano + EIP** in one public subnet, `source_dest_check` off, default
  route (`0.0.0.0/0`) from the private route tables through its ENI.
  ~$7.5/mo all-in (~$3.1 instance + ~$0.7 EBS + ~$3.7 public IPv4).
- **All interface endpoints removed**: the demo's in `vpc-endpoints.tf`
  (only the free S3 gateway endpoint remains — it also keeps PMTiles/import
  S3 bytes off the NAT) and the module's via
  `enable_deploy_control_vpc_endpoints = false` in `main.tf`.
- **Bonus**: the VPC now has true internet egress, so previously-unreachable
  paths (Nominatim, OIDC, webhooks) are network-possible again.
- **Accepted SPOF**: one instance, one AZ. If it dies, private-subnet egress
  is down (the public CloudFront → API Gateway → Lambda invoke path stays
  up, but Secrets Manager / Bedrock / Amazon Location / deploy-control calls
  fail) until `terraform apply -replace=aws_instance.nat` (~2 min).
  Acceptable for a demo; a production stack would run fck-nat in an ASG or a
  managed NAT gateway per AZ. See the runbook ("Cost & network
  architecture") for recreate/resize steps.

If the demo ever outgrows the instance, bump `nat_instance_type`, or set
`enable_nat_gateway = true` in `main.tf` (managed NAT, ~$33/mo + data) and
remove `nat-instance.tf`.

### Geocoding on Amazon Location Service (honua-server#2948)

> Unlike the "Pro + AI demo drift" toggles below, this one is **not** live —
> it is a plan awaiting operator approval, not already-applied state to
> adopt/import.

Off by default (`enable_amazon_location_geocoding = false`). The demo's
geocoding provider defaults to Nominatim (OpenStreetMap) purely because
that's the server's compiled-in default (`GeocodingConfiguration.DefaultProvider
= "nominatim"`, `LocatorName = "World"`) — no env var currently sets either on
`demo.honua.io`. Nominatim needs general internet egress, which this VPC does
not have (see "No NAT gateway" above): every forward/reverse geocode call
fails after a consistent ~15.8s outbound-connect timeout, first call or
hundredth alike — a categorical network-reachability problem, not a
cold-start one, and a keep-warm probe would not fix it.

Setting `enable_amazon_location_geocoding = true`:

- Provisions an **Amazon Location place index** (`aws_location_place_index`,
  data source `Esri` by default) via the `aws-serverless` module.
- Grants the Lambda role a least-privilege policy scoped to that index:
  `geo:SearchPlaceIndexForText`, `geo:SearchPlaceIndexForPosition`,
  `geo:SearchPlaceIndexForSuggestions`, `geo:DescribePlaceIndex` (the latter
  two beyond forward/reverse geocode because the server's provider class
  already calls them for its `suggest` operation and health check).
- Injects `Geocoding__DefaultProvider=amazon-location` +
  `Geocoding__Providers__AmazonLocation__*` and explicitly
  `Geocoding__Providers__Nominatim__Enabled=false` (see the module README for
  why: Nominatim's own default is `Enabled=true`, and failover would
  otherwise retry it — and hang another ~15.8s — on any Amazon Location
  error).
- Amazon Location is reached through the fck-nat egress (`nat-instance.tf`);
  the dedicated `geo.places` interface endpoint this originally provisioned
  was removed in the 2026-07-24 cost round.

**Data source note**: results come from Esri (or HERE if
`amazon_location_data_source = "Here"`) — not OpenStreetMap. This is a full
provider swap: coverage, address formatting, and attribution differ from
Nominatim.

**Cost**: the place index has no idle charge — cost is per API call under
Amazon Location's Esri/HERE pricing tier, low single dollars/month at demo
traffic volumes. No dedicated endpoint or compute (traffic rides the shared
fck-nat instance).

**This has not been applied.** See the root repo's demo runbook
(`honua-server` `docs/internal/demo/demo-honua-io-capability-runbook.md`) for
the full operator-approval remediation plan this PR is one part of, including
the exact `terraform plan`/`apply` sequence and rollback (repoint
`Geocoding__DefaultProvider` back to `nominatim` and destroy the place index +
endpoint — additive, no coupling to the other tracks).

### Studio AI proxy on Bedrock (`enable_studio_ai`) — not yet applied

> Like the Amazon Location toggle above, this one is **not** live — it is new
> wiring awaiting operator approval, not already-applied state to adopt or
> import. Tracks issue #9 (REQ-002: Studio AI proxy configured with at least
> one provider; keys server-side only) for the Studio pilot rehearsal.

Off by default (`enable_studio_ai = false`). honua-server#3000's Studio AI
proxy (`src/Honua.Ai/Features/StudioAiProxy`) powers live AI generation in
Honua Studio; its `bedrock` adapter kind authenticates via the AWS credential
chain and targets the regional bedrock-runtime endpoint derived from the
provider's `Region` — no endpoint URL, no API key, so nothing secret enters
Terraform, tfvars, or state.

Setting `enable_studio_ai = true` (`studio-ai.tf`):

- Grants the Lambda execution role a least-privilege inline policy
  (`bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream` — the
  Converse/ConverseStream APIs authorize against these same actions) scoped to
  `studio_ai_model`'s **and** `studio_ai_fallback_models`' inference-profile
  ARNs (calling region, account-scoped) **plus** each model's underlying
  foundation-model ARNs in every `us.` member region
  (us-east-1/us-east-2/us-west-2) — both halves are required or the routed
  invocation gets AccessDenied. Covering the fallback models up front means an
  operator can flip `studio_ai_model` between them with a plain env change and
  no IAM edit. Separate from the module's WorkflowGeneration grant so the two
  AI add-ons toggle/model independently.
- Injects the exact server config keys (`StudioAiProxy` section, ASP.NET Core
  double-underscore form): `StudioAiProxy__Enabled=true`,
  `StudioAiProxy__DefaultProvider=bedrock`,
  `StudioAiProxy__Providers__bedrock__Kind=bedrock`,
  `StudioAiProxy__Providers__bedrock__Model=<studio_ai_model>`,
  `StudioAiProxy__Providers__bedrock__Region=<studio_ai_region>`.
- Network path: Bedrock is reached through the fck-nat egress
  (`nat-instance.tf`) — no dedicated endpoint. (The bedrock-runtime
  interface endpoint this originally shared with `enable_bedrock_ai` was
  removed in the 2026-07-24 cost round.)

**Model**: defaults to the cross-region Claude Opus 5 inference profile
`us.anthropic.claude-opus-5` (profile ACTIVE in this account; the
foundation-model agreement for `anthropic.claude-opus-5` was accepted
2026-07-24, and runtime entitlement can lag the agreement — smoke-test before
a rehearsal). The IAM grant also covers `studio_ai_fallback_models`, default
`us.anthropic.claude-sonnet-4-6` — verified invocable end-to-end on
2026-07-24 (from us-east-1 and from us-west-2, the region the demo Lambda
actually calls) — so flipping `studio_ai_model` to it is env-only.
`us.anthropic.claude-haiku-4-5-20251001-v1:0` also invokes in this account.
Validation smoke commands live in the repo runbook
(`runbook/demo-honua-io-capability-runbook.md` → "Studio AI (Bedrock BYOM)").

**Region**: keep `studio_ai_region` equal to `region` (the VPC's region) so
invocations stay region-local through the NAT egress.

### Pro + AI demo drift (Pro license, Bedrock AI, Redis)

> Tracks iac issue **#58** (AWS Bedrock InvokeModel IAM for the demo Lambda)
> and the broader Pro+AI demo delivery drift. These three add-ons power the
> Pro+AI demo beat and are **all gated off by default** so a stock `aws-demo`
> apply is unchanged. The live `demo.honua.io` environment runs them **ON**,
> applied out-of-band via the AWS CLI. The Terraform here codifies that drift;
> because the Bedrock and Redis resources already exist live, **adopt them with
> `terraform import` (below) before any plan/apply** — do not let Terraform
> recreate them. The Pro license secret is the exception: it is adopted **by
> ARN**, needs no import, and is never owned by Terraform (see
> "Pro licensing (adopt-by-ARN)").

**No secret values live in Terraform.** The Pro license envelope is staged
out-of-band directly in Secrets Manager and adopted **by ARN**
(`pro_license_secret_arn`): Terraform is handed the secret's *ARN*, never its
*value*, so the envelope cannot land in `terraform.tfvars`, in a plan file, or
in state. The license reaches the Lambda by *reference*
(`Licensing__LicenseContentSecretRef = aws:secretsmanager:<arn>`), never inline.
The trusted **public** key is a plain default in `variables.tf` — it verifies a
signature and cannot mint a license, so it is not secret. Enabling Pro is
therefore just `enable_pro_license = true`, with **no secret material on any
workstation and no `terraform import` step**.

| Toggle | What it adds | Live values (from the deploy record) |
|---|---|---|
| `enable_pro_license` | `secretsmanager:GetSecretValue` for the Lambda role on the **externally-managed** license secret; injects `Licensing__LicenseContentSecretRef` + `Licensing__TrustedKeys__honuademo2026q2`. Creates **no** secret and **no** secret version. | secret `honua-demo-demo/license-pro` (ARN default in `variables.tf`); keyId `honuademo2026q2` |
| `enable_bedrock_ai` | least-privilege `bedrock:InvokeModel` (+ `…WithResponseStream`) on the Lambda role scoped to the Claude model's inference-profile + foundation-model ARNs; `WorkflowGeneration__*` env (provider=bedrock, region=us-west-2). Bedrock rides the fck-nat egress — the dedicated `bedrock-runtime` endpoint (live `vpce-003090af73dc835fe`, SG `sg-0ac55474b410c5d34`) was removed from config in the 2026-07-24 cost round; if it is in state the next apply destroys it, and if it is NOT in state it must be deleted by hand or it keeps billing (~$7.5/mo) as an orphan | model `us.anthropic.claude-sonnet-4-5-20250929-v1:0`; region `us-west-2` |
| `enable_redis` | in-VPC ElastiCache Redis (`cache.t3.micro`, port 6379); `ConnectionStrings__redis`; the Lambda 6379 egress rule | cluster `honua-demo-redis`, SG `sg-0454e3341c5de3068` |

#### The CIDR-egress gotcha (important)

When `enable_redis = true`, the `aws-serverless` module adds the Lambda's
6379 egress rule **as a CIDR rule pointed at the VPC CIDR (`10.0.0.0/16`)**,
not as a security-group-reference rule. This is deliberate: in the demo VPC a
security-group-*reference* egress rule (egress → the Redis SG) did **not**
work — the Lambda could not reach Redis until the rule was rewritten as a
CIDR rule. The module already does the right thing (`redis_egress_cidrs =
[vpc_cidr_block]` when it creates the cluster), so nothing extra is needed
here — but do not "tidy" it into a SG-reference rule.

#### Import the already-live resources (do this BEFORE plan/apply)

The demo's remote tfstate **is live and readable**: `versions.tf` carries an
active `backend "s3"` block (bucket `honua-tfstate-585192672263`, key
`demo/aws-demo/terraform.tfstate`, region `us-east-1` — the BUCKET's region,
deliberately different from the demo's `us-west-2`), with S3-native locking via
`use_lockfile`. `terraform init` + `terraform plan` against live work from this
checkout. (Earlier revisions of this README claimed state was inaccessible
because the backend was commented out; that has not been true since #122.)

Run the imports below so Terraform adopts the existing resources instead of
trying to create duplicates. Run from this directory (`stacks/aws` in
honua-demo) with the toggles set in `terraform.tfvars`:

```bash
# --- Pro license secret — NO IMPORT REQUIRED ------------------------------
# The license secret is deliberately NOT managed by Terraform. The example
# passes pro_license_secret_arn, so the module creates no secret and no secret
# version for it and never reads the envelope — it only injects
# Licensing__LicenseContentSecretRef and grants the Lambda role
# GetSecretValue on that ARN. Nothing to import; nothing to keep in sync.
# See "Pro licensing (adopt-by-ARN)" below.

# --- Bedrock runtime VPC endpoint + its SG — OBSOLETE (2026-07-24) ---------
# The endpoint was removed from config in the fck-nat cost round; there is no
# longer a resource address to import to. If the live endpoint
# vpce-003090af73dc835fe / SG sg-0ac55474b410c5d34 are still in state, the
# next apply destroys them (intended); if they were never imported, delete
# them by hand (aws ec2 delete-vpc-endpoints / delete-security-group) or they
# keep billing as orphans.

# --- Redis (item 4) — replication group, subnet group, SG, secret ----------
terraform import 'module.honua.aws_elasticache_replication_group.redis[0]' honua-demo-redis
terraform import 'module.honua.aws_elasticache_subnet_group.redis[0]'      honua-demo-demo-redis
terraform import 'module.honua.aws_security_group.redis[0]'                sg-0454e3341c5de3068
terraform import 'module.honua.aws_secretsmanager_secret.redis_connection[0]' honua-demo-demo/redis-connection
```

Notes:

- **Lambda env vars + the IAM inline policies are not separately importable** —
  they are attributes of resources Terraform already manages (the Lambda
  function's `environment`, the role's inline `bedrock`/`secrets` policies).
  Once the toggles are on and the imports above are in state, a `plan` should
  show those as in-place updates (env keys merged, policy statements added),
  which an operator reviews before applying. Expect the `random_password`
  resources for the Redis auth token to want to generate on first apply if a
  Redis cluster is imported that already has an auth token — supply the live
  token via `redis_auth_token`/`redis_connection_string` (module variables) if
  drift on the auth token must be avoided.
- The module names the ElastiCache subnet group/replication group/SG from
  `${name_prefix}-${environment}` (`honua-demo-demo-*`); the live cluster id in
  the deploy record is `honua-demo-redis`. If the live names differ from what
  the module would generate, import maps the live id into the module address
  regardless — verify the `plan` shows no rename/replace after import.

#### Pro licensing (adopt-by-ARN)

The signed Pro license envelope is **managed entirely outside Terraform**. It is
already staged in the demo account as `honua-demo-demo/license-pro`, and the
example passes its ARN to the module via `pro_license_secret_arn`. The module
therefore creates **no** `aws_secretsmanager_secret` and **no**
`aws_secretsmanager_secret_version` for the license: it only injects the
reference and grants the Lambda role `GetSecretValue` on that exact ARN.

Why adopt-by-ARN rather than `terraform import`:

- **The envelope never reaches Terraform.** Import would put the secret in state
  and still require `pro_license_content` in a `terraform.tfvars` to stop a
  future apply rewriting the version. Adopt-by-ARN needs no tfvars at all — the
  ARN is not sensitive.
- **Terraform cannot destroy the envelope.** The secret is not in state, so
  `enable_pro_license = false` (or a `destroy`) cannot delete the trust asset.
  An imported secret would be deleted by either.
- **No prerequisite state surgery.** Enabling Pro is a plain `plan`/`apply`, so
  the plan can be reviewed end-to-end before anything touches live.

To enable Pro, set `enable_pro_license = true` in `terraform.tfvars`. Everything
else defaults correctly in `variables.tf`:

| Variable | Default | Secret? |
|---|---|---|
| `pro_license_secret_arn` | live `honua-demo-demo/license-pro` ARN | No — an ARN, not a value |
| `pro_license_key_id` | `honuademo2026q2` | No |
| `pro_license_trusted_public_key` | `base64url:Y2Xg…` | **No** — a public key verifies a signature; it cannot mint a license |

The `keyId` is hyphen-free **deliberately**: it becomes the env-var name segment
`Licensing__TrustedKeys__honuademo2026q2`, and hyphens are illegal there. It must
match the staged envelope's `keyId` **exactly** — `FileBackedLicenseService` does
`TrustedKeys.TryGetValue(envelope.KeyId, …)`, and a miss means the server logs a
warning and **silently serves Community**. Do not "restore" the hyphens.

The private signing seed lives in `honua-demo-demo/license-signing-key`. It is the
trust root for all Honua licensing: never read, print, or copy it. This config
does not need it and must never reference it.

Verify Pro actually activated after an apply (the failure mode is silent):

```bash
# Expect EventId 10014 "License loaded from secret reference."
# 10010-10013 are the silent-fallback-to-Community failures.
aws logs filter-log-events --region us-west-2 \
  --log-group-name /aws/lambda/honua-demo-demo-honua \
  --start-time "$(($(date +%s) - 900))000" \
  --filter-pattern '"License"' --query 'events[].message' --output text
```

### Database bootstrap and migrations

The RDS instance is in private subnets with no inbound path (the fck-nat
instance is egress-only — not a bastion — and there is no VPN), so nothing
outside the VPC can reach it — including the module's `enable_postgis` local-exec
(psql) path, which this config disables. Instead:

1. **PostGIS** — a one-shot Python Lambda inside the VPC
   (`postgis-bootstrap.tf`) enables `postgis` + `postgis_raster` as the RDS
   master user during `terraform apply`. Requires python3 + pip on the apply
   host (the pure-Python `pg8000` driver is vendored into the zip at apply
   time).
2. **Schema migrations** — the module call sets `skip_migrations = false`, so
   Honua runs its own DbUp migrations on startup (first invoke). After the
   first successful boot this can be flipped back to `true` (the serverless
   default) so cold starts skip the migration journal check. The module call
   also raises `lambda_timeout_seconds` to 60 so a slow first-boot migration
   run can finish even though API Gateway stops waiting at 30 s.

### Rate limiting (no WAF)

API Gateway HTTP API does **not** support WAFv2 association (WAF v2 attaches to
REST APIs / ALBs / CloudFront only). Rate-limiting is handled at the API Gateway
stage level via throttle settings:

| Limit | Default | Variable |
|---|---|---|
| Burst (concurrent requests) | 200 | `api_throttle_burst_limit` |
| Steady-state (req/s) | 50 | `api_throttle_rate_limit` |

These defaults are conservative for a public demo. Adjust in `terraform.tfvars`
if you need higher throughput.

If WAF-level rate-limiting becomes a hard requirement in future, the easy
path is now to attach a WAFv2 CLOUDFRONT-scoped ACL to the existing
distribution in `cloudfront.tf` (`web_acl_id`); the CDN layer also absorbs
most tile-burst traffic before it ever reaches the API Gateway throttles.

### Container/function environment

| Variable | Value |
|---|---|
| `HONUA_SERVE_API_DOCS` | `true` — OpenAPI docs served at `/api-docs` |
| `HONUA_SERVE_STAC_DEMO` | `true` — STAC demo catalog enabled |
| `MultiTenancy__Enabled` | `true` |
| `MultiTenancy__DefaultTenantId` | `public` |
| `HostValidation__AllowedHosts__1` | `demo.honua.io` |

### Known limitation: /healthz/ready requires Redis in Production

honua-server hard-requires a durable distributed feature-change event store
(Redis) whenever ASPNETCORE_ENVIRONMENT is Production. With `enable_redis =
false` (the default), `/healthz/live` returns 200 and the API works normally,
but **`/healthz/ready` always returns 503** ("Feature-change event storage
unavailable"). Nothing probes readiness in the Lambda deployment path, so this
is cosmetic for the demo — but don't wire `/healthz/ready` into external uptime
checks until honua-server treats the event store as optional for single-node
deployments. Set `enable_redis = true` (the live demo does) to provision the
in-VPC ElastiCache cluster and clear the 503; see "Pro + AI demo drift" above
for the CIDR-egress gotcha and the import sequence.

## Lambda → RDS connection management

The module connects Lambda **directly to RDS** within the VPC. Lambda's
security group allows egress on port 5432 to the VPC CIDR, and the RDS
security group allows ingress from the Lambda security group.

Each Lambda execution environment runs its own Npgsql pool, so the worst-case
connection demand is a simple product that MUST stay under the instance's
slot ceiling (Postgres `max_connections` ≈ `DBInstanceClassMemory / 9531392`
on RDS — ~112 slots on db.t4g.micro, ~225 on db.t4g.small, minus
`superuser_reserved_connections` and the bootstrap/maintenance Lambda):

```
reserved_concurrent_executions × Maximum Pool Size + headroom < max_connections
```

The 48 h metrics autopsy (2026-06-12) showed what happens when this budget is
unbounded: cache-cold MVT tile bursts → Lambda scale-out → 53300 "remaining
connection slots reserved" → 33% error rate. Three levers keep it bounded,
and all three are **encoded in Terraform** so applies stop reverting
hand-edits:

1. **`lambda_reserved_concurrent_executions`** caps the number of pools.
2. **Connection-string pool tuning** (`Maximum Pool Size`, short
   `Connection Idle Lifetime` / `Connection Pruning Interval` so idle slots
   are released quickly between bursts) — built into the module's
   connection-string secret; e.g. 30 environments × pool 3 + headroom fits a
   db.t4g.micro's ~112 slots, 50 × 4 fits a db.t4g.small's ~225.
3. **Demand reduction in front of the function**: pre-baked PMTiles archives
   for every demo raster AND vector layer (tile browsing never opens a DB
   connection), 24 h pinned CloudFront TTLs, and Origin Shield (see the CDN
   section).

**RDS Proxy gotcha (2026-06-12)**: a proxy in front of RDS is the textbook
fix here, but honua-server currently cannot speak through it — the server
sends per-session settings as the Postgres `options` startup parameter
(Npgsql `Options`), which RDS Proxy rejects with `0A000: Feature not
supported: RDS Proxy currently doesn't support command-line options`, taking
**every** DB-backed route down. Do not point the connection-string secret at
an RDS Proxy endpoint until honua-server moves those settings to a
physical-connection initializer.

## DNS prerequisites

> **Action required before applying.** Read this section carefully.

The `demo.honua.io` apex (`honua.io`) currently points to GitHub Pages via A/CNAME
records at the registrar. You **cannot** use the Route53-managed ACM validation
path without one of the following two options:

### Option A — Delegated subzone (recommended)

Create a Route53 hosted zone specifically for `demo.honua.io`. Add the NS
records the hosted zone returns to your registrar **as NS records for the
`demo` subdelegation**, without touching the `honua.io` apex records.

Steps:

1. In the AWS Console (or via Terraform), create a public Route53 hosted zone
   for `demo.honua.io`. Note the four NS values.
2. At your registrar, add an NS record set for `demo.honua.io` pointing to
   those four nameservers. This does not affect the apex `honua.io` records
   (GitHub Pages continues to work).
3. Pass the hosted zone ID to this config as `route53_zone_id`.
4. Run `terraform apply`. Terraform will create the ACM certificate,
   add the DNS validation CNAME inside the `demo.honua.io` zone, wait for
   validation, and then create the alias A record.

### Option B — Full migration

Migrate the entire `honua.io` zone to Route53. Create a hosted zone for
`honua.io`, recreate all existing records (including GitHub Pages CNAME/A),
and update the registrar NS records. Then set `route53_zone_id` to the
`honua.io` zone ID and use `domain_name = "demo.honua.io"`.

**Option A is strongly preferred** because it has zero impact on the
`honua.io` apex and GitHub Pages.

## Prerequisites

- AWS account with permissions matching the IAM policy in honua-iac's
  `infrastructure/terraform/bootstrap/aws-serverless/` (or equivalent).
- Terraform >= 1.5 (< 2.0).
- `python3` + `pip` on the machine running `terraform apply` — used to vendor
  the pure-Python `pg8000` driver into the PostGIS bootstrap Lambda zip. No
  psql and **no network path to RDS** are needed: PostGIS is enabled by the
  in-VPC bootstrap Lambda (see "Database bootstrap and migrations").
- A Route53 hosted zone for `demo.honua.io` (see DNS prerequisites above).
- **ECR repository** — Lambda container images must be in ECR. Push the
  `*-lambda-aot` image from GHCR to your ECR repository before applying.
- An S3 bucket + DynamoDB table for remote state (optional but strongly
  recommended for team use — uncomment the `backend "s3"` block in `versions.tf`).

## Apply steps

```bash
# 1. Push Lambda image to ECR (Lambda cannot pull from GHCR directly)
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin 123456789012.dkr.ecr.us-west-2.amazonaws.com

docker pull ghcr.io/honua-io/honua-server:v1.5.0-lambda-aot
docker tag ghcr.io/honua-io/honua-server:v1.5.0-lambda-aot \
  123456789012.dkr.ecr.us-west-2.amazonaws.com/honua-server:v1.5.0-lambda-aot
docker push 123456789012.dkr.ecr.us-west-2.amazonaws.com/honua-server:v1.5.0-lambda-aot

# 2. Copy and fill in tfvars
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 3. Initialise (with remote backend enabled, pass -backend-config or env vars)
terraform init

# 4. Review
terraform plan -out=demo.tfplan

# 5. Apply
terraform apply demo.tfplan
```

After apply, validate:

```bash
# Raw API Gateway endpoint (no custom domain required)
API_URL=$(terraform output -raw api_gateway_endpoint)
curl -f "$API_URL/healthz/ready"

# Custom domain (once DNS propagates)
curl -f https://demo.honua.io/healthz/ready

# Confirm PostGIS extensions (reported by the in-VPC bootstrap Lambda)
terraform output postgis_bootstrap_result
```

## Estimated monthly cost (us-west-2, July 2026 — post fck-nat cost round)

| Component | Estimate |
|---|---|
| RDS db.t4g.micro, single-AZ (downsized back from small 2026-07-24, paired with reserved concurrency 25) | ~$12 |
| RDS storage, 20 GB gp3 | ~$2.50 |
| fck-nat instance (t4g.nano + gp3 root + public IPv4) | ~$7.50 |
| S3 gateway endpoint | $0 (free) |
| Interface VPC endpoints | $0 (**all removed** 2026-07-24 — was ~$110/mo: 12 module deploy-control ENIs + 3 demo single-AZ ENIs) |
| Lambda (requests + duration, demo traffic) | ~$0–1 |
| API Gateway HTTP API (requests) | ~$0–1 |
| CloudFront (requests + data out, demo traffic) | ~$0–1 (no base fee) |
| CloudWatch Logs (90-day, low volume) | ~$1 |
| Secrets Manager secrets, Route53 zone | ~$1.50 |
| AWS Budget | $0 (first two budgets free) |
| **Total** | **~$26 / month** stock; **~$35–40 / month** with the live add-ons (Pro license $1.50, Bedrock/Studio AI per-token, Amazon Location per-call, ElastiCache ~$9 if/when Redis is enabled) |

This total is the **stock** apply (`enable_redis`, `enable_pro_license`,
`enable_bedrock_ai`, `enable_studio_ai`, and
`enable_amazon_location_geocoding` all default `false`). Before the
2026-07-24 cost round the live environment's fixed spend was ~$140/mo — the
delta is almost entirely interface-endpoint ENI-hours (~$110) plus the RDS
small→micro downsize (~$12).

The RDS instance and the NAT instance now dominate fixed cost. Lambda + API
Gateway are effectively free at demo traffic volumes. Compare to the ECS/ALB
equivalent (~$120–160/mo) — the main savings are eliminating the always-on
Fargate task, the ALB (~$20/mo), and the managed NAT gateway (~$33/mo) that
the fck-nat instance replaces.

## Post-deploy: seeding demo data

Demo datasets are loaded via the Honua Admin API import endpoints.

**Initial seed (manual, one-time):**

```bash
HONUA_URL=https://demo.honua.io
ADMIN_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id honua-demo-demo/admin-password \
  --query SecretString --output text)

# Example: import a GeoPackage as tenant "public"
curl -X POST "$HONUA_URL/admin/datasets/import" \
  -H "Authorization: Bearer $ADMIN_PASSWORD" \
  -F "file=@/path/to/demo-data.gpkg" \
  -F "tenantId=public"
```

Exact datasets and import parameters are TBD — coordinate with the data team.

**Nightly reseed (follow-up work):**

To keep the demo fresh and wipe user-contributed data, configure a nightly
reseed via an EventBridge scheduled rule that invokes the Lambda function
directly with a custom seed payload:

1. An EventBridge scheduled rule (cron expression, e.g. `cron(0 3 * * ? *)`).
2. The rule invokes the Honua Lambda function with a seed-trigger event.
3. The function (or a separate seed Lambda) drops and recreates the `public`
   tenant schema and reimports the canonical demo datasets.

Alternatively, the seed job can be a one-off Lambda function or a local script
triggered by EventBridge → Lambda. Unlike ECS `RunTask`, there is no cluster
or task-definition ARN needed — just the Lambda function ARN and an invocation
policy.

This is a follow-up infrastructure item; the EventBridge rule is not included
in Phase A.

## Destroying the environment

```bash
terraform destroy
```

No ALB deletion protection flag to disable first — API Gateway has no equivalent
setting.
