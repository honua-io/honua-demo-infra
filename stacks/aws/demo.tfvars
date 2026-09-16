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
#   honua_image                    Per-release, not per-environment. It must be the ECR image for the
#                                  server sha pinned in honua-release's platform-manifest.yaml, in
#                                  THIS region (Lambda cannot pull from GHCR). Hard-coding it here
#                                  would go stale exactly the way the old local file did.
#
# `stac_seed_metadata_environment` is committed below because the live database
# has one exact active Metadata v2 environment: `Production`. This value was
# read directly from `honua.metadata_v2_current`; it is not inferred from
# ASPNETCORE_ENVIRONMENT. The STAC seed gate also rejects any plan-time override,
# so an operator cannot silently seed the unused `default` environment.

region                         = "us-west-2"
route53_zone_id                = "Z089181827C9GKIKHXUTT"
stac_seed_metadata_environment = "Production"

# Studio AI — the demo runs Bedrock-backed generation.
enable_studio_ai  = true
studio_ai_model   = "us.anthropic.claude-sonnet-4-6"
enable_bedrock_ai = true

# Amazon Location geocoding is ON for the demo (the server's compiled-in default is Nominatim).
enable_amazon_location_geocoding = true

# Redis is OFF. There is no ElastiCache cluster for this stack — the names the runbook's old import
# commands referenced (honua-demo-redis, honua-demo-demo-redis, sg-0454e3341c5de3068) do not exist.
enable_redis = false

# Pro is adopted BY ARN — the module creates no secret and never reads the licence envelope, so
# enabling it needs no secret material and no terraform import.
enable_pro_license = true
