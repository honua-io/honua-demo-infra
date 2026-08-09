# honua-demo-infra

Infrastructure-as-code for **demo.honua.io** — the live, public, hosted
Honua demo environment. This repo was extracted from
[honua-io/honua-iac](https://github.com/honua-io/honua-iac)
(`infrastructure/terraform/examples/aws-demo`) via
[honua-iac#126](https://github.com/honua-io/honua-iac/issues/126) because that
path was codifying a **live environment**, not a reusable example, and its
demo-ops churn was polluting the honua-iac backlog. This repo is **private**
(unlike honua-iac and honua-server, which are public) because it holds
account IDs, VPC/security-group/subnet IDs, and other operational detail
about a real running AWS account.

## Repository and domain responsibilities

This repository owns the Terraform root, data seeding, published service
manifest, operational runbooks, and deployment operations for the live demo
environment. It contains no sample application or developer-gallery code.

- `demo.honua.io` is the live environment surface. It exposes health, runtime
  capabilities, and the seeded-service manifest consumed by clients and
  canaries.
- `samples.honua.io` is the developer learning center and gallery. Its
  examples, walkthroughs, projects, and publication checks live in
  [honua-io/honua-samples](https://github.com/honua-io/honua-samples).
- Product-story demos embedded on `honua.io` remain owned by
  [honua-io/honua-site](https://github.com/honua-io/honua-site).

## What's here

```
stacks/aws/          The Phase A demo.honua.io Terraform root module
  main.tf              Lambda + API Gateway + RDS via the aws-serverless module,
                        custom domain (ACM + Route53)
  cloudfront.tf         CDN layer — tile caching, edge CORS, Origin Shield
  vpc-endpoints.tf      No-NAT VPC endpoints (Secrets Manager, S3, Bedrock, geo)
  seed-data.tf          FileStorage S3 bucket, /fonts glyph proxy route
  postgis-bootstrap.tf / postgis-bootstrap/
                        One-shot in-VPC Lambda that enables postgis + postgis_raster
  variables.tf / outputs.tf / versions.tf
  terraform.tfvars.example
  SEED_MANIFEST.md      Maui Nui open-data seed provenance and pipeline notes
  scripts/              warm-demo-cdn.mjs (CDN pre-warm), seed-test-service.sh
                        (SDK client-compat certification target)
  README.md             Full operational README for the stack (moved verbatim
                        from honua-iac; read this before touching anything)
manifest/
  demo-services.v1.json Generated public service manifest (#19) — see below
  generate-demo-services.py
                        Derives the manifest from the seed definitions
  README.md             Manifest schema + publish-path documentation
.github/workflows/
  drift-plan.yml        Scheduled read-only `terraform plan` (see below)
  manifest-drift.yml    Fails PRs where the manifest is stale vs the seeds
```

There is no application code here — Honua itself lives in
[honua-io/honua-server](https://github.com/honua-io/honua-server). This repo
only provisions and operates the AWS resources the demo runs on.

## State / backend — the truth

`stacks/aws/versions.tf` carries a **live, active** `backend "s3"` block:

| | |
|---|---|
| Bucket | `honua-tfstate-585192672263` |
| Key | `demo/aws-demo/terraform.tfstate` |
| Region | `us-east-1` (the bucket's region — deliberately different from the demo's own `us-west-2`) |
| Locking | S3-native conditional-write locking (`use_lockfile`, Terraform >= 1.10) |

This state **is reachable and was read successfully** as part of the
honua-iac#126 extraction (see "Migration proof" below) — it is not
local/hand-managed state, despite that having been true in the past (older
revisions of the stack's own README describe a since-fixed state predating
honua-iac#122).

**Known drift**: the live account runs three add-ons out-of-band —
Pro license (adopt-by-ARN, already clean), Bedrock AI, and Redis (the latter
two applied directly via AWS CLI/console and **not yet imported into this
Terraform state** — a real `terraform plan` with `enable_bedrock_ai = true` /
`enable_redis = true` currently proposes to *create* new copies of those
resources rather than showing no changes). `stacks/aws/README.md` → "Pro + AI
demo drift" documents the exact `terraform import` commands an operator must
run once before a real `apply` with those toggles on. This is pre-existing
drift, carried over faithfully from honua-iac — the extraction did not create
or worsen it.

## Migration proof (honua-iac#126)

From this repo, `terraform -chdir=stacks/aws init -backend=false && terraform
-chdir=stacks/aws validate && terraform fmt -check -recursive stacks/aws` all
pass cleanly.

A **real** `terraform init` (live S3 backend) and `terraform plan` (read-only,
never applied) were also run as part of the extraction, from this exact
checkout, and confirmed:

- The pinned module git ref (`git::https://…honua-iac.git//…?ref=<sha>`)
  resolves and fetches correctly (honua-iac is private — this proves the git
  credential path works, not just the URL syntax).
- The S3 backend connects and reads all ~104 resources of real remote state.
- The plan is **not** a literal no-op today, for three reasons, none of which
  are migration artifacts:
  1. `enable_redis` / `enable_bedrock_ai` propose creates — the pre-existing
     known drift above; needs the documented `terraform import` first.
  2. The `postgis_bootstrap_build` `terraform_data` trigger hash differs from
     what's in state — the source `handler.py` was edited after the state's
     last real apply (pre-existing repo/state skew, unrelated to the move).
  3. Supplying the real `honua_admin_password` secret value was deliberately
     skipped for this proof (to avoid pulling a live production secret onto a
     shared workstation) — a placeholder was used instead, which forces a
     `aws_secretsmanager_secret_version` replacement in the diff. An operator
     running the plan with the real value will not see this line.
  A minor `aws_lambda_function`/`aws_lambda_alias` version diff also appeared,
  most likely from the `hashicorp/aws` provider having moved forward since
  the state's last real apply (a sensitivity-marking behavior change on
  `environment.variables`), not a config difference.

**What the operator should run for a literal zero-diff plan**: from
`stacks/aws`, run the `terraform import` commands in `stacks/aws/README.md`
(Bedrock VPC endpoint + SG, the Redis replication group/subnet group/SG/
secret), supply the real `honua_admin_password` value, and re-run
`terraform plan` with `enable_redis = true` and `enable_bedrock_ai = true`.
That plan is expected to show 0 changes (module/provider-version
housekeeping aside).

## How to plan / apply

```bash
cd stacks/aws
cp terraform.tfvars.example terraform.tfvars   # fill in real values; NEVER commit this file
terraform init      # needs git credentials that can fetch the private honua-iac module — see below
terraform plan
terraform apply     # only ever run this deliberately, against the real account
```

### Module source (private-repo auth)

`stacks/aws/main.tf`'s `module "honua"` source is a pinned git ref of
honua-iac:

```
git::https://github.com/honua-io/honua-iac.git//infrastructure/terraform/modules/aws-serverless?ref=<sha>
```

honua-iac is **private**, so whatever runs `terraform init` needs git
credentials that can clone it: an interactive workstation with
`gh auth login` (its HTTPS credential helper is what made the module resolve
during this extraction) works locally; CI needs a machine token/deploy key
with read access to honua-iac configured as the git credential before the
Terraform steps run.

## Deployment model — frozen, published Lambda versions

Honua deploys to this stack by **publishing a new Lambda version and moving
the `live` alias to point at it** (`aws-serverless` module's
`aws_lambda_alias.live` + `deploy-control.tf`), not by pushing a mutable
`$LATEST`. The server's coordinated deploy/rollback backend
(`AwsLambdaGitOpsDeployBackend`) calls `lambda:UpdateAlias` to roll forward
and `lambda:UpdateAlias` back to the prior version to roll back, gated on
CloudWatch health telemetry. Because this VPC has no NAT gateway,
`deploy-control.tf` provisions interface VPC endpoints (Lambda control API,
STS, CloudWatch monitoring/logs) so those control-plane calls have a network
path — without them the deploy backend's calls hang until Lambda times out,
which is why the demo's alias flip has historically been a manual,
out-of-band step (see honua-server#2166). Every deployed image is therefore a
specific, immutable, already-built Lambda container version — "frozen" in
the sense that a deploy never rebuilds or mutates a running version in place,
it only points the alias at a different already-published one (and rollback
is exactly the same operation in reverse).

## Seed data

- **Terraform-native seeding** (S3 FileStorage bucket, PostGIS bootstrap,
  the Maui Nui open-data pipeline) lives in this repo: `seed-data.tf`,
  `SEED_MANIFEST.md`, `postgis-bootstrap/`.
- **Schema-coupled seed SQL stays in honua-server** (`tests/seed/*.sql` —
  validated there against the server's own migrations, not duplicated here).
  Reference it by a pinned honua-server ref, e.g.:

  ```
  https://raw.githubusercontent.com/honua-io/honua-server/30d6d6f115e23e193fdaceb2a5742180165cc618/tests/seed/demo-stac-imagery-v1.sql
  ```

  honua-server is public, so that raw URL works without auth. Bump the
  `30d6d6f1…` ref deliberately when picking up newer seed fixtures; don't
  float on `trunk`. See `stacks/aws/SEED_MANIFEST.md` for which fixtures apply
  to which layers and `stacks/aws/scripts/seed-test-service.sh` for the one
  seed path in this repo (it deliberately does *not* apply
  `tests/seed/client-compat-v1.sql` directly — see that script's header for
  why).

## Protected client-compat fixture

The protected SDK fixture is governed by
`stacks/aws/client-compat-seed.v1.json` and the generated non-secret
`manifest/client-compat.v1.json` descriptor. Rotate server/image/service/layer
and credential bindings only through
[`runbook/client-compat-rotation.md`](./runbook/client-compat-rotation.md).
The public `demo-services.v1.json` continues to exclude this fixture.

## Service manifest (demo-services.v1.json)

`manifest/demo-services.v1.json` is the generated, schema-versioned inventory
of the demo's publicly discoverable services (issue #19) — derived from
`stacks/aws/SEED_MANIFEST.md` and the pinned STAC seed above, never edited by
hand (`.github/workflows/manifest-drift.yml` enforces this). Its stable
public URL is `https://demo.honua.io/demo-services.v1.json`; the publish
wiring (`stacks/aws/demo-services-manifest.tf`) has been live and tracked in
the shared Terraform state since 2026-07-31. The scheduled public canary
probes every declared service family and uploads its receipt. Consumer:
honua-io/honua-sdk-js#825. See [`manifest/README.md`](./manifest/README.md).

## Demo ops runbook

See [`runbook/`](./runbook) for the operator capability runbook (moved from
honua-server `docs/internal/demo/`) and its verification scripts, if present
— check that directory's own header for whether it reflects the merged
honua-server#3009 runbook v2 or a provisional pre-merge import (see that
runbook for which).

The planned Maui WMS image promotion, semantic canary, admission, and exact
Lambda alias rollback are defined in
[`runbook/wms-release-promotion.md`](./runbook/wms-release-promotion.md). It
must remain fail-closed until both runtime and source-governance gates pass.

## Drift-plan CI: one-time operator setup

`.github/workflows/drift-plan.yml` runs a scheduled, **read-only**
`terraform plan` against the live account so the known hand-managed drift
(above) becomes a visible signal instead of a surprise. It never applies.

It ships **disabled** because the AWS OIDC role it needs does not exist yet:

1. Provision an AWS OIDC role for this repo — mirror honua-iac's
   `infrastructure/terraform/components/aws-github-oidc` component (trust
   `repo:honua-io/honua-demo-infra:*` or a tighter `environment:` subject; grant it
   read-only Describe/Get/List on the demo stack's resources, plus
   `s3:GetObject`/`s3:PutObject` scoped to the `demo/aws-demo/` state key for
   backend init/locking — **not** broad write access).
2. Set the repo variable `HONUA_DEMO_DRIFT_ROLE_ARN` to that role's ARN.
3. Set repo variables `HONUA_DEMO_HONUA_IMAGE` (the live ECR image URI) and
   `HONUA_DEMO_ROUTE53_ZONE_ID` (the `demo.honua.io` hosted zone ID), and repo
   secret `HONUA_DEMO_ADMIN_PASSWORD` (the live admin-password secret value),
   so the plan reflects real inputs instead of failing on missing required
   variables.
4. Set repo variable `HONUA_DEMO_DRIFT_ENABLED=true` and uncomment the
   `schedule:` block in the workflow to turn on the daily run.

Until all of the above are done, the workflow only runs on manual
`workflow_dispatch` and skips neutrally (not a failure) if the role ARN is
still the placeholder.

## Related

- [honua-io/honua-iac](https://github.com/honua-io/honua-iac) — the
  `aws-serverless` module this stack consumes (pinned by git ref, see
  above), and the wider Honua Terraform module library. `examples/aws-demo`
  there is now a stub pointing here.
- [honua-io/honua-server](https://github.com/honua-io/honua-server) — the
  application this stack deploys.
