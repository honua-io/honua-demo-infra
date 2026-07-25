###############################################################################
# fck-nat NAT instance — cheap general egress for the private subnets.
#
# Replaces the interface-VPC-endpoint architecture. The demo VPC was built
# no-NAT, with every AWS service the Lambda needs reached through interface
# endpoints instead — which quietly became the dominant fixed cost:
#
#   - module deploy-control endpoints (lambda/sts/monitoring/logs, 3 AZs
#     each = 12 ENIs)                                      ~ $88/mo
#   - demo endpoints (secretsmanager, bedrock-runtime, geo.places,
#     single-AZ)                                           ~ $22/mo
#
# A single fck-nat instance (https://fck-nat.dev — a purpose-built minimal
# NAT AMI maintained by the fck-nat project) on a t4g.nano provides the same
# reachability for ~ $7.5/mo all-in (~$3.1 instance + ~$0.7 EBS + ~$3.7
# public IPv4), and additionally restores true internet egress (OIDC,
# webhooks, Nominatim, outbound integrations) that the endpoint architecture
# never had. The interface endpoints are removed (vpc-endpoints.tf keeps only
# the free S3 gateway endpoint, which also bypasses NAT data charges for S3);
# the module's deploy-control endpoints are disabled via
# enable_deploy_control_vpc_endpoints = false in main.tf.
#
# SPOF — accepted for a demo: one instance in one AZ. If the instance (or its
# AZ) dies, private-subnet egress is down until it is replaced — the public
# API path (CloudFront -> API Gateway -> Lambda invoke) stays up, but the
# Lambda loses Secrets Manager / Bedrock / Amazon Location / deploy-control
# reachability. Recovery is `terraform apply -replace=aws_instance.nat`
# (~2 min) — see the runbook ("Cost & network architecture"). Scale up by
# setting nat_instance_type (t4g.nano sustains ~5 Gbps burst, far beyond demo
# traffic).
###############################################################################

# Latest fck-nat Amazon Linux 2023 arm64 AMI, published by the fck-nat
# project's AWS account (568608671756 — the id documented at fck-nat.dev).
data "aws_ami" "fck_nat" {
  most_recent = true
  owners      = ["568608671756"]

  filter {
    name   = "name"
    values = ["fck-nat-al2023-hvm-*-arm64-ebs"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

# The aws-serverless module does not output its public subnet ids, so resolve
# them by the terraform-aws-modules/vpc naming convention
# (<name_prefix>-<environment>-vpc-public-<az>). Live example:
# honua-demo-demo-vpc-public-us-west-2a.
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [module.honua.vpc_id]
  }

  filter {
    name   = "tag:Name"
    values = ["${var.name_prefix}-${var.environment}-vpc-public-*"]
  }
}

resource "aws_security_group" "nat_instance" {
  name_prefix = "${var.name_prefix}-${var.environment}-nat-"
  description = "fck-nat instance: forward anything from inside the VPC out to the internet"
  vpc_id      = module.honua.vpc_id

  # NAT must forward whatever the private subnets send it, so ingress is
  # all-protocol — but only from inside the VPC.
  ingress {
    description = "All traffic from the VPC (traffic being NATed)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "All outbound (the NATed traffic itself)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-nat" })
}

resource "aws_instance" "nat" {
  ami                    = data.aws_ami.fck_nat.id
  instance_type          = var.nat_instance_type
  subnet_id              = sort(data.aws_subnets.public.ids)[0] # single AZ — accepted demo SPOF (header note)
  vpc_security_group_ids = [aws_security_group.nat_instance.id]

  # A NAT device forwards packets that are not addressed to itself.
  source_dest_check = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
  }

  root_block_device {
    volume_type = "gp3"
    encrypted   = true
  }

  lifecycle {
    # A new fck-nat AMI release should not silently propose replacing the
    # live NAT (and a blip of egress downtime) on an unrelated apply. Take
    # AMI updates deliberately: terraform apply -replace=aws_instance.nat.
    ignore_changes = [ami]
  }

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-nat" })
}

# Stable public IP: the module VPC's public subnets have map_public_ip_on_launch
# disabled, and an EIP survives instance replacement anyway. (~$3.7/mo — all
# public IPv4 addresses are billed since 2024, EIP or auto-assigned alike.)
resource "aws_eip" "nat" {
  domain   = "vpc"
  instance = aws_instance.nat.id

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-${var.environment}-nat" })
}

# Default route from every private route table through the NAT instance's ENI.
# The module VPC currently has a single shared private route table; for_each
# keeps this correct if that ever changes. (Route-table ids come from state,
# so the keys are known at plan time against the live stack.)
resource "aws_route" "private_nat" {
  for_each = toset(module.honua.private_route_table_ids)

  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat.primary_network_interface_id
}
