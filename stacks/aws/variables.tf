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

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.small is the reliability floor for the public demo: on 2026-07-31 db.t4g.micro exhausted usable PostgreSQL connection slots at only 16 concurrent Lambda environments (72 reported database connections), returning HTTP 500 during the real Console browser journey. Keep the Lambda pool and reserved-concurrency budget in main.tf aligned with this size."
  type        = string
  default     = "db.t4g.small"
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
# bedrock:InvokeModel for the configured Claude model. Bedrock is reached via
# the fck-nat egress (nat-instance.tf).
# ---------------------------------------------------------------------------

variable "enable_bedrock_ai" {
  description = "Grant the demo Lambda role bedrock:InvokeModel / InvokeModelWithResponseStream for the configured Claude model and route the AI studio (WorkflowGeneration) to Amazon Bedrock, reached via the fck-nat egress (nat-instance.tf; the bedrock-runtime interface endpoint was removed in the 2026-07 cost round). Off by default."
  type        = bool
  default     = false
}

variable "bedrock_ai_model" {
  description = "Bedrock model id the server's WorkflowGeneration uses. Defaults to the cross-region Claude Sonnet 4.5 inference profile (the `us.` prefix routes across us-east-1/us-east-2/us-west-2). The IAM grant is scoped to this model's inference-profile + foundation-model ARNs."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "bedrock_ai_region" {
  description = "AWS region the server invokes Bedrock in (WorkflowGeneration provider Region). Defaults to us-west-2 — keep it equal to var.region (the demo VPC's region) so invocations stay region-local through the NAT egress."
  type        = string
  default     = "us-west-2"
}

# ---------------------------------------------------------------------------
# Studio AI proxy — live Honua Studio AI generation via Amazon Bedrock
# (honua-server#3000; demo enablement tracked in #9). Off by default.
# Distinct from enable_bedrock_ai (WorkflowGeneration): both reach the
# Bedrock runtime via the fck-nat egress (nat-instance.tf), but each has its
# own toggle, model, and IAM grant so they can be tuned and rolled
# independently.
# ---------------------------------------------------------------------------

variable "enable_studio_ai" {
  description = "Wire the demo Lambda for live Studio AI generation via Amazon Bedrock: grant the Lambda role bedrock:InvokeModel / InvokeModelWithResponseStream scoped to studio_ai_model's (and studio_ai_fallback_models') inference-profile + foundation-model ARNs and inject the StudioAiProxy__* env (kind=bedrock). Bedrock is reached via the fck-nat egress (nat-instance.tf). Off by default."
  type        = bool
  default     = false
}

variable "studio_ai_model" {
  description = "Bedrock model id the server's Studio AI proxy uses. Defaults to the cross-region Claude Opus 5 inference profile (the `us.` prefix routes across us-east-1/us-east-2/us-west-2; profile verified ACTIVE in this account 2026-07-24). The account's foundation-model agreement for anthropic.claude-opus-5 was accepted the same day; runtime entitlement can lag the agreement — run the runbook's converse smoke before a rehearsal, and if it still returns AccessDenied flip this to a studio_ai_fallback_models entry (no IAM change needed). The IAM grant covers this model plus studio_ai_fallback_models."
  type        = string
  default     = "us.anthropic.claude-opus-5"
}

variable "studio_ai_fallback_models" {
  description = "Additional Bedrock model ids the Studio AI IAM grant ALSO covers (inference-profile + foundation-model ARNs), so studio_ai_model can be flipped between them with a plain env change and no IAM edit. Defaults to the cross-region Claude Sonnet 4.6 profile — verified invocable end-to-end in this account (2026-07-24, us-east-1 and us-west-2) and the rehearsal fallback while the Opus 5 entitlement propagates."
  type        = list(string)
  default     = ["us.anthropic.claude-sonnet-4-6"]
}

variable "studio_ai_region" {
  description = "AWS region the server invokes Bedrock in for the Studio AI proxy (StudioAiProxy provider Region). Defaults to us-west-2, matching bedrock_ai_region — keep it equal to var.region so invocations stay region-local through the NAT egress."
  type        = string
  default     = "us-west-2"
}

# ---------------------------------------------------------------------------
# Geocoding on Amazon Location Service — replaced the Nominatim provider,
# which the then-egress-less VPC could not reach (honua-server#2948: every
# geocode call failed after a consistent ~15.8s outbound-connect timeout).
# Off by default. When enabled, provisions an Amazon Location place index +
# Lambda IAM grant (via the aws-serverless module); the service is reached
# via the fck-nat egress (nat-instance.tf; the geo.places interface endpoint
# was removed in the 2026-07 cost round).
# ---------------------------------------------------------------------------

variable "enable_amazon_location_geocoding" {
  description = "Provision an Amazon Location place index, grant the Lambda role geo:Search*/DescribePlaceIndex on it, and route Geocoding__DefaultProvider to amazon-location (Nominatim disabled). Amazon Location is reached via the fck-nat egress (nat-instance.tf). Off by default."
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

# ---------------------------------------------------------------------------
# Cost controls — fck-nat NAT instance (nat-instance.tf) and the monthly AWS
# Budget alarm (cost-controls.tf). The NAT instance replaced the interface
# VPC endpoints (~$110/mo of ENI-hours) in 2026-07; see nat-instance.tf for
# the architecture and the accepted single-AZ SPOF.
# ---------------------------------------------------------------------------

variable "nat_instance_type" {
  description = "Instance type for the fck-nat NAT instance. t4g.nano (~$3.1/mo) sustains far more throughput than demo traffic needs; bump to t4g.micro/small only if NAT becomes a measured bottleneck."
  type        = string
  default     = "t4g.nano"
}

variable "monthly_budget_amount" {
  description = "Monthly AWS Budget limit in USD. Alerts fire at 100% (actual + forecasted) and again at 200% (actual + forecasted) of this amount."
  type        = number
  default     = 150
}

variable "budget_notification_email" {
  description = "Email address subscribed to the monthly budget notifications. Defaults to the demo ops contact."
  type        = string
  default     = "mike@honua.io"
}

variable "stac_seed_metadata_environment" {
  description = "Exact Metadata v2 environment served by the deployed Lambda (Metadata__Environment, Environment, or the active metadata_v2_current row). Required for the managed STAC seed; never infer it from ASPNETCORE_ENVIRONMENT."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.stac_seed_metadata_environment))
    error_message = "stac_seed_metadata_environment must be a non-empty Metadata v2 environment identifier."
  }
}

