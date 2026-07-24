variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used in resource naming."
  type        = string
  default     = "demo"
}

variable "name_prefix" {
  description = "Prefix used for resource names."
  type        = string
  default     = "honua-demo"
}

variable "honua_image" {
  description = "Lambda container image URI (ECR). Must be a *-lambda-aot tag for AOT performance. Pin to an immutable release tag or digest."
  type        = string
}

variable "honua_admin_password" {
  description = "Admin API password for Honua (minimum 32 characters; also used as Security__ConnectionEncryption__MasterKey)."
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "PostgreSQL admin password. Leave null to auto-generate."
  type        = string
  sensitive   = true
  default     = null
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for demo.honua.io. Required — see DNS prerequisites in README."
  type        = string
}

variable "lambda_memory_size" {
  description = "Lambda memory in MB. 2048 matches the live function (raised out-of-band from 1024 during seeding; Lambda CPU scales with memory and tile rendering is CPU-bound — encoded 2026-06-12 so applies stop reverting it)."
  type        = number
  default     = 2048
}

variable "api_throttle_burst_limit" {
  description = "API Gateway burst throttle limit (max concurrent requests). Replaces WAF rate-limiting — HTTP API does not support WAFv2."
  type        = number
  default     = 200
}

variable "api_throttle_rate_limit" {
  description = "API Gateway steady-state throttle limit (requests per second). Conservative default for a public demo."
  type        = number
  default     = 50
}

variable "route_demo_dns_to_cloudfront" {
  description = "Point the demo.honua.io A/AAAA alias at the CloudFront distribution (true, steady state) or directly at the API Gateway custom domain (false — used to validate a fresh distribution via its *.cloudfront.net domain before swapping DNS)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "enable_gp_batch" {
  description = "Provision the AWS Batch (Fargate Spot) backend for the demo's geoprocessing/import jobs (the 'GP over Batch' beat). Off by default; flip to true to demo it. Scales to zero between jobs — pay-per-job only."
  type        = bool
  default     = false
}

