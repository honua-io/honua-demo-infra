###############################################################################
# Demo seed-data wiring — S3 FileStorage bucket, Lambda IAM access, and the
# /fonts glyph route.
#
# Supports seeding demo.honua.io with the Maui open-data layers consumed by
# https://honua.io/demo.html:
#   - honua-demo-data-585192672263 is the Honua FileStorage bucket
#     (FileStorage__AwsS3__* in additional_env, main.tf). It holds:
#       * import staging objects (folder "imports/", written by the server),
#       * the Protomaps basemap extract served by the PMTiles range proxy at
#         /api/v1/tiles/pmtiles/maui-basemap (key "maui-basemap"),
#       * SDF glyph PBFs under fonts/ (Noto Sans Regular).
#   - The Honua server has no native /fonts route, but the demo page contract
#     requires /fonts/{fontstack}/{range}.pbf, so API Gateway proxies that
#     path straight to the bucket's public fonts/ prefix and stamps a
#     wildcard CORS header on the way out.
###############################################################################

locals {
  data_bucket_name = "honua-demo-data-585192672263"
}

resource "aws_s3_bucket" "demo_data" {
  #checkov:skip=CKV_AWS_18: Access logging omitted for the public read-only demo FileStorage bucket; cost and complexity unjustified for a $23/mo demo environment.
  #checkov:skip=CKV_AWS_21: Versioning disabled; demo data is reseeded via scripts rather than versioned — enabling versioning would accumulate unbounded storage cost on a demo bucket.
  #checkov:skip=CKV_AWS_144: Cross-region replication not required; demo environment has no DR or SLA requirement.
  #checkov:skip=CKV_AWS_145: SSE-S3 (AES-256) is applied via aws_s3_bucket_server_side_encryption_configuration; KMS CMK is not required for open-licensed demo glyph/tile data.
  #checkov:skip=CKV2_AWS_6: Public access block is intentionally partial (see aws_s3_bucket_public_access_block.demo_data) to allow the bucket policy that exposes the fonts/ prefix.
  #checkov:skip=CKV2_AWS_61: No lifecycle policy; demo bucket contains static seed assets that are replaced wholesale on reseed rather than aged out.
  #checkov:skip=CKV2_AWS_62: Event notifications not required for a read-only demo FileStorage bucket with no automated downstream processing.
  bucket = local.data_bucket_name
  tags   = local.common_tags
}

# Public access stays blocked except for the bucket policy that exposes the
# fonts/ prefix (open-licensed glyph PBFs only — everything else in the bucket
# remains private and is reached through the Lambda role or presigned URLs).
resource "aws_s3_bucket_public_access_block" "demo_data" {
  #checkov:skip=CKV_AWS_54: block_public_policy=false is required to allow the aws_s3_bucket_policy that exposes the fonts/ prefix; all other prefixes remain private via the IAM policy.
  #checkov:skip=CKV_AWS_56: restrict_public_buckets=false is required to serve the fonts/ prefix via the public bucket policy; the policy is scoped to s3:GetObject on fonts/* only.
  bucket                  = aws_s3_bucket.demo_data.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

# CORS for the glyph requests: the /fonts API Gateway route below is a pure
# HTTP proxy, so the browser's Origin header reaches S3 and S3 answers with
# the Access-Control-Allow-Origin header itself (API Gateway HTTP APIs refuse
# parameter mappings on access-control-* headers).
resource "aws_s3_bucket_cors_configuration" "demo_data" {
  bucket = aws_s3_bucket.demo_data.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 86400
  }
}

resource "aws_s3_bucket_policy" "demo_data_fonts_public" {
  #checkov:skip=CKV_AWS_70: Principal="*" is intentional; map glyph PBFs (open-licensed Noto Sans SDF fonts) must be world-readable so MapLibre can load them from any browser without authentication.
  bucket = aws_s3_bucket.demo_data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGlyphs"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.demo_data.arn}/fonts/*"
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.demo_data]
}

# ---------------------------------------------------------------------------
# Lambda role access to the data bucket (FileStorage provider, PMTiles range
# reads, import staging). The module does not export the role name, so derive
# it from the deployed function's role ARN.
# ---------------------------------------------------------------------------

data "aws_lambda_function" "honua" {
  function_name = module.honua.lambda_function_name
}

locals {
  honua_lambda_role_name = regex("[^/]+$", data.aws_lambda_function.honua.role)
}

resource "aws_iam_role_policy" "lambda_demo_data" {
  name = "${var.name_prefix}-${var.environment}-demo-data-s3"
  role = local.honua_lambda_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectAttributes"
        ]
        Resource = "${aws_s3_bucket.demo_data.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.demo_data.arn
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# /fonts/{fontstack}/{range}.pbf — glyphs for MapLibre symbol layers.
# HTTP proxy route on the existing demo API straight to the public fonts/
# prefix. CORS comes from the bucket CORS configuration above (the app-level
# Honua CORS policy never sees this route).
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "fonts" {
  api_id                 = local.api_id
  integration_type       = "HTTP_PROXY"
  integration_method     = "GET"
  integration_uri        = "https://${aws_s3_bucket.demo_data.bucket_regional_domain_name}/fonts/{proxy}"
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "fonts" {
  #checkov:skip=CKV_AWS_309: Authorization type is intentionally NONE; the /fonts route is a public proxy to open-licensed SDF glyph PBFs that MapLibre fetches unauthenticated from any browser.
  api_id    = local.api_id
  route_key = "GET /fonts/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.fonts.id}"
}
