###############################################################################
# Phase-A Honua Demo Environment — demo.honua.io
#
# Lambda container (AOT) + API Gateway HTTP API + RDS db.t4g.small + PostGIS.
# Optional Pro+AI demo add-ons, all gated off by default (see README → "Pro +
# AI demo drift"): a Secrets-Manager-delivered Pro license (enable_pro_license),
# a least-privilege Bedrock InvokeModel grant + WorkflowGeneration env for the
# AI studio (enable_bedrock_ai, plus the Bedrock VPC endpoint in vpc-endpoints.tf
# this no-NAT VPC requires), and an in-VPC ElastiCache Redis (enable_redis) for
# the durable feature-change event store /healthz/ready needs in Production.
# No WAF: API Gateway HTTP API does not support WAFv2 association; rate-limiting
# is handled via API Gateway throttle settings (see throttle variables below).
# See README.md for DNS prerequisites before applying.
###############################################################################

locals {
  common_tags = merge({
    Project     = "honua-server"
    Environment = var.environment
    ManagedBy   = "terraform"
    Purpose     = "public-demo"
  }, var.tags)

  # Declared here (rather than relying on the module default) because it is
  # also used for the VPC-endpoint security group and the in-VPC PostGIS
  # bootstrap (see vpc-endpoints.tf / postgis-bootstrap.tf).
  vpc_cidr = "10.0.0.0/16"
}

# ---------------------------------------------------------------------------
# Honua Server — Lambda / API Gateway HTTP API
# ---------------------------------------------------------------------------

module "honua" {
  # Pinned to honua-io/honua-iac trunk at the commit this stack was extracted
  # from (honua-iac#126). honua-iac is a PRIVATE repo, so `terraform init`
  # needs git credentials that can fetch it — see README.md → "Module source
  # (private repo auth)". Bump the ref deliberately (and re-run the drift
  # plan) when picking up module changes; do not float on a branch.
  source = "git::https://github.com/honua-io/honua-iac.git//infrastructure/terraform/modules/aws-serverless?ref=32112c417c30413752294f0cac5d152d50a11058"

  # Identity
  name_prefix = var.name_prefix
  environment = var.environment

  # Lambda container — use the AOT image variant for fast cold starts.
  # x86_64, not Graviton: the demo image is AOT-built on an amd64 workstation
  # and .NET AOT under QEMU arm64 emulation fails (MSBuild MSB4223 node spawn
  # error). Function and image architecture must match. Revisit when images
  # come from CI's native arm64 runners.
  image                = var.honua_image
  lambda_architectures = ["x86_64"]
  lambda_memory_size   = var.lambda_memory_size

  # No provisioned concurrency: cold starts are acceptable for a demo.
  # The AOT image keeps cold start latency short (~200–400 ms typical).
  #
  # Reserved concurrency 50 (2026-06-12, LIVE): was 20, set out-of-band in
  # the console as an emergency brake when browser tile bursts exhausted the
  # micro instance's connection slots (53300) — encoded here so applies stop
  # reverting it. 50 environments x Maximum Pool Size 4 = 200 direct
  # connections, under db.t4g.small's ~225-slot ceiling; bursts beyond 50
  # throttle at Lambda instead of 500ing every in-flight request.
  lambda_reserved_concurrent_executions = 50

  # 60 s bounds abandoned work: API Gateway gives up at 30 s, but the Lambda
  # keeps executing until this timeout. During seeding this was raised to 600
  # (the county-parcels synchronous import needs ~5 min); steady-state it must
  # stay LOW — a browser tile burst that outruns the db.t4g.micro otherwise
  # leaves a pile of orphaned multi-minute queries that starve the database
  # and 500 every later request. Raise temporarily for future bulk re-seeds.
  lambda_timeout_seconds = 60

  # Secrets
  admin_password = var.honua_admin_password

