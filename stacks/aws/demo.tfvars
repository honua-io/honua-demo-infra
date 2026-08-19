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
#   export TF_VAR_honua_admin_password="$(aws secretsmanager get-secret-value \
#     --secret-id honua-demo-demo/admin-password --region us-west-2 \
#     --query SecretString --output text)"
#   terraform plan -var-file=demo.tfvars \
#     -var "honua_image=<ECR image for the manifest-pinned server>" \
#     -var "stac_seed_metadata_environment=<see below>"
#
# The three values NOT in this file, and why:
#
#   honua_admin_password           SECRET. Read the CURRENT value back from Secrets Manager:
#                                    aws secretsmanager get-secret-value \
#                                      --secret-id honua-demo-demo/admin-password --region us-west-2
#                                  Yes, terraform OWNS that secret
#                                  (module.honua.aws_secretsmanager_secret.admin_password), so it is
#                                  an OUTPUT of this apply and feeding it back in is circular in
#                                  principle. In practice it is the only source that exists and it is
#                                  stable: the 2026-08-18 apply passed the value read this way and
#                                  produced no change to the secret version, which is the check that
#                                  it round-trips. Passing a DIFFERENT value silently rotates the
#                                  admin password and the connection-encryption master key derived
#                                  from it — so read it, never invent it.
#                                  NOT in `pass`: honua-iac's scripts/lib/tf-secret-catalog.sh lists
#                                  HONUA_ADMIN_PASSWORD as an essential secret, but no such entry
#                                  exists in the operator password store today (it holds only
#                                  honua/aws/demo). That catalog is the intended future home, not the
#                                  current one — do not send someone to `pass` for this value.
#
#   honua_image                    Per-release, not per-environment. It must be the ECR image for the
#                                  server sha pinned in honua-release's platform-manifest.yaml, in
#                                  THIS region (Lambda cannot pull from GHCR). Hard-coding it here
#                                  would go stale exactly the way the old local file did.
#
#   stac_seed_metadata_environment Must be the exact Metadata v2 environment the deployed Lambda
#                                  serves — read it from the active `metadata_v2_current` row, or
#                                  from Metadata__Environment / Environment if either is set. Do NOT
#                                  infer it from ASPNETCORE_ENVIRONMENT, and do not copy the
#                                  "default" placeholder out of terraform.tfvars.example: the live
#                                  Lambda (published version behind the `live` alias) sets neither
#                                  Metadata__Environment nor ASPNETCORE_ENVIRONMENT, so the
#                                  capability manifest's `deploymentEnvironment: Production` is just
#                                  ASP.NET's default and is precisely the inference the variable's
#                                  description forbids. Getting it wrong points the managed STAC seed
#                                  at the wrong metadata environment.

region          = "us-west-2"
route53_zone_id = "Z089181827C9GKIKHXUTT"

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
