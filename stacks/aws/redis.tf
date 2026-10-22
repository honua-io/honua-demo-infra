###############################################################################
# ElastiCache Redis that is provisioned but NOT wired into the serving Lambda
# (var.wire_redis_connection = false).
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
