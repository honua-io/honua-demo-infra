###############################################################################
# Credential-safe candidate preflight, isolated from the demo application root.
#
# This root resolves one exact secret by its account/region-unique name through
# DescribeSecret metadata. It never reads SecretString or any primary state.
# It has no module call and cannot place the application Lambda, alias,
# environment, RDS, secret versions, seed/bootstrap helpers, or CloudFront in
# its plan.
###############################################################################

data "aws_secretsmanager_secret" "admin_password" {
  name = "honua-demo-demo/admin-password"

  lifecycle {
    postcondition {
      condition     = self.arn == "arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-${reverse(split("-", self.arn))[0]}" && can(regex("^[A-Za-z0-9]{6}$", reverse(split("-", self.arn))[0]))
      error_message = "metadata lookup did not return the exact demo admin-password secret identity."
    }
    postcondition {
      condition     = self.description == "Admin API password for Honua." && self.tags == tomap(local.common_tags)
      error_message = "metadata lookup returned unexpected description or tags."
    }
  }
}

check "default_workspace_only" {
  assert {
    condition     = terraform.workspace == "default"
    error_message = "candidate-preflight must be planned and applied only from the default workspace."
  }
}

locals {
  common_tags = {
    Project     = "honua-server"
    Environment = "demo"
    ManagedBy   = "terraform"
    Purpose     = "public-demo"
  }

  # The module creates this exact unique secret name. DescribeSecret returns
  # only metadata, including the authoritative ARN and generated suffix. The
  # suffix and secret value are never copied, derived, or queried here.
  admin_password_secret_arn = data.aws_secretsmanager_secret.admin_password.arn

  candidate_preflight_dir           = "${path.module}/../aws/candidate-preflight"
  candidate_preflight_function_name = "honua-demo-demo-candidate-preflight"
  candidate_preflight_log_group     = "/aws/lambda/${local.candidate_preflight_function_name}"
  candidate_preflight_role_name     = "honua-demo-demo-candidate-preflight-role"
  candidate_preflight_role_arn      = "arn:aws:iam::585192672263:role/${local.candidate_preflight_role_name}"

  candidate_preflight_app_function_name     = "honua-demo-demo-honua"
  candidate_preflight_app_function_arn      = "arn:aws:lambda:us-west-2:585192672263:function:honua-demo-demo-honua"
  candidate_preflight_log_group_arn         = "arn:aws:logs:us-west-2:585192672263:log-group:/aws/lambda/honua-demo-demo-candidate-preflight"
  candidate_preflight_candidate_version     = "40"
  candidate_preflight_candidate_revision_id = "0326e209-4231-4acd-9bb4-d3cb89402db0"
  candidate_preflight_live_alias_name       = "live"
  candidate_preflight_live_version          = "39"
  candidate_preflight_live_revision_id      = "4f73dd76-0294-44d3-8362-c6f8606f034e"
  candidate_preflight_image_digest          = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
  candidate_preflight_artifact_reference    = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@${local.candidate_preflight_image_digest}"
  candidate_preflight_source_commit         = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
  candidate_preflight_handler_source        = replace(file("${local.candidate_preflight_dir}/handler.py"), "\r\n", "\n")
  candidate_preflight_classification_source = replace(file("${local.candidate_preflight_dir}/classification.v1.json"), "\r\n", "\n")
  candidate_preflight_handler_sha256        = "69a59299be3c49530ed04bce9a0bfa53a79d63b0b23dcffcfd9f65c9bde217a1"
  candidate_preflight_classification_sha256 = "285b41bcc8b207b234b3ecfdeba7bae88b47920bffcbf0453fa4d099b585b579"
  candidate_preflight_archive_base64sha256  = "kp5S3LTvL8L2csu0yvP9zKwUv+0RP0Q++lgnTobCYgU="
}

data "archive_file" "candidate_preflight" {
  type = "zip"

  source {
    content  = local.candidate_preflight_handler_source
    filename = "handler.py"
  }

  source {
    content  = local.candidate_preflight_classification_source
    filename = "classification.v1.json"
  }

  output_path = "${path.module}/candidate-preflight.zip"

  lifecycle {
    precondition {
      condition     = sha256(local.candidate_preflight_handler_source) == local.candidate_preflight_handler_sha256
      error_message = "candidate-preflight handler.py differs from the reviewed source hash."
    }
    precondition {
      condition     = sha256(local.candidate_preflight_classification_source) == local.candidate_preflight_classification_sha256
      error_message = "candidate-preflight classification.v1.json differs from the reviewed source hash."
    }
    postcondition {
      condition     = self.output_base64sha256 == local.candidate_preflight_archive_base64sha256
      error_message = "candidate-preflight deterministic ZIP differs from the reviewed archive hash."
    }
  }
}

