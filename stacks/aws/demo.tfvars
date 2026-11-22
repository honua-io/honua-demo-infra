# Non-secret configuration for the demo.honua.io stack.
#
# WHY THIS FILE IS COMMITTED AND terraform.tfvars IS NOT
# Exactly one input to this stack is secret — `honua_admin_password` — and it does not belong in a
# file at all (see below). Everything here is ordinary configuration: a region, a hosted-zone id,
# feature toggles and a model id. Keeping it on one workstation meant `terraform plan` could not be
# run or reviewed by anyone else, and the local copy silently drifted (its `honua_image` was two
# server pins and one CPU architecture behind the platform manifest). Committed, it is diffable,
# reviewable, and the plan is reproducible by anyone with read access.
#
# HOW TO APPLY
#   source <(../../../honua-iac/scripts/tf-pass-secrets.sh export)   # provides HONUA_ADMIN_PASSWORD
#   export TF_VAR_honua_admin_password="$HONUA_ADMIN_PASSWORD"
#   terraform plan -var-file=demo.tfvars \
#     -var "honua_image=<ECR image for the manifest-pinned server>"
#
# The three values NOT in this file, and why:
#
#   honua_admin_password           SECRET. Lives in `pass` under honua/terraform (honua-iac's
#                                  scripts/lib/tf-secret-catalog.sh lists HONUA_ADMIN_PASSWORD as an
#                                  essential secret). It must NOT be sourced from Secrets Manager:
#                                  terraform OWNS that secret
#                                  (module.honua.aws_secretsmanager_secret.admin_password), so the
#                                  secret is an OUTPUT of this apply. Reading it back as an input
#                                  would be circular.
#
#   honua_image                    Now pinned below by digest for the rc cutover (it must be the ECR
#                                  image for the server sha in THIS region; Lambda cannot pull from
#                                  GHCR). Update it with every server pin, in the same change.
#
# `stac_seed_metadata_environment` is committed below because the live database
# has one exact active Metadata v2 environment: `Production`. This value was
# read directly from `honua.metadata_v2_current`; it is not inferred from
# ASPNETCORE_ENVIRONMENT. The STAC seed gate also rejects any plan-time override,
# so an operator cannot silently seed the unused `default` environment.

region                         = "us-west-2"
route53_zone_id                = "Z089181827C9GKIKHXUTT"
stac_seed_metadata_environment = "Production"

# The serving image: the Redis-free amd64 Lambda AOT build of honua-server trunk 99c422646c4c,
# published by "Build & Publish Platform Images" run 44958072387 to this region's ECR (tag
# trunk-99c4226-lambda-aot-amd64; this is its runnable linux/amd64 child, the attested parent
# is sha256:13c99531...). It keeps durable process jobs and package drafts in Postgres, so it
# serves without Redis. It needs the 99c4226 migration set: apply this only after the governed
# runner receipt (runbook/db-migrations-99c4226.md). The function architecture is x86_64 in
# main.tf and must match. Digest-pinned; bump with the server pin.
honua_image = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:2ef00dc750c8222ed01694f52371bfd3e7b17cd12378ad6f2281efdde2bd2e76"

# Studio AI — the demo runs Bedrock-backed generation.
enable_studio_ai  = true
studio_ai_model   = "us.anthropic.claude-sonnet-4-6"
enable_bedrock_ai = true

# Geocoding uses the server's compiled-in default, Nominatim (OpenStreetMap),
# reached via the fck-nat egress. Amazon Location is not used.

# Founder-directed Redis removal (2026-11-13, applied in #132): keep both
# provisioning and connection injection disabled. The managed cache, its
# connection secret and Redis egress stay removed; only the preserved
# operator-owned key-ring secret below is wired for Postgres durable jobs.
enable_redis          = false
wire_redis_connection = false
enable_gp_batch       = false

# The Redis-free image keeps the operation key ring in the system database and refuses to
# start in Production without the key-ring certificate, so the operator-owned certificate
# preserved by the Redis removal is wired again on its own (Pkcs12 secret reference plus a
# GetSecretValue grant on exactly this ARN; no Redis).
operation_key_ring_certificate_secret_arn = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/operation-key-ring-GDtfsQ"

# Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
# enabling it needs no secret material and no terraform import.
enable_pro_license = false
licensing_mode     = "Disabled"

# EMERGENCY ROLLBACK (runbook/rollback-by-pr.md): pin the live alias to an already-published
# version in a one-line PR, e.g.  lambda_alias_version = "52"  . terraform.yml applies it on
# merge. Leave unset (null) in normal operation so each apply's new version goes live.
# lambda_alias_version = "52"
