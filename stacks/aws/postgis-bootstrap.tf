###############################################################################
# PostGIS bootstrap — one-shot in-VPC Lambda.
#
# Honua's migration 001 creates GEOMETRY columns, so postgis must exist BEFORE
# the server's startup migrations run. The module's `enable_postgis`
# local-exec needs psql plus a network path to the (private, no-NAT) RDS
# instance, which the apply host does not have. Instead, a tiny Python Lambda
# inside the VPC enables postgis, postgis_raster, h3 and h3_postgis as the RDS master user.
# Terraform invokes it exactly once after the database is created
# (idempotent: CREATE EXTENSION IF NOT EXISTS), and again whenever handler.py changes.
#
# Build prerequisites on the plan/apply host: python3 and HTTPS to PyPI and the
# RDS truststore (replaces the module's psql + network-path requirement). Run
# `python3 postgis-bootstrap/build.py` before `terraform plan`; see below.
###############################################################################

locals {
  postgis_bootstrap_dir       = "${path.module}/postgis-bootstrap"
  postgis_bootstrap_func_name = "${var.name_prefix}-${var.environment}-postgis-bootstrap"
}

# The package directory is built by postgis-bootstrap/build.py from the
# hash-locked inputs in postgis-bootstrap/requirements.lock.json (pg8000 and
# its full transitive set as exact wheels, plus the Amazon RDS global CA
# bundle so the Lambda can verify the RDS server certificate). The lock keeps
# #105's scramp 1.4.18 pin: scramp 1.5.0 removed make_channel_binding, which
# pg8000 1.31.2 calls. The build installs
# nothing and writes every file 0644, so the zip is byte-identical on every
# host. CI runs the same script before `terraform plan`, because the archive
# below is read at plan time whenever this resource has no pending change.
resource "terraform_data" "postgis_bootstrap_build" {
  triggers_replace = {
    handler = filesha256("${local.postgis_bootstrap_dir}/handler.py")
    build   = filesha256("${local.postgis_bootstrap_dir}/build.py")
    lock    = filesha256("${local.postgis_bootstrap_dir}/requirements.lock.json")
  }

  provisioner "local-exec" {
    interpreter = ["python3"]
    command     = "${local.postgis_bootstrap_dir}/build.py"
  }
}

data "archive_file" "postgis_bootstrap" {
  type             = "zip"
  source_dir       = "${local.postgis_bootstrap_dir}/build"
  output_path      = "${local.postgis_bootstrap_dir}/bootstrap.zip"
  output_file_mode = "0644"

  depends_on = [terraform_data.postgis_bootstrap_build]
}

#checkov:skip=CKV2_AWS_5: Security group is attached to the bootstrap Lambda function.
resource "aws_security_group" "postgis_bootstrap" {
  #checkov:skip=CKV2_AWS_5: Security group is attached to the bootstrap Lambda function.
  name_prefix = "${var.name_prefix}-${var.environment}-pgboot-"
  description = "PostGIS bootstrap Lambda security group"
  vpc_id      = module.honua.vpc_id

  egress {
    description = "PostgreSQL access"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "HTTPS to public Secrets Manager through the private-subnet NAT route"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

data "aws_iam_policy_document" "postgis_bootstrap_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "postgis_bootstrap" {
  name_prefix        = "${var.name_prefix}-${var.environment}-pgboot-"
  assume_role_policy = data.aws_iam_policy_document.postgis_bootstrap_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "postgis_bootstrap_basic" {
  role       = aws_iam_role.postgis_bootstrap.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "postgis_bootstrap_vpc" {
  role       = aws_iam_role.postgis_bootstrap.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "postgis_bootstrap_secret" {
  name = "read-db-connection-secret"
  role = aws_iam_role.postgis_bootstrap.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [module.honua.db_connection_secret_arn]
      }
    ]
  })
}

#checkov:skip=CKV_AWS_50: One-shot bootstrap helper; X-Ray adds no value.
#checkov:skip=CKV_AWS_115: One-shot bootstrap helper; concurrent execution limit set to 1 explicitly below.
#checkov:skip=CKV_AWS_116: Invoked synchronously by Terraform; a DLQ is meaningless.
#checkov:skip=CKV_AWS_173: The env var holds a secret ARN, not secret material.
#checkov:skip=CKV_AWS_272: Code signing is unnecessary for a Terraform-built helper zip.
resource "aws_lambda_function" "postgis_bootstrap" {
  #checkov:skip=CKV_AWS_50: One-shot bootstrap helper; X-Ray adds no value.
  #checkov:skip=CKV_AWS_115: One-shot bootstrap helper; concurrent execution limit set to 1 explicitly below.
  #checkov:skip=CKV_AWS_116: Invoked synchronously by Terraform; a DLQ is meaningless.
  #checkov:skip=CKV_AWS_173: The env var holds a secret ARN, not secret material.
  #checkov:skip=CKV_AWS_272: Code signing is unnecessary for a Terraform-built helper zip.
  function_name                  = local.postgis_bootstrap_func_name
  role                           = aws_iam_role.postgis_bootstrap.arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.postgis_bootstrap.output_path
  source_code_hash               = data.archive_file.postgis_bootstrap.output_base64sha256
  timeout                        = 120
  memory_size                    = 256
  reserved_concurrent_executions = 1

  vpc_config {
    subnet_ids         = module.honua.private_subnet_ids
    security_group_ids = [aws_security_group.postgis_bootstrap.id]
  }

  environment {
    variables = {
      DB_SECRET_ARN  = module.honua.db_connection_secret_arn
      OPERATION_MODE = "break-glass-bootstrap"
    }
  }

  tags = local.common_tags
}

# Runs during apply, after the module (and therefore RDS + the connection
# secret) is fully created and Secrets Manager is reachable from the private
# subnets — via the fck-nat default route since the 2026-07 cost round
# (previously via the Secrets Manager interface endpoint).
resource "aws_lambda_invocation" "postgis_bootstrap" {
  function_name = aws_lambda_function.postgis_bootstrap.function_name
  input         = jsonencode({})

  # Re-run the (idempotent) bootstrap whenever the handler changes, e.g. when
  # the extension list grows; a new input is not needed for that.
  triggers = {
    source_code_hash = aws_lambda_function.postgis_bootstrap.source_code_hash
  }

  depends_on = [
    module.honua,
    aws_route.private_nat,
    aws_iam_role_policy.postgis_bootstrap_secret,
    aws_iam_role_policy_attachment.postgis_bootstrap_basic,
    aws_iam_role_policy_attachment.postgis_bootstrap_vpc,
  ]
}

output "postgis_bootstrap_result" {
  description = "Extensions reported by the one-shot PostGIS bootstrap Lambda."
  value       = aws_lambda_invocation.postgis_bootstrap.result
}
