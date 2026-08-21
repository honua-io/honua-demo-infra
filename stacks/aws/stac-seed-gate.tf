###############################################################################
# Managed demo STAC seed and query-only deployment receipt.
#
# This deliberately does not auto-apply the seed. An authorized operator invokes
# the allowlisted manager after the serving Lambda revision is healthy. GitHub
# Actions can invoke only the separate receipt function, whose PostgreSQL login is
# limited to SELECT on the marker and current-pointer tables.
###############################################################################

data "aws_caller_identity" "stac_seed" {}

check "stac_seed_targets_serving_metadata_environment" {
  assert {
    condition     = var.stac_seed_metadata_environment == "Production"
    error_message = "The live demo serves Metadata v2 environment Production; refusing to seed any other environment."
  }
}

locals {
  demo_services_contract     = jsondecode(file("${path.module}/../../manifest/demo-services.v1.json"))
  stac_seed_source_url       = local.demo_services_contract.sources.stacSeed
  stac_seed_source_sha256    = local.demo_services_contract.sources.stacSeedSha256
  stac_seed_server_commit    = regex("/([0-9a-f]{40})/tests/seed/demo-stac-imagery-v1\\.sql$", local.stac_seed_source_url)[0]
  stac_seed_manager_name     = "${var.name_prefix}-${var.environment}-stac-seed-manager"
  stac_seed_receipt_name     = "${var.name_prefix}-${var.environment}-stac-seed-receipt"
  stac_seed_receipt_username = "honua_demo_seed_receipt"
  stac_seed_receipt_connection_string = replace(
    replace(module.honua.db_connection_string, "/Username=[^;]*/", "Username=${local.stac_seed_receipt_username}"),
    "/Password=[^;]*/",
    "Password=${random_password.stac_seed_receipt.result}"
  )
}

resource "random_password" "stac_seed_receipt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "stac_seed_receipt_connection" {
  name_prefix             = "${var.name_prefix}/${var.environment}/stac-seed-receipt-"
  description             = "Query-only PostgreSQL credential for the demo STAC seed receipt Lambda"
  recovery_window_in_days = 7
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "stac_seed_receipt_connection" {
  secret_id     = aws_secretsmanager_secret.stac_seed_receipt_connection.id
  secret_string = local.stac_seed_receipt_connection_string
}

# Security groups are deliberately separate from the arbitrary-SQL bootstrap and
# from each other. The VPC no longer has Secrets Manager interface endpoints, so
# HTTPS must follow the existing private-subnet NAT route. Port 443 is the narrow
# functional network contract for Secrets Manager and, for the manager only, the
# immutable raw.githubusercontent.com source. Database egress remains VPC-local.
#checkov:skip=CKV2_AWS_5: Attached directly to aws_lambda_function.stac_seed_manager.
resource "aws_security_group" "stac_seed_manager" {
  name_prefix = "${var.name_prefix}-${var.environment}-stacseed-"
  description = "Managed STAC seed Lambda network boundary"
  vpc_id      = module.honua.vpc_id

  egress {
    description = "PostgreSQL access inside the dedicated VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "HTTPS to Secrets Manager and immutable GitHub source through NAT"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

#checkov:skip=CKV2_AWS_5: Attached directly to aws_lambda_function.stac_seed_receipt.
resource "aws_security_group" "stac_seed_receipt" {
  name_prefix = "${var.name_prefix}-${var.environment}-stacreceipt-"
  description = "Query-only STAC seed receipt Lambda network boundary"
  vpc_id      = module.honua.vpc_id

  egress {
    description = "PostgreSQL access inside the dedicated VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "HTTPS to public Secrets Manager through NAT"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_iam_role" "stac_seed_manager" {
  name_prefix        = "${var.name_prefix}-${var.environment}-stacseed-"
  assume_role_policy = data.aws_iam_policy_document.postgis_bootstrap_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "stac_seed_manager_basic" {
  role       = aws_iam_role.stac_seed_manager.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "stac_seed_manager_vpc" {
  role       = aws_iam_role.stac_seed_manager.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "stac_seed_manager_secrets" {
  name = "read-admin-and-receipt-db-secrets"
  role = aws_iam_role.stac_seed_manager.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        module.honua.db_connection_secret_arn,
        aws_secretsmanager_secret.stac_seed_receipt_connection.arn,
      ]
    }]
  })
}

#checkov:skip=CKV_AWS_50: Explicit one-shot operator path; CloudWatch logs are the audit surface.
#checkov:skip=CKV_AWS_116: Synchronous operator invocation must fail directly, not enqueue a DLQ record.
#checkov:skip=CKV_AWS_173: Environment variables contain ARNs and public immutable source identifiers only.
#checkov:skip=CKV_AWS_272: Terraform-built helper shares the reviewed bootstrap artifact.
resource "aws_lambda_function" "stac_seed_manager" {
  function_name                  = local.stac_seed_manager_name
  role                           = aws_iam_role.stac_seed_manager.arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.postgis_bootstrap.output_path
  source_code_hash               = data.archive_file.postgis_bootstrap.output_base64sha256
  timeout                        = 180
  memory_size                    = 256
  reserved_concurrent_executions = 1

  vpc_config {
    subnet_ids         = module.honua.private_subnet_ids
    security_group_ids = [aws_security_group.stac_seed_manager.id]
  }

  environment {
    variables = {
      DB_SECRET_ARN                  = module.honua.db_connection_secret_arn
      OPERATION_MODE                 = "managed-stac-seed"
      RECEIPT_DB_SECRET_ARN          = aws_secretsmanager_secret.stac_seed_receipt_connection.arn
      STAC_SEED_METADATA_ENVIRONMENT = var.stac_seed_metadata_environment
      STAC_SEED_SERVER_COMMIT        = local.stac_seed_server_commit
      STAC_SEED_SOURCE_SHA256        = local.stac_seed_source_sha256
      STAC_SEED_SOURCE_URL           = local.stac_seed_source_url
    }
  }

  depends_on = [
    aws_iam_role_policy.stac_seed_manager_secrets,
    aws_iam_role_policy_attachment.stac_seed_manager_basic,
    aws_iam_role_policy_attachment.stac_seed_manager_vpc,
  ]
  tags = local.common_tags
}

resource "aws_iam_role" "stac_seed_receipt" {
  name_prefix        = "${var.name_prefix}-${var.environment}-stacreceipt-"
  assume_role_policy = data.aws_iam_policy_document.postgis_bootstrap_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "stac_seed_receipt_basic" {
  role       = aws_iam_role.stac_seed_receipt.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "stac_seed_receipt_vpc" {
  role       = aws_iam_role.stac_seed_receipt.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "stac_seed_receipt_secret" {
  name = "read-query-only-db-secret"
  role = aws_iam_role.stac_seed_receipt.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.stac_seed_receipt_connection.arn]
    }]
  })
}

