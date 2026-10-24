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

# The serving image: the amd64 Lambda AOT build of honua-server 199bce77fe99 mirrored
# into this region's ECR (tag rc-2026.1-rc.1-lambda-aot-199bce7-amd64). The function
# architecture is x86_64 in main.tf and must match. Digest-pinned; bump with the server pin.
honua_image = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@sha256:24bb5279ece9dc55da46a69c4bbed5430a773490fcb11476c5ab615f7305e7d3"

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
# Redis is provisioned but UNWIRED again. It was wired on 2026-10-24 21:55Z (#98) and the
# live alias was rolled back to the pre-Redis version at 22:34Z the same day: with
# ConnectionStrings__redis set, the serving process died silently inside warm containers
# (last app line: a MapServer export failing with "Distributed import progress state is
# unavailable", then DbUp journal checks, then the Lambda Web Adapter panicking with
# "tcp connect error: Connection refused" on every later invocation of that container);
# the function logged 0 errors in the 14 h before the flip and 97 in the hour after.
# See runbook/foss4g-demo-apply-20261024.md ("Rollback") and the honua-server issue it links.
# The operation key-ring secret stays (operator-owned; it is the only key for the ring) and
# its ARN stays here so the re-flip is a one-line change once the server fix is pinned.
wire_redis_connection                     = false
operation_key_ring_certificate_secret_arn = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/operation-key-ring-GDtfsQ"
enable_gp_batch                           = false

# Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
# enabling it needs no secret material and no terraform import.
enable_pro_license = false
licensing_mode     = "Disabled"
