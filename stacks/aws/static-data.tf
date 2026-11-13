###############################################################################
# Static demo data on the demo CDN: https://demo.honua.io/demo-data/<prefix>/<file>
#
# The files come from release archives pinned by SHA-256 in
# static-data.lock.json. scripts/fetch-static-data.py (a prebuild step in
# stacks/drift-stacks.json) downloads and verifies them into .static-data/
# before every plan; the bytes never enter git. Every prefix is versioned and
# never rewritten, so browsers and the edge may keep objects for a year.
#
# The behavior serves byte-exact ranges: compress = false and no
# Accept-Encoding in the cache key, so Content-Length and Content-Range always
# describe the stored object. Range is CORS-safelisted for a single range, and
# the viewer-request function answers any other preflight at the edge.
###############################################################################

locals {
  static_data_path_pattern = "/demo-data/*"
  static_data_key_prefix   = "demo-data"
  static_data_lock         = jsondecode(file("${path.module}/static-data.lock.json"))
  static_data_max_age_sec  = 31536000

  static_data_objects = merge([
    for prefix, archive in local.static_data_lock.archives : {
      for name, file in archive.files : "${local.static_data_key_prefix}/${prefix}/${name}" => merge(file, {
        source = "${path.module}/.static-data/${prefix}/files/${name}"
      })
    }
  ]...)
}

resource "aws_s3_object" "static_data" {
  for_each = local.static_data_objects

  bucket        = aws_s3_bucket.demo_data.id
  key           = each.key
  source        = each.value.source
  source_hash   = each.value.sha256
  content_type  = each.value.content_type
  cache_control = "public, max-age=${local.static_data_max_age_sec}, immutable"
  tags          = local.common_tags

  lifecycle {
    precondition {
      condition     = try(filesha256(each.value.source), "") == each.value.sha256
      error_message = "${each.value.source} is missing or differs from static-data.lock.json: run scripts/fetch-static-data.py before planning."
    }
  }
}

resource "aws_cloudfront_cache_policy" "static_data" {
  name    = "${var.name_prefix}-${var.environment}-static-data-1y"
  comment = "One-year TTL for versioned, never-rewritten static demo data served from S3"

  min_ttl     = local.static_data_max_age_sec
  default_ttl = local.static_data_max_age_sec
  max_ttl     = local.static_data_max_age_sec

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = false
    enable_accept_encoding_brotli = false

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_response_headers_policy" "static_data" {
  #checkov:skip=CKV_AWS_259: Read-only public demo data over HTTPS only; HSTS is enforced at the distribution viewer_certificate level.
  name    = "${var.name_prefix}-${var.environment}-static-data-cors"
  comment = "CORS for byte-range reads of static demo data"

  cors_config {
    access_control_allow_credentials = false

    access_control_allow_origins {
      items = local.tile_cors_origins
    }

    access_control_allow_methods {
      items = ["GET", "HEAD", "OPTIONS"]
    }

    access_control_allow_headers {
      items = ["Range", "If-Range", "If-None-Match", "If-Modified-Since"]
    }

    access_control_expose_headers {
      items = ["Accept-Ranges", "Content-Range", "Content-Length", "ETag", "Last-Modified"]
    }

    access_control_max_age_sec = 86400

    origin_override = true
  }
}

resource "aws_cloudfront_function" "static_data_preflight" {
  name    = "${var.name_prefix}-${var.environment}-static-data-preflight"
  runtime = "cloudfront-js-2.0"
  comment = "Answer CORS preflights for /demo-data/* at the edge; S3 never sees the viewer Origin"
  publish = true
  code    = <<-EOT
    var CORS_ORIGINS = ${jsonencode(local.tile_cors_origins)};
    function handler(event) {
      var request = event.request;
      if (request.method !== 'OPTIONS') return request;
      var headers = {
        'access-control-allow-methods': { value: 'GET, HEAD, OPTIONS' },
        'access-control-allow-headers': { value: 'Range, If-Range, If-None-Match, If-Modified-Since' },
        'access-control-max-age': { value: '86400' },
        'vary': { value: 'Origin' }
      };
      var origin = request.headers.origin && request.headers.origin.value;
      if (CORS_ORIGINS.indexOf('*') !== -1) {
        headers['access-control-allow-origin'] = { value: '*' };
      } else if (origin && CORS_ORIGINS.indexOf(origin) !== -1) {
        headers['access-control-allow-origin'] = { value: origin };
      }
      return { statusCode: 204, statusDescription: 'No Content', headers: headers };
    }
  EOT
}
