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

# The serving image: the amd64 Lambda AOT build of honua-server trunk 574c804176d9 (contains
# honua-server#6644, the #6558 fix) published by "Build & Publish Platform Images" run
# 40540495543 to this region's ECR (tag trunk-574c804-lambda-aot-amd64). The function
# architecture is x86_64 in main.tf and must match. Digest-pinned; bump with the server pin.
honua_image = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:82cf04288a8f7a5aa054f06e7a2201fbc72e00fe7e6fabfa7b7eac4acfd0788e"

# Studio AI — the demo runs Bedrock-backed generation.
enable_studio_ai  = true
studio_ai_model   = "us.anthropic.claude-sonnet-4-6"
enable_bedrock_ai = true

# Geocoding uses the server's compiled-in default, Nominatim (OpenStreetMap),
# reached via the fck-nat egress. Amazon Location is not used.

# Founder-directed Redis removal (2026-11-13): disable both provisioning and
# connection injection. This removes the managed cache, its connection secret,
# Redis egress and operation-key-ring IAM/env wiring in the normal saved plan.
# Preserve the operator-owned key-ring secret documented in the historical
# runbook/exports-redis-prereq.md; Terraform does not own that secret.
# Before applying, follow runbook/remove-demo-redis.md: the old image pinned above
# still needs Redis for durable process jobs. Bind a compatible image and canary
# evidence to the same reviewed deployment; do not weaken the existing canary.
enable_redis          = false
wire_redis_connection = false
enable_gp_batch       = false

# Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
# enabling it needs no secret material and no terraform import.
enable_pro_license = false
licensing_mode     = "Disabled"

# EMERGENCY ROLLBACK (runbook/rollback-by-pr.md): pin the live alias to an already-published
# version in a one-line PR, e.g.  lambda_alias_version = "52"  . terraform.yml applies it on
# merge. Leave unset (null) in normal operation so each apply's new version goes live.
# lambda_alias_version = "52"