# ---------------------------------------------------------------------------
# Feature streaming (streaming.tf) — the snapshot payload budget that keeps a
# baseline deliverable through the buffered ~6 MB gateway response, and the
# controlled-conformance mutation surface (off until a dedicated source is
# provisioned). See streaming.tf for why the server default is not sufficient
# here, and runbook/streaming-snapshot-conformance.md for the enablement
# procedure.
# ---------------------------------------------------------------------------

variable "streaming_max_snapshot_bytes" {
  description = "FeatureStreaming__MaxSnapshotBytes — byte budget for ONE baseline snapshot. Must stay below the gateway's buffered-response ceiling (~6 MB) WITH headroom, because snapshot-then-delta keeps appending delta frames to the same response. 2 MiB leaves ~4 MB of headroom; the server default (4 MiB) does not and can still produce a gateway-manufactured untyped 500."
  type        = number
  default     = 2097152

  validation {
    condition     = var.streaming_max_snapshot_bytes > 0 && var.streaming_max_snapshot_bytes <= 4194304
    error_message = "streaming_max_snapshot_bytes must be positive and at most 4194304 (4 MiB, the server default) — a larger budget leaves no headroom under the ~6 MB buffered-response ceiling."
  }
}

variable "streaming_conformance_enabled" {
  description = "Enable the controlled-conformance mutation surface (honua-server#3038 REQ-005) so a scheduled SDK evidence run can drive one correlated mutation and observe it on every advertised transport. Off by default: turning it on without a dedicated conformance source lets a bounded write land in a real demo layer. Requires streaming_conformance_service_id/layer_id and an operator-issued credential for the admin-scoped ConformanceMutate policy."
  type        = bool
  default     = false
}

variable "streaming_conformance_service_id" {
  description = "Service id of the DEDICATED conformance source. Required when streaming_conformance_enabled. Must be a small layer whose baseline completes — a truncated baseline is fail-closed and ends the stream, so a large layer can never stay open for the correlated mutation. Never point this at a layer the demo page serves."
  type        = string
  default     = ""

  validation {
    condition     = !var.streaming_conformance_enabled || trimspace(var.streaming_conformance_service_id) != ""
    error_message = "streaming_conformance_service_id is required when streaming_conformance_enabled is true; a typo must fail the plan rather than silently resolve to a shared demo service."
  }
}

variable "streaming_conformance_layer_id" {
  description = "Layer id within streaming_conformance_service_id that controlled records are written to. The layer must carry the run-ownership columns the server writes (conformance_run_id, and conformance_label when labels are used)."
  type        = number
  default     = 0

  validation {
    condition     = !var.streaming_conformance_enabled || var.streaming_conformance_layer_id > 0
    error_message = "streaming_conformance_layer_id must identify a real layer when streaming_conformance_enabled is true."
  }
}