variable "gp_batch_image" {
  description = "ECR image URI for the geoprocessing Batch worker. Leave empty to reuse honua_image (single image, branches on HONUA_JOB_KIND)."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Redis — in-VPC ElastiCache for the Production feature-change event store.
# Off by default (matches the original demo). When enabled, the aws-serverless
# module creates the cluster and wires the Lambda 6379 egress as a CIDR rule
# (the VPC CIDR) — a SG-reference egress rule did NOT work in this VPC.
# ---------------------------------------------------------------------------

variable "enable_redis" {
  description = "Provision an in-VPC ElastiCache Redis (honua-demo-demo-redis) and inject ConnectionStrings__redis. honua-server requires a durable feature-change event store in Production; without Redis /healthz/ready returns 503. The Lambda's 6379 egress rule is added as a CIDR rule (the VPC CIDR) — a SG-reference egress rule does not work in this VPC."
  type        = bool
  default     = false
}

variable "redis_node_type" {
  description = "ElastiCache node type. The live demo runs cache.t3.micro."
  type        = string
  default     = "cache.t3.micro"
}

# ---------------------------------------------------------------------------
# Pro license — signed envelope delivered via Secrets Manager.
#
# The envelope's VALUE is managed ENTIRELY OUTSIDE TERRAFORM. It is already
# staged in the demo account at honua-demo-demo/license-pro, and this example
# adopts that secret by ARN (pro_license_secret_arn): Terraform never receives,
# reads, writes, or stores the envelope, and there is no terraform.tfvars
# holding a signed license. Enabling Pro is therefore just
# `enable_pro_license = true` — no secret material on any workstation.
#
# The trusted PUBLIC key is NOT a secret: it only verifies a signature and
# cannot mint a license. It is a plain default below, which is what makes the
# "no tfvars" flow possible. The corresponding PRIVATE signing seed lives in
# honua-demo-demo/license-signing-key and must never be read by this config.
# ---------------------------------------------------------------------------

variable "enable_pro_license" {
  description = "Deliver the signed Pro license to the demo Lambda via Secrets Manager. When off the server runs Community. The envelope itself is staged out-of-band and adopted via pro_license_secret_arn, so enabling Pro needs no secret material in tfvars."
  type        = bool
  default     = false
}

# Default: the live, out-of-band-staged demo envelope. Terraform only ever
# learns this ARN — never the envelope behind it.
variable "pro_license_secret_arn" {
  description = "ARN of the EXISTING Secrets Manager secret holding the demo's signed Pro license envelope, managed outside Terraform. The module creates no secret and no version for it; it only injects Licensing__LicenseContentSecretRef and grants the Lambda role GetSecretValue on this ARN."
  type        = string
  default     = "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/license-pro-53rTHr"
}

# ESCAPE HATCH — leave empty. Setting this while pro_license_secret_arn still
# holds its default trips the module's mutual-exclusion precondition and fails
# the plan by design. To hand Terraform the envelope instead, blank the ARN:
#   pro_license_secret_arn = ""
#   pro_license_content    = "<envelope json>"   # then it IS in tfvars + state
variable "pro_license_content" {
  description = "ESCAPE HATCH — leave empty. The demo's envelope is staged out-of-band and adopted via pro_license_secret_arn; supplying it here puts a signed license into tfvars and state, which is what the adopt-by-ARN flow exists to avoid. Mutually exclusive with pro_license_secret_arn: setting both fails the plan. Use only by also setting pro_license_secret_arn = \"\"."
  type        = string
  default     = ""
  sensitive   = true
}

variable "pro_license_key_id" {
  description = "The license signing keyId as relabeled in the envelope (hyphen-free so it is a legal Lambda env var name segment: Licensing__TrustedKeys__<keyId>). Must match the staged envelope's keyId exactly or the server falls back to Community."
  type        = string
  default     = "honuademo2026q2"
}

# NOT sensitive: a public key verifies a signature, it cannot create one. Marking
# it sensitive would only redact it from plan output while protecting nothing.
variable "pro_license_trusted_public_key" {
  description = "The Ed25519 PUBLIC key (base64url, with the base64url: prefix) that verifies the Pro license signature. Injected as Licensing__TrustedKeys__<pro_license_key_id>. Defaults to the demo's published key — safe in config; it is not secret."
  type        = string
  default     = "base64url:Y2XgDBncW5w6n7L3YG-T6HxX51DGybWazt0_gubk30k"
}

# ---------------------------------------------------------------------------
# Bedrock AI — WorkflowGeneration via Amazon Bedrock (us-west-2).
# Off by default. When enabled, grants the Lambda role least-privilege
# bedrock:InvokeModel for the configured Claude model and provisions the
# bedrock-runtime VPC interface endpoint this no-NAT VPC needs (vpc-endpoints.tf).
# ---------------------------------------------------------------------------

variable "enable_bedrock_ai" {
  description = "Grant the demo Lambda role bedrock:InvokeModel / InvokeModelWithResponseStream for the configured Claude model, route the AI studio (WorkflowGeneration) to Amazon Bedrock, and provision the bedrock-runtime VPC interface endpoint (this no-NAT VPC has no other egress path to Bedrock). Off by default."
  type        = bool
  default     = false
}

variable "bedrock_ai_model" {
  description = "Bedrock model id the server's WorkflowGeneration uses. Defaults to the cross-region Claude Sonnet 4.5 inference profile (the `us.` prefix routes across us-east-1/us-east-2/us-west-2). The IAM grant is scoped to this model's inference-profile + foundation-model ARNs."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "bedrock_ai_region" {
  description = "AWS region the server invokes Bedrock in (WorkflowGeneration provider Region) and where the bedrock-runtime VPC interface endpoint is created. Defaults to us-west-2 — keep it equal to var.region (the demo VPC's region) so the interface endpoint resolves."
  type        = string
  default     = "us-west-2"
}

# ---------------------------------------------------------------------------
# Geocoding on Amazon Location Service — replaces the Nominatim provider,
# which this no-NAT VPC cannot reach (honua-server#2948: every geocode call
# failed after a consistent ~15.8s outbound-connect timeout — a categorical
# network-egress problem, not a cold-start one). Off by default. When
# enabled, provisions an Amazon Location place index + Lambda IAM grant (via
# the aws-serverless module) and the `com.amazonaws.<region>.geo` VPC
# interface endpoint this no-NAT VPC needs to reach it (vpc-endpoints.tf).
# ---------------------------------------------------------------------------

variable "enable_amazon_location_geocoding" {
  description = "Provision an Amazon Location place index, grant the Lambda role geo:Search*/DescribePlaceIndex on it, route Geocoding__DefaultProvider to amazon-location (Nominatim disabled), and provision the geo VPC interface endpoint this no-NAT VPC needs to reach Amazon Location. Off by default."
  type        = bool
  default     = false
}

variable "amazon_location_place_index_name" {
  description = "Name of the Amazon Location place index. Defaults to '<name_prefix>-<environment>-geocode' when empty."
  type        = string
  default     = ""
}

variable "amazon_location_data_source" {
  description = "Upstream data provider for the Amazon Location place index: Esri or Here (not OpenStreetMap/Nominatim — this is a full provider swap with different coverage/attribution)."
  type        = string
  default     = "Esri"
}
