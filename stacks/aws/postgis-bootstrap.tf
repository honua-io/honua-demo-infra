###############################################################################
# PostGIS bootstrap — one-shot in-VPC Lambda.
#
# Honua's migration 001 creates GEOMETRY columns, so postgis must exist BEFORE
# the server's startup migrations run. The module's `enable_postgis`
# local-exec needs psql plus a network path to the (private, no-NAT) RDS
# instance, which the apply host does not have. Instead, a tiny Python Lambda
# inside the VPC enables postgis + postgis_raster as the RDS master user.
# Terraform invokes it exactly once after the database is created
# (idempotent: CREATE EXTENSION IF NOT EXISTS).
#
# Build prerequisites on the apply host: python3 + pip (replaces the module's
# psql + network-path requirement). The pure-Python pg8000 driver is vendored
# into the deployment zip at apply time; nothing is compiled.
###############################################################################

locals {
  postgis_bootstrap_dir       = "${path.module}/postgis-bootstrap"
  postgis_bootstrap_pg8000    = "1.31.2" # pure-Python PostgreSQL driver, pinned
  postgis_bootstrap_func_name = "${var.name_prefix}-${var.environment}-postgis-bootstrap"
  # Amazon RDS global CA bundle, vendored into the zip so the Lambda can verify
  # the RDS server certificate (chain + hostname) instead of trusting any cert.
  # https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html
  postgis_bootstrap_rds_ca_url      = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
  postgis_bootstrap_rds_ca_filename = "rds-global-bundle.pem"
}

resource "terraform_data" "postgis_bootstrap_build" {
  triggers_replace = {
    handler        = filesha256("${local.postgis_bootstrap_dir}/handler.py")
    pg8000_version = local.postgis_bootstrap_pg8000
    rds_ca_url     = local.postgis_bootstrap_rds_ca_url
  }

  provisioner "local-exec" {
    interpreter = ["python", "-c"]
    command     = <<-EOT
      import pathlib, shutil, subprocess, sys, urllib.request
      root = pathlib.Path(r"${local.postgis_bootstrap_dir}")
      build = root / "build"
      shutil.rmtree(build, ignore_errors=True)
      build.mkdir(parents=True, exist_ok=True)
      subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                             "--target", str(build), "pg8000==${local.postgis_bootstrap_pg8000}"])
      shutil.copy(root / "handler.py", build / "handler.py")
      # Vendor the Amazon RDS global CA bundle so the Lambda can verify the RDS
      # server certificate. The build host already requires internet for pip.
      ca = build / "${local.postgis_bootstrap_rds_ca_filename}"
      with urllib.request.urlopen("${local.postgis_bootstrap_rds_ca_url}", timeout=30) as r:
          data = r.read()
      if b"BEGIN CERTIFICATE" not in data:
          raise SystemExit("downloaded RDS CA bundle is not a PEM certificate file")
      ca.write_bytes(data)
    EOT
  }
}

data "archive_file" "postgis_bootstrap" {
  type        = "zip"
  source_dir  = "${local.postgis_bootstrap_dir}/build"
  output_path = "${local.postgis_bootstrap_dir}/bootstrap.zip"

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
    description = "HTTPS to the Secrets Manager interface endpoint"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
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
      DB_SECRET_ARN = module.honua.db_connection_secret_arn
    }
  }

  tags = local.common_tags
}

# Runs during apply, after the module (and therefore RDS + the connection
# secret) is fully created and the Secrets Manager endpoint is reachable.
resource "aws_lambda_invocation" "postgis_bootstrap" {
  function_name = aws_lambda_function.postgis_bootstrap.function_name
  input         = jsonencode({})

  depends_on = [
    module.honua,
    aws_vpc_endpoint.secretsmanager,
    aws_iam_role_policy.postgis_bootstrap_secret,
    aws_iam_role_policy_attachment.postgis_bootstrap_basic,
    aws_iam_role_policy_attachment.postgis_bootstrap_vpc,
  ]
}

output "postgis_bootstrap_result" {
  description = "Extensions reported by the one-shot PostGIS bootstrap Lambda."
  value       = aws_lambda_invocation.postgis_bootstrap.result
}