data "aws_iam_policy_document" "candidate_preflight_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "candidate_preflight" {
  name              = local.candidate_preflight_log_group
  retention_in_days = 90
  tags              = local.common_tags
}

resource "aws_iam_role" "candidate_preflight" {
  name               = local.candidate_preflight_role_name
  assume_role_policy = data.aws_iam_policy_document.candidate_preflight_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "candidate_preflight" {
  name = "credential-safe-candidate-preflight-v1"
  role = local.candidate_preflight_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadExactAdminPassword"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [local.admin_password_secret_arn]
      },
      {
        Sid    = "ReadExactCandidate"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
        ]
        Resource = ["${local.candidate_preflight_app_function_arn}:${local.candidate_preflight_candidate_version}"]
      },
      {
        Sid      = "ReadExactLiveAlias"
        Effect   = "Allow"
        Action   = ["lambda:GetAlias"]
        Resource = [local.candidate_preflight_app_function_arn]
      },
      {
        Sid      = "InvokeExactCandidate"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = ["${local.candidate_preflight_app_function_arn}:${local.candidate_preflight_candidate_version}"]
      },
      {
        Sid    = "WriteExactLogGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["${local.candidate_preflight_log_group_arn}:*"]
      },
    ]
  })

  depends_on = [aws_iam_role.candidate_preflight]
}

#checkov:skip=CKV_AWS_50: Fixed synchronous probe; logs contain only Lambda runtime failures with sanitized codes.
#checkov:skip=CKV_AWS_116: A synchronous operator probe must fail directly instead of enqueueing a DLQ record.
#checkov:skip=CKV_AWS_173: Environment contains only immutable public identifiers and one secret ARN, never secret material.
#checkov:skip=CKV_AWS_272: The repository-built helper is content-addressed by source_code_hash and independently reviewed.
resource "aws_lambda_function" "candidate_preflight" {
  function_name                  = local.candidate_preflight_function_name
  role                           = local.candidate_preflight_role_arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.candidate_preflight.output_path
  source_code_hash               = data.archive_file.candidate_preflight.output_base64sha256
  timeout                        = 120
  memory_size                    = 128
  reserved_concurrent_executions = 1
  publish                        = true

  environment {
    variables = {
      ADMIN_PASSWORD_SECRET_ARN      = local.admin_password_secret_arn
      EXPECTED_APP_FUNCTION_NAME     = local.candidate_preflight_app_function_name
      EXPECTED_ARCHITECTURE          = "arm64"
      EXPECTED_ARTIFACT_REFERENCE    = local.candidate_preflight_artifact_reference
      EXPECTED_CANDIDATE_REVISION_ID = local.candidate_preflight_candidate_revision_id
      EXPECTED_CANDIDATE_VERSION     = local.candidate_preflight_candidate_version
      EXPECTED_IMAGE_DIGEST          = local.candidate_preflight_image_digest
      EXPECTED_LIVE_ALIAS_NAME       = local.candidate_preflight_live_alias_name
      EXPECTED_LIVE_REVISION_ID      = local.candidate_preflight_live_revision_id
      EXPECTED_LIVE_VERSION          = local.candidate_preflight_live_version
      EXPECTED_PACKAGE_TYPE          = "Image"
      EXPECTED_SKIP_MIGRATIONS       = "true"
      EXPECTED_SOURCE_COMMIT         = local.candidate_preflight_source_commit
      SOURCE_CLASSIFICATION_SHA256   = local.candidate_preflight_classification_sha256
      SOURCE_HANDLER_SHA256          = local.candidate_preflight_handler_sha256
    }
  }

  lifecycle {
    precondition {
      condition     = data.aws_secretsmanager_secret.admin_password.name == "honua-demo-demo/admin-password"
      error_message = "candidate-preflight-v1 must resolve only the exact module-owned admin secret name."
    }
    precondition {
      condition     = can(regex("^arn:aws:secretsmanager:us-west-2:585192672263:secret:honua-demo-demo/admin-password-[A-Za-z0-9]{6}$", local.admin_password_secret_arn))
      error_message = "the authoritative module output is not the exact demo admin-password secret ARN."
    }
    precondition {
      condition     = data.aws_secretsmanager_secret.admin_password.description == "Admin API password for Honua."
      error_message = "candidate-preflight-v1 resolved unexpected secret metadata."
    }
    precondition {
      condition     = terraform.workspace == "default"
      error_message = "candidate-preflight must be applied only from the default workspace."
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.candidate_preflight,
    aws_iam_role_policy.candidate_preflight,
  ]

  tags = local.common_tags
}

output "candidate_preflight_qualified_arn" {
  description = "Immutable published candidate-preflight Lambda ARN; invoke only this qualified ARN."
  value       = aws_lambda_function.candidate_preflight.qualified_arn
}

output "candidate_preflight_version" {
  description = "Immutable published candidate-preflight Lambda version."
  value       = aws_lambda_function.candidate_preflight.version
}
