###############################################################################
# demo-services.v1.json — publish the generated service manifest (#19).
#
# >>> PLAN-ONLY FOR NOW — DO NOT APPLY <<<
# The stack has pending out-of-band drift that must be `terraform import`ed
# first (README.md "Known drift" / the drift-plan workflow; state-import work
# tracked in #11). Until the operator has run those imports and a plan with
# the real toggles shows zero unrelated changes, nothing in this file may be
# applied. It is committed now so the publish path is codified and reviewed;
# `terraform validate` / `fmt` / read-only `plan` are fine.
#
# What it does once applied:
#   - Uploads the committed manifest/demo-services.v1.json (generated from the
#     seed definitions by manifest/generate-demo-services.py, drift-gated by
#     .github/workflows/manifest-drift.yml) to the demo data bucket under the
#     public manifest/ prefix (world-readable via the bucket policy statement
#     in seed-data.tf, same pattern as the fonts/ glyph prefix).
#   - Routes GET /demo-services.v1.json on the demo API straight to that
#     object (HTTP proxy, same pattern as the /fonts route), so the stable
#     public URL is:
#
#         https://demo.honua.io/demo-services.v1.json
#
#     CloudFront's default behavior (CachingDisabled pass-through) covers this
#     route — no CDN cache to invalidate on reseed; freshness is governed by
#     the object's own Cache-Control (5 min) for browsers/consumers.
#
# Because the S3 object content comes from the committed file, every future
# `terraform apply` of this stack republishes the current manifest — the
# deploy/seed flow and the publish step stay a single pipeline, as #19 asks.
# The manifest only lists what is already publicly discoverable through
# demo.honua.io capability endpoints, so publishing it is safe.
###############################################################################

locals {
  demo_services_manifest_file = "${path.module}/../../manifest/demo-services.v1.json"
}

resource "aws_s3_object" "demo_services_manifest" {
  bucket        = aws_s3_bucket.demo_data.id
  key           = "manifest/demo-services.v1.json"
  content       = file(local.demo_services_manifest_file)
  content_type  = "application/json; charset=utf-8"
  cache_control = "public, max-age=300"

  # Re-upload whenever the committed manifest changes.
  etag = md5(file(local.demo_services_manifest_file))

  tags = local.common_tags
}

resource "aws_apigatewayv2_integration" "demo_services_manifest" {
  api_id                 = local.api_id
  integration_type       = "HTTP_PROXY"
  integration_method     = "GET"
  integration_uri        = "https://${aws_s3_bucket.demo_data.bucket_regional_domain_name}/manifest/demo-services.v1.json"
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "demo_services_manifest" {
  #checkov:skip=CKV_AWS_309: Authorization type is intentionally NONE; the manifest is a public, generated inventory of already publicly discoverable demo services (no credentials, no infra internals) — see manifest/README.md.
  api_id    = local.api_id
  route_key = "GET /demo-services.v1.json"
  target    = "integrations/${aws_apigatewayv2_integration.demo_services_manifest.id}"
}