  # Database — db.t4g.small + PostGIS (upgraded from micro 2026-06-12 with
  # founder approval, APPLIED LIVE: tile bursts exhausted micro's ~112
  # connection slots — 4,400+ Npgsql 53300 errors in 48h while CPU never
  # passed 48%; small doubles memory and the max_connections formula
  # (~225 slots) for roughly +$12/mo).
  db_instance_class    = "db.t4g.small"
  db_allocated_storage = 20
  db_engine_version    = "15"
  db_password          = var.db_password
  db_require_ssl       = true
  db_multi_az          = false # single-AZ for demo cost
  db_apply_immediately = true  # demo: take resize outages now, not in the maintenance window

  # Npgsql pool tuning, LIVE in the connection-string secret since
  # 2026-06-12 (previously hand-edited there — an apply used to silently
  # revert it). Pool stays small on purpose: every Lambda execution
  # environment runs its own pool, so worst case is
  # reserved_concurrency x Maximum Pool Size connections.
  db_connection_string_options = "Maximum Pool Size=4;Connection Idle Lifetime=60;Connection Pruning Interval=30"

  # PostGIS + PostGIS Raster are required by Honua, but the module's
  # enable_postgis local-exec needs psql plus a network path to the private
  # RDS instance — neither exists here (no NAT, no VPN). The extensions are
  # installed by the in-VPC bootstrap Lambda instead (postgis-bootstrap.tf).
  enable_postgis = false

  # Run Honua's own schema migrations on startup for the initial deploy.
  # After the first successful boot this can be flipped back to true
  # (the serverless default) so cold starts skip the DbUp journal check.
  skip_migrations = true # runtime never migrates: concurrent cold starts each running migrations crash-looped the demo (2026-06-12); run migrations as a one-off job (set false only for a single first boot)

  # Let the in-VPC PostGIS bootstrap Lambda (its own security group) reach
  # PostgreSQL. The VPC is dedicated to this stack, so the VPC CIDR is the
  # tightest practical bound.
  vpc_cidr                    = local.vpc_cidr
  db_additional_ingress_cidrs = [local.vpc_cidr]

  # Redis — gated on var.enable_redis (default off). honua-server hard-requires
  # a durable distributed feature-change event store whenever
  # ASPNETCORE_ENVIRONMENT=Production, so /healthz/ready returns 503 without it
  # (see README → "Known limitation"). When enabled the module creates an in-VPC
  # ElastiCache Redis (cache.t3.micro by default) and — critically — adds the
  # Lambda's 6379 egress rule as a CIDR rule (the VPC CIDR), NOT a
  # security-group-reference rule. A SG-reference egress rule did NOT work in
  # this VPC; the module already does the right thing because redis_create wires
  # redis_egress_cidrs = [vpc_cidr]. The connection string lands in a dedicated
  # Secrets Manager secret and is injected as ConnectionStrings__redis.
  redis_enabled   = var.enable_redis
  redis_node_type = var.redis_node_type
  redis_port      = 6379

  # ---- Pro license (Secrets Manager) — gated on var.enable_pro_license -------
  # ADOPT-BY-ARN. The signed envelope is staged out-of-band in
  # honua-demo-demo/license-pro; Terraform is given only that secret's ARN, so it
  # creates no secret and no secret version and never reads the envelope. All it
  # does is inject Licensing__LicenseContentSecretRef=aws:secretsmanager:<arn> +
  # Licensing__TrustedKeys__<key_id> and grant the Lambda role GetSecretValue on
  # that ARN. Consequently there is no terraform.tfvars holding a signed license,
  # no envelope in state or in a plan file, and no `terraform import` step — and
  # a future `enable_pro_license = false` cannot delete the envelope, because the
  # secret was never in state to begin with.
  #
  # pro_license_content is forwarded but expected to stay empty. It is mutually
  # exclusive with pro_license_secret_arn, so setting it while the ARN default is
  # in place trips the module's precondition and fails the PLAN — loudly, which is
  # the point. Forwarding it (rather than dropping it) is what makes that failure
  # loud: an unforwarded variable would be silently ignored, and a signed envelope
  # quietly doing nothing in tfvars is precisely the failure mode this wiring
  # exists to prevent. To use Terraform-managed content instead, set
  # pro_license_secret_arn = "" and supply pro_license_content.
  enable_pro_license             = var.enable_pro_license
  pro_license_secret_arn         = var.pro_license_secret_arn
  pro_license_content            = var.pro_license_content
  pro_license_key_id             = var.pro_license_key_id
  pro_license_trusted_public_key = var.pro_license_trusted_public_key

