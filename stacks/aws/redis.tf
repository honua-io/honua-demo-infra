###############################################################################
# ElastiCache Redis that is provisioned but NOT wired into the serving Lambda
# (var.wire_redis_connection = false). Wired on 2026-10-24 (#98) and unwired again the
# same day after the serving process started dying in warm containers; see demo.tfvars.
#
# Why this file exists
# --------------------
# The pinned aws-serverless module couples "create Redis" to "inject
# ConnectionStrings__redis into the function environment" (redis_enabled), and
# merges that injection after var.additional_env, so a caller cannot omit it.
# The 2026-10-22 rc cutover must keep the cache but not use it:
#   * honua-server#6500: with Redis configured the rc requires
#     Operations:SecretChannel:KeyRingCertificatePath and aborts at boot without it.
#   * honua-server#6487: Redis-off Lambda keeps admin API keys per process (known gap).
# Re-pinning the module to get a toggle is not an option here: the current
# honua-iac trunk replaces random_password.db and updates the RDS instance.
#
# So when wire_redis_connection is false the module is told redis_enabled=false,
# and the same six resources, with the module's own definitions and defaults,
# are owned here. The moved blocks adopt the existing objects from the module's
# addresses so nothing is destroyed or recreated. They are inert while the module
# owns the resources (wire_redis_connection = true) and when the objects are
# already at the stack addresses.
#
# To wire Redis back in later, move the six objects back into the module in a
# reviewed change (a moved block cannot point both ways), then set
# wire_redis_connection = true. The module and this file must never both own
# honua-demo-demo-redis.
###############################################################################

# ---------------------------------------------------------------------------
# Operation key-ring certificate delivery for the WIRED case (honua-server#6500).
# Inert while wire_redis_connection = false (the demo today): no env var, no
# IAM grant. When Redis is wired the Lambda receives only the secret
# reference; the server resolves it with the function role at startup and
# writes the PKCS#12 to a private temp file. The secret is operator-owned and
# created out of band (runbook/exports-redis-prereq.md); Terraform never reads
# it. ~95 bytes of the 4096-byte Lambda environment budget.
# ---------------------------------------------------------------------------

locals {
  redis_wired = var.enable_redis && var.wire_redis_connection
  # arn:aws:secretsmanager:<region>:<acct>:secret:<name>-<6-char suffix> -> <name>
  operation_key_ring_secret_name = var.operation_key_ring_certificate_secret_arn == "" ? "" : regex("^arn:aws:secretsmanager:[^:]+:[0-9]{12}:secret:(.+)-[A-Za-z0-9]{6}$", var.operation_key_ring_certificate_secret_arn)[0]
  operation_key_ring_environment = local.redis_wired ? {
    # NAME form (same account and region), not the ARN: 54 bytes shorter, which
    # the 4096-byte Lambda environment needs once Redis is wired. The server
    # resolves either form; the IAM grant below still names the exact ARN.
    Operations__SecretChannel__KeyRingCertificatePkcs12 = "aws:secretsmanager:${local.operation_key_ring_secret_name}"
  } : {}
}

resource "aws_iam_role_policy" "lambda_operation_key_ring" {
  count = local.redis_wired ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-operation-key-ring"
  role  = local.honua_lambda_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.operation_key_ring_certificate_secret_arn]
      }
    ]
  })
}

locals {
  redis_unwired  = var.enable_redis && !var.wire_redis_connection
  redis_name     = "${var.name_prefix}-${var.environment}"
  redis_tags     = merge({ Project = "honua-server", Environment = var.environment, ManagedBy = "terraform" }, local.common_tags)
  redis_engine_v = "7.0"
  redis_param_g  = "default.redis7"
}

resource "random_password" "redis_auth_unwired" {
  count       = local.redis_unwired ? 1 : 0
  length      = 32
  special     = true
  min_upper   = 1
  min_lower   = 1
  min_numeric = 1
  min_special = 1
  # ElastiCache AUTH accepts only this restricted special-character set.
  override_special = "!&#$^<>-"
}

resource "aws_security_group" "redis_unwired" {
  count       = local.redis_unwired ? 1 : 0
  name_prefix = "${local.redis_name}-redis-"
  description = "Redis security group"
  vpc_id      = module.honua.vpc_id

  ingress {
    description     = "Redis from Lambda"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.honua.lambda_security_group_id]
  }

  tags = local.redis_tags
}

resource "aws_elasticache_subnet_group" "redis_unwired" {
  count       = local.redis_unwired ? 1 : 0
  name        = "${local.redis_name}-redis"
  subnet_ids  = module.honua.private_subnet_ids
  description = "Redis subnet group"
  tags        = local.redis_tags
}

resource "aws_elasticache_replication_group" "redis_unwired" {
  count                      = local.redis_unwired ? 1 : 0
  replication_group_id       = "${local.redis_name}-redis"
  description                = "Honua Redis"
  node_type                  = var.redis_node_type
  engine                     = "redis"
  engine_version             = local.redis_engine_v
  port                       = 6379
  parameter_group_name       = local.redis_param_g
  automatic_failover_enabled = false
  multi_az_enabled           = false
  num_cache_clusters         = 1
  subnet_group_name          = aws_elasticache_subnet_group.redis_unwired[0].name
  security_group_ids         = [aws_security_group.redis_unwired[0].id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth_unwired[0].result
  apply_immediately          = true
  tags                       = local.redis_tags
}

resource "aws_secretsmanager_secret" "redis_connection_unwired" {
  count       = local.redis_unwired ? 1 : 0
  name        = "${local.redis_name}/redis-connection"
  description = "Redis connection string for Honua."
  tags        = local.redis_tags
}

resource "aws_secretsmanager_secret_version" "redis_connection_unwired" {
  count         = local.redis_unwired ? 1 : 0
  secret_id     = aws_secretsmanager_secret.redis_connection_unwired[0].id
  secret_string = "${aws_elasticache_replication_group.redis_unwired[0].primary_endpoint_address}:6379,password=${random_password.redis_auth_unwired[0].result},ssl=true"
}

# Redis is UNWIRED again (wire_redis_connection = false since the 2026-10-24 rollback):
# the six objects move from the module back to the stack addresses. The reverse blocks
# (*_unwired -> module) from #98 were removed in the same change; a moved block cannot
# point both ways.
moved {
  from = module.honua.random_password.redis_auth[0]
  to   = random_password.redis_auth_unwired[0]
}
moved {
  from = module.honua.aws_security_group.redis[0]
  to   = aws_security_group.redis_unwired[0]
}
moved {
  from = module.honua.aws_elasticache_subnet_group.redis[0]
  to   = aws_elasticache_subnet_group.redis_unwired[0]
}
moved {
  from = module.honua.aws_elasticache_replication_group.redis[0]
  to   = aws_elasticache_replication_group.redis_unwired[0]
}
moved {
  from = module.honua.aws_secretsmanager_secret.redis_connection[0]
  to   = aws_secretsmanager_secret.redis_connection_unwired[0]
}
moved {
  from = module.honua.aws_secretsmanager_secret_version.redis_connection[0]
  to   = aws_secretsmanager_secret_version.redis_connection_unwired[0]
}