#checkov:skip=CKV_AWS_50: Fixed query-only receipt; CloudWatch logs are sufficient.
#checkov:skip=CKV_AWS_116: Synchronous canary invocation must fail directly.
#checkov:skip=CKV_AWS_173: Environment variable contains only a secret ARN.
#checkov:skip=CKV_AWS_272: Terraform-built helper shares the reviewed bootstrap artifact.
resource "aws_lambda_function" "stac_seed_receipt" {
  function_name                  = local.stac_seed_receipt_name
  role                           = aws_iam_role.stac_seed_receipt.arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.postgis_bootstrap.output_path
  source_code_hash               = data.archive_file.postgis_bootstrap.output_base64sha256
  timeout                        = 30
  memory_size                    = 128
  reserved_concurrent_executions = 1

  vpc_config {
    subnet_ids         = module.honua.private_subnet_ids
    security_group_ids = [aws_security_group.stac_seed_receipt.id]
  }

  environment {
    variables = {
      DB_SECRET_ARN  = aws_secretsmanager_secret.stac_seed_receipt_connection.arn
      OPERATION_MODE = "stac-seed-receipt"
    }
  }

  depends_on = [
    aws_iam_role_policy.stac_seed_receipt_secret,
    aws_iam_role_policy_attachment.stac_seed_receipt_basic,
    aws_iam_role_policy_attachment.stac_seed_receipt_vpc,
    aws_secretsmanager_secret_version.stac_seed_receipt_connection,
  ]
  tags = local.common_tags
}

resource "aws_iam_role" "github_stac_seed_receipt" {
  name = "${var.name_prefix}-${var.environment}-github-stac-receipt"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.stac_seed.account_id}:oidc-provider/token.actions.githubusercontent.com"
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:honua-io/honua-demo-infra:ref:refs/heads/trunk"
        }
      }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "github_stac_seed_receipt" {
  name = "invoke-query-only-stac-seed-receipt"
  role = aws_iam_role.github_stac_seed_receipt.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.stac_seed_receipt.arn]
    }]
  })
}

output "stac_seed_manager_function_name" {
  description = "Allowlisted in-VPC demo STAC seed manager; invoke only after the serving revision is healthy."
  value       = aws_lambda_function.stac_seed_manager.function_name
}

output "stac_seed_receipt_function_name" {
  description = "Fixed-query demo STAC seed receipt Lambda consumed by the dispatch canary."
  value       = aws_lambda_function.stac_seed_receipt.function_name
}

output "stac_seed_receipt_github_role_arn" {
  description = "Exact-trunk GitHub OIDC role that can invoke only the query-only receipt Lambda."
  value       = aws_iam_role.github_stac_seed_receipt.arn
}