  # ---- Bedrock AI (WorkflowGeneration) — gated on var.enable_bedrock_ai ------
  # Grants the Lambda role least-privilege bedrock:InvokeModel /
  # InvokeModelWithResponseStream scoped to the configured Claude model and
  # routes the AI studio (WorkflowGeneration) to Amazon Bedrock in us-west-2.
  # The no-NAT VPC reaches Bedrock through the bedrock-runtime interface VPC
  # endpoint provisioned in vpc-endpoints.tf (also gated on enable_bedrock_ai).
  enable_bedrock_ai = var.enable_bedrock_ai
  bedrock_ai_model  = var.bedrock_ai_model
  bedrock_ai_region = var.bedrock_ai_region

  # ---- Geocoding on Amazon Location — gated on var.enable_amazon_location_geocoding
  # Replaces Nominatim (unreachable from this no-NAT VPC, honua-server#2948) with
  # the server's built-in amazon-location provider against a place index reached
  # through the `geo` VPC interface endpoint provisioned in vpc-endpoints.tf.
  enable_amazon_location_geocoding = var.enable_amazon_location_geocoding
  amazon_location_place_index_name = var.amazon_location_place_index_name
  amazon_location_data_source      = var.amazon_location_data_source

  # Networking — no NAT gateway (~$33/mo + data saved). The public demo has
  # no OIDC and needs no general internet egress; the only AWS services the
  # Lambda needs at runtime are reached via VPC endpoints instead
  # (Secrets Manager interface endpoint + S3 gateway endpoint, see
  # vpc-endpoints.tf).
  enable_nat_gateway = false

  # API Gateway throttling (replaces WAF; HTTP API does not support WAFv2)
  api_throttle_burst_limit = var.api_throttle_burst_limit
  api_throttle_rate_limit  = var.api_throttle_rate_limit

  # Log retention — shorter for demo to contain cost
  log_retention_days = 90

  # GP over AWS Batch (Fargate Spot) — off unless var.enable_gp_batch is set.
  # Scales to zero between jobs; pay only for the seconds a job's container runs.
  # Architecture matches the demo's x86_64 image (see lambda_architectures note
  # above). The GP job role gets read/write on the demo data bucket so imports
  # can stage to S3 the same way the Lambda does.
  enable_gp_batch              = var.enable_gp_batch
  gp_batch_image               = var.gp_batch_image
  gp_batch_cpu_architecture    = "X86_64"
  gp_batch_data_bucket_arn     = aws_s3_bucket.demo_data.arn
  gp_batch_data_bucket_enabled = true

