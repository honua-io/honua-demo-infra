###############################################################################
# Immutable, no-trigger, migrations-only runner for demo schema 091 -> 105.
#
# This root is deliberately isolated from primary application and preflight
# state. It never manages RDS, the application Lambda, aliases, seed helpers,
# secret values, or a trigger. The separately authorized operator creates and
# verifies the manual recovery snapshot before invoking the qualified version.
###############################################################################

check "default_workspace_only" {
  assert {
    condition     = terraform.workspace == "default"
    error_message = "db-migration-runner must use only the default workspace."
  }
}

data "aws_secretsmanager_secret" "db_connection" {
  name = "honua-demo-demo/connection-string"

  lifecycle {
    postcondition {
      condition     = can(regex("^arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/connection-string-[A-Za-z0-9]{6}$", self.arn))
      error_message = "metadata lookup did not return the exact demo DB connection secret."
    }
    postcondition {
      condition     = self.description == "Database connection string for Honua."
      error_message = "DB connection secret description drifted."
    }
  }
}

locals {
  common_tags = {
    Project     = "honua-server"
    Environment = "demo"
    ManagedBy   = "terraform"
    Purpose     = "public-demo"
  }

  account_id         = "585192672263"
  region             = "us-west-2"
  vpc_id             = "vpc-0ac1893d15caf97b8"
  vpc_cidr           = "10.0.0.0/16"
  private_subnet_ids = ["subnet-042ddf313d8ae1b17", "subnet-095cc7be2b14464f5", "subnet-0fd43d4ab39f4ff00"]
  function_name      = "honua-demo-demo-db-migration-092-105"
  role_name          = "${local.function_name}-role"
  log_group_name     = "/aws/lambda/${local.function_name}"
  log_group_arn      = "arn:aws:logs:${local.region}:${local.account_id}:log-group:${local.log_group_name}"
  source_commit      = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
  candidate_digest   = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
  pending_set_sha256 = "e0ee6b49e11639e971a58efd942f377de588b81bd6f8ed7eb0dae4ccb1a28cb7"
  preflight_sha256   = "357424246af64a7e435ac5e694f50d8935fd61744ac7ce954efb05223dc3c0ee"
  runner_source_dir  = "${path.module}/runner"
  runner_archive     = "${path.module}/db-migration-runner.zip"
  handler_sha256     = sha256(file("${local.runner_source_dir}/handler.py"))
  manifest_sha256    = sha256(file("${local.runner_source_dir}/migration-manifest.v1.json"))
  lock_sha256        = sha256(file("${local.runner_source_dir}/requirements.lock.json"))
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "runner" {
  name              = local.log_group_name
  retention_in_days = 90
  tags              = local.common_tags
}

resource "aws_iam_role" "runner" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "runner" {
  name = "fixed-db-migration-092-105"
  role = aws_iam_role.runner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadExactConnectionSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [data.aws_secretsmanager_secret.db_connection.arn]
      },
      {
        Sid    = "ManageOwnVpcInterface"
        Effect = "Allow"
        Action = [
          "ec2:AssignPrivateIpAddresses",
          "ec2:CreateNetworkInterface",
          "ec2:DeleteNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:UnassignPrivateIpAddresses",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "WriteExactLogGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["${local.log_group_arn}:*"]
      },
    ]
  })
}

#checkov:skip=CKV2_AWS_5: Attached directly to aws_lambda_function.runner.
resource "aws_security_group" "runner" {
  name_prefix = "honua-demo-demo-db-migration-"
  description = "One-shot Honua DB migration runner network boundary"
  vpc_id      = local.vpc_id

  egress {
    description = "PostgreSQL inside the dedicated demo VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "Secrets Manager through the existing private-subnet NAT route"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

#checkov:skip=CKV_AWS_50: Fixed synchronous operation; output is explicitly sanitized.
#checkov:skip=CKV_AWS_116: Synchronous one-attempt operation must fail directly.
#checkov:skip=CKV_AWS_173: Environment contains public pins and a secret ARN, not secret material.
#checkov:skip=CKV_AWS_272: Repository-built artifact is content-addressed by source_code_hash.
resource "aws_lambda_function" "runner" {
  function_name                  = local.function_name
  role                           = aws_iam_role.runner.arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = local.runner_archive
  source_code_hash               = filebase64sha256(local.runner_archive)
  timeout                        = 900
  memory_size                    = 512
  reserved_concurrent_executions = 1
  publish                        = true

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.runner.id]
  }

  environment {
    variables = {
      DB_SECRET_ARN                   = data.aws_secretsmanager_secret.db_connection.arn
      EXPECTED_CANDIDATE_IMAGE_DIGEST = local.candidate_digest
      EXPECTED_PENDING_SET_SHA256     = local.pending_set_sha256
      EXPECTED_PREFLIGHT_SHA256       = local.preflight_sha256
      EXPECTED_SOURCE_COMMIT          = local.source_commit
      MIGRATION_OPERATION             = "apply-092-105"
      SOURCE_HANDLER_SHA256           = local.handler_sha256
      SOURCE_MANIFEST_SHA256          = local.manifest_sha256
    }
  }

  lifecycle {
    precondition {
      condition     = terraform.workspace == "default"
      error_message = "db-migration-runner must be published only from the default workspace."
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.runner,
    aws_iam_role_policy.runner,
  ]
  tags = local.common_tags
}

output "db_migration_runner_qualified_arn" {
  description = "Immutable qualified ARN for the fixed 092-105 runner; never invoke an unqualified ARN."
  value       = aws_lambda_function.runner.qualified_arn
}

output "db_migration_runner_version" {
  description = "Immutable published version of the fixed 092-105 runner."
  value       = aws_lambda_function.runner.version
}
