###############################################################################
# VPC endpoints — only the FREE S3 gateway endpoint remains.
#
# History: the demo VPC was originally built no-NAT, with every AWS service
# the Lambda needs reached through interface VPC endpoints (Secrets Manager,
# bedrock-runtime, geo.places here; lambda/sts/monitoring/logs inside the
# module's deploy-control.tf). That architecture quietly cost ~ $110/mo in
# endpoint ENI-hours alone. It was replaced by a single fck-nat instance
# (nat-instance.tf, ~$7.5/mo) that gives the private subnets general egress —
# so all interface endpoints were REMOVED (this file, 2026-07-24) and the
# module's deploy-control endpoints are disabled via
# enable_deploy_control_vpc_endpoints = false in main.tf. Applying that
# removal destroys the live endpoints and their SGs — that is the point;
# the NAT default route must be in place in the same apply (it is: both are
# in this stack).
#
# The S3 GATEWAY endpoint stays: it is free, and it keeps S3 traffic (PMTiles
# range reads, import staging, future COG serving) off the NAT instance —
# both for throughput and to avoid NAT data-processing charges on tile bytes.
###############################################################################

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.honua.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.honua.private_route_table_ids

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-s3" })
}