  # Demo-specific environment variables
  additional_env = {
    HONUA_SERVE_API_DOCS          = "true"
    HONUA_SERVE_STAC_DEMO         = "true"
    MultiTenancy__Enabled         = "true"
    MultiTenancy__DefaultTenantId = "public"
    # Allow the API Gateway custom domain as a valid host
    HostValidation__AllowedHosts__1 = "demo.honua.io"

    # Advertised absolute URLs (2026-06-12). Two lanes, both required:
    #
    # 1. Public__BaseUrl — BaseUrlResolver (src/Honua.Hosting/Features/
    #    Helpers/BaseUrlResolver.cs) NEVER trusts request Host headers for
    #    link generation; without an explicit public base URL it derives a
    #    local origin, so /rest/services self-links advertised
    #    `localhost:8080` (the Lambda Web Adapter binding).
    # 2. ForwardedHeaders__Enabled — surfaces honoring Request.Host/Scheme
    #    (@odata.context et al.) need X-Forwarded-Host/Proto applied; the
    #    CloudFront viewer-request function in cloudfront.tf injects
    #    `X-Forwarded-Host: demo.honua.io` because the origin is the
    #    execute-api endpoint and the viewer Host can never pass through
    #    (API Gateway routes by Host).
    Public__BaseUrl           = "https://demo.honua.io"
    ForwardedHeaders__Enabled = "true"

    # FileStorage on S3 (see seed-data.tf): import staging for >10 MB vector
    # uploads and the PMTiles range proxy both resolve against this bucket.
    # Credentials are intentionally omitted so the AWS SDK falls back to the
    # Lambda execution role.
    FileStorage__Provider              = "AwsS3"
    FileStorage__AwsS3__BucketName     = local.data_bucket_name
    FileStorage__AwsS3__Region         = var.region
    FileStorage__AwsS3__ForcePathStyle = "false"
    # The demo contract serves the basemap at /api/v1/tiles/pmtiles/maui-basemap,
    # i.e. the artifact lives at the bucket root. "/" normalizes to an empty
    # publish prefix so the proxy accepts root-level artifact keys.
    FileStorage__PMTilesPublish__KeyPrefix = "/"

    # Request budget pairs with lambda_timeout_seconds above. Raise both to
    # 10 minutes temporarily for bulk synchronous re-seeds (county parcels
    # needs it); steady-state keep them tight so orphaned tile queries get
    # cancelled instead of starving db.t4g.micro for minutes after a burst.
    Limits__Connections__RequestTimeout = "00:01:00"

    # Application-level CORS so https://honua.io/demo.html can call
    # /rest/*, /ogc/*, and /api/v1/tiles/* (the /fonts route gets its CORS
    # header from the API Gateway integration mapping in seed-data.tf).
    Cors__AllowedOrigins__0 = "https://honua.io"
    Cors__AllowedOrigins__1 = "https://www.honua.io"
    # Local demo.html development/verification (python -m http.server 8123
    # in honua-site) — harmless for a public-data demo server.
    Cors__AllowedOrigins__2 = "http://localhost:8123"
    Cors__AllowCredentials  = "false"
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Custom domain — demo.honua.io
# ACM certificate, API Gateway custom domain, and Route53 record.
# The aws-serverless module does not manage custom domains; we provision them
# here, consistent with how the module exposes api_endpoint for the stage.
# ---------------------------------------------------------------------------

resource "aws_acm_certificate" "demo" {
  domain_name       = "demo.honua.io"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.common_tags
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.demo.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

resource "aws_acm_certificate_validation" "demo" {
  certificate_arn         = aws_acm_certificate.demo.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

resource "aws_apigatewayv2_domain_name" "demo" {
  domain_name = "demo.honua.io"

  domain_name_configuration {
    certificate_arn = aws_acm_certificate_validation.demo.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }

  tags = local.common_tags
}

# Map the $default stage of the module's HTTP API to the custom domain.
# The module exposes the API endpoint URL; extract the API ID from it.
# api_endpoint format: https://<api-id>.execute-api.<region>.amazonaws.com
locals {
  api_id = regex("https://([^.]+)\\.execute-api", module.honua.api_endpoint)[0]
}

resource "aws_apigatewayv2_api_mapping" "demo" {
  api_id      = local.api_id
  domain_name = aws_apigatewayv2_domain_name.demo.id
  stage       = "$default"
}

# Alias target switches between the CloudFront distribution (steady state)
# and the API Gateway custom domain (pre-CDN validation) — see cloudfront.tf
# for the locals and the DNS swap sequencing notes.
resource "aws_route53_record" "demo" {
  name    = "demo.honua.io"
  type    = "A"
  zone_id = var.route53_zone_id

  alias {
    name                   = local.demo_alias_name
    zone_id                = local.demo_alias_zone
    evaluate_target_health = false
  }
}
