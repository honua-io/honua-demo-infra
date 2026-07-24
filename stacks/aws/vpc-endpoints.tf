###############################################################################
# VPC endpoints — replace the NAT gateway for the demo's AWS-service traffic.
#
# The demo runs with enable_nat_gateway = false (no general egress needed —
# no OIDC, no outbound integrations). The Lambda still resolves
# `aws:secretsmanager:` environment references at runtime, so Secrets Manager
# must stay reachable from the private subnets:
#
#   - Secrets Manager INTERFACE endpoint (~$7.30/mo + per-GB). Single-AZ on
#     purpose: one ENI is enough for demo traffic; cross-AZ hops from the
#     other private subnets are fine.
#   - S3 GATEWAY endpoint (free) on the private route tables, for future COG
#     (Cloud-Optimized GeoTIFF) serving from S3.
#
# When the AI demo is on (enable_bedrock_ai = true) the server calls Amazon
# Bedrock in us-west-2. With no NAT and no default route, the only path to the
# Bedrock runtime is a bedrock-runtime INTERFACE endpoint in this VPC — added
# below, gated on enable_bedrock_ai.
###############################################################################

resource "aws_security_group" "vpc_endpoints" {
  #checkov:skip=CKV2_AWS_5: Attached to the Secrets Manager interface endpoint below.
  name_prefix = "${var.name_prefix}-${var.environment}-vpce-"
  description = "Allow HTTPS to interface VPC endpoints from inside the VPC"
  vpc_id      = module.honua.vpc_id

  ingress {
    description = "HTTPS from the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = module.honua.vpc_id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [module.honua.private_subnet_ids[0]] # single AZ to save cost
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-secretsmanager" })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.honua.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.honua.private_route_table_ids

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-s3" })
}

# ---------------------------------------------------------------------------
# Bedrock runtime interface endpoint — only when the AI demo is enabled.
#
# This no-NAT VPC has no default route, so the Lambda cannot reach the public
# Bedrock runtime endpoint. com.amazonaws.<region>.bedrock-runtime as an
# interface endpoint (with private DNS) makes bedrock-runtime.<region>.
# amazonaws.com resolve to in-VPC ENIs. bedrock_ai_region must equal var.region
# (the VPC's region) — an interface endpoint can only front a service in its own
# region, and the WorkflowGeneration provider invokes Bedrock in that same
# region. Live id: vpce-003090af73dc835fe (SG sg-0ac55474b410c5d34) — adopt both
# with `terraform import` (see README → "Import the Bedrock VPC endpoint").
# ---------------------------------------------------------------------------

resource "aws_security_group" "bedrock_endpoint" {
  #checkov:skip=CKV2_AWS_5: Attached to the bedrock-runtime interface endpoint below.
  count       = var.enable_bedrock_ai ? 1 : 0
  name_prefix = "${var.name_prefix}-${var.environment}-bedrock-vpce-"
  description = "Allow HTTPS to the Bedrock runtime interface VPC endpoint from inside the VPC"
  vpc_id      = module.honua.vpc_id

  ingress {
    description = "HTTPS from the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint" "bedrock_runtime" {
  count               = var.enable_bedrock_ai ? 1 : 0
  vpc_id              = module.honua.vpc_id
  service_name        = "com.amazonaws.${var.bedrock_ai_region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [module.honua.private_subnet_ids[0]] # single AZ to save cost
  security_group_ids  = [aws_security_group.bedrock_endpoint[0].id]
  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-bedrock-runtime" })
}

# ---------------------------------------------------------------------------
# Amazon Location (geo) interface endpoint — only when Amazon Location
# geocoding is enabled (honua-server#2948).
#
# Same rationale as the Secrets Manager / Bedrock endpoints above: this no-NAT
# VPC has no default route, so the Lambda cannot reach the public
# geo.<region>.amazonaws.com endpoint for the Amazon Location Places API
# (SearchPlaceIndexForText/ForPosition/ForSuggestions, DescribePlaceIndex) that
# AmazonLocationGeocodeProvider calls. com.amazonaws.<region>.geo as an
# interface endpoint (with private DNS) makes that hostname resolve to in-VPC
# ENIs instead. var.region is used directly (not a separate
# amazon_location_region variable) — a VPC interface endpoint can only front a
# service in its own region, and the place index the aws-serverless module
# creates lives in this same region (the module resolves its region from the
# "aws" provider, i.e. var.region), so there is no other valid choice.
#
# NOTE (read-only AWS audit, 2026-07-23): the already-live bedrock-runtime
# endpoint above is documented here and in main.tf as "single-AZ to save
# cost", but `aws ec2 describe-vpc-endpoints` shows the deployed
# vpce-003090af73dc835fe actually spans all three private subnets/AZs — live
# drift from what this file's `subnet_ids = [module.honua.private_subnet_ids[0]]`
# describes. (The account's other interface endpoints for
# com.amazonaws.us-west-2.lambda/.sts/.logs/.monitoring, also observed
# spanning all three private subnets during the same audit, are NOT drift —
# they are the module's own deploy-control.tf, gated behind
# enable_control_plane_events, which intentionally uses all of
# local.private_subnets rather than a single AZ.) This new `geo` endpoint
# follows the single-AZ pattern as WRITTEN (matching the Secrets Manager
# endpoint and the cost this PR documents), not the bedrock endpoint's
# drifted live shape. Flagging the bedrock discrepancy for a maintainer to
# reconcile separately — not touched by this change.
# ---------------------------------------------------------------------------

resource "aws_security_group" "amazon_location_endpoint" {
  #checkov:skip=CKV2_AWS_5: Attached to the Amazon Location (geo) interface endpoint below.
  count       = var.enable_amazon_location_geocoding ? 1 : 0
  name_prefix = "${var.name_prefix}-${var.environment}-geo-vpce-"
  description = "Allow HTTPS to the Amazon Location (geo) interface VPC endpoint from inside the VPC"
  vpc_id      = module.honua.vpc_id

  ingress {
    description = "HTTPS from the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint" "geo" {
  count               = var.enable_amazon_location_geocoding ? 1 : 0
  vpc_id              = module.honua.vpc_id
  service_name        = "com.amazonaws.${var.region}.geo"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [module.honua.private_subnet_ids[0]] # single AZ to save cost, matches the Secrets Manager endpoint's pattern
  security_group_ids  = [aws_security_group.amazon_location_endpoint[0].id]
  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-geo" })
}
