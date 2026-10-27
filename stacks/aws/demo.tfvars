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

# Amazon Location geocoding is ON for the demo (the server's compiled-in default is Nominatim).
enable_amazon_location_geocoding = true

# Redis backs the public process job queue/result store and the distributed
# fixed-window request budget. The managed geometry.buffer executor runs in the
# serving Lambda; Batch remains disabled for this bounded public process.
enable_redis = true
# Redis is WIRED into the serving Lambda again (ConnectionStrings__redis + the operation
# key-ring certificate reference, honua-server#6500). It was first wired on 2026-10-24 (#98)
# and rolled back by hand the same day because the serving process died silently inside
# warm containers. Root cause (honua-server#6558, fixed in honua-server#6644): after a
# Lambda freeze/thaw the admin API-key index backfill let a Redis TaskCanceledException
# escape, StopHost stopped the host, and the frozen sandbox left a listener-less process
# that the Lambda Web Adapter answered with "layers::panic" for ~8 minutes. The image below
# carries that fix plus a guard that exits a stopping host so Lambda recycles the sandbox.
# The key-ring PKCS#12 lives in the operator-owned secret below (created per
# runbook/exports-redis-prereq.md; never managed by Terraform, never delete it: it is the
# only key that can read the Redis key ring). Wiring also closes honua-server#6487 and
# unblocks MapServer/ImageServer exports, OGC process execution and jobs.
wire_redis_connection                     = true
operation_key_ring_certificate_secret_arn = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/operation-key-ring-GDtfsQ"
enable_gp_batch                           = false

# Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
# enabling it needs no secret material and no terraform import.
enable_pro_license = false
licensing_mode     = "Disabled"
