###############################################################################
# Credential-safe candidate preflight.
#
# This synchronous, operator-invoked Lambda probes one immutable Honua Lambda
# version through four fixed API Gateway v2 GET events. It cannot update or
# publish the application, move an alias, reach the database, run migrations,
# invoke seed/bootstrap helpers, or attach to the VPC. Terraform deliberately
# creates no aws_lambda_invocation resource for this function.
###############################################################################

locals {
  candidate_preflight_dir           = "${path.module}/candidate-preflight"
  candidate_preflight_function_name = "${var.name_prefix}-${var.environment}-candidate-preflight"
  candidate_preflight_log_group     = "/aws/lambda/${local.candidate_preflight_function_name}"

  candidate_preflight_app_function_name     = "honua-demo-demo-honua"
  candidate_preflight_candidate_version     = "40"
  candidate_preflight_candidate_revision_id = "0326e209-4231-4acd-9bb4-d3cb89402db0"
  candidate_preflight_live_alias_name       = "live"
  candidate_preflight_live_version          = "39"
  candidate_preflight_live_revision_id      = "4f73dd76-0294-44d3-8362-c6f8606f034e"
  candidate_preflight_image_digest          = "sha256:67d96f75ec9220c7cc238e241888d5cf79d9587b8220aaa1bfcb4f0d6f4bd861"
  candidate_preflight_artifact_reference    = "585192672263.dkr.ecr.us-west-2.amazonaws.com/honua-server@${local.candidate_preflight_image_digest}"
  candidate_preflight_source_commit         = "7a29ce0cb4b862b7e58bd58c42e96dcc5e16ccad"
}

data "archive_file" "candidate_preflight" {
  type        = "zip"
  source_dir  = local.candidate_preflight_dir
  output_path = "${path.module}/candidate-preflight.zip"
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
  name_prefix        = "${var.name_prefix}-${var.environment}-candidate-preflight-"
  assume_role_policy = data.aws_iam_policy_document.candidate_preflight_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "candidate_preflight" {
  name = "credential-safe-candidate-preflight-v1"
  role = aws_iam_role.candidate_preflight.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadExactAdminPassword"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [module.honua.admin_password_secret_arn]
      },
      {
        Sid    = "ReadExactCandidate"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
        ]
        Resource = ["${module.honua.lambda_function_arn}:${local.candidate_preflight_candidate_version}"]
      },
      {
        Sid      = "ReadExactLiveAlias"
        Effect   = "Allow"
        Action   = ["lambda:GetAlias"]
        Resource = ["${module.honua.lambda_function_arn}:${local.candidate_preflight_live_alias_name}"]
      },
      {
        Sid      = "InvokeExactCandidate"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = ["${module.honua.lambda_function_arn}:${local.candidate_preflight_candidate_version}"]
      },
      {
        Sid    = "WriteExactLogGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["${aws_cloudwatch_log_group.candidate_preflight.arn}:*"]
      },
    ]
  })
}

#checkov:skip=CKV_AWS_50: Fixed synchronous probe; logs contain only Lambda runtime failures with sanitized codes.
#checkov:skip=CKV_AWS_116: A synchronous operator probe must fail directly instead of enqueueing a DLQ record.
#checkov:skip=CKV_AWS_173: Environment contains only immutable public identifiers and one secret ARN, never secret material.
#checkov:skip=CKV_AWS_272: The repository-built helper is content-addressed by source_code_hash and independently reviewed.
resource "aws_lambda_function" "candidate_preflight" {
  function_name                  = local.candidate_preflight_function_name
  role                           = aws_iam_role.candidate_preflight.arn
  runtime                        = "python3.13"
  handler                        = "handler.handler"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.candidate_preflight.output_path
  source_code_hash               = data.archive_file.candidate_preflight.output_base64sha256
  timeout                        = 120
  memory_size                    = 128
  reserved_concurrent_executions = 1

  environment {
    variables = {
      ADMIN_PASSWORD_SECRET_ARN      = module.honua.admin_password_secret_arn
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
    }
  }

  lifecycle {
    precondition {
      condition     = module.honua.lambda_function_name == local.candidate_preflight_app_function_name
      error_message = "candidate-preflight-v1 is pinned to the exact demo Honua function name."
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.candidate_preflight,
    aws_iam_role_policy.candidate_preflight,
  ]

  tags = local.common_tags
}

output "candidate_preflight_function_name" {
  description = "Credential-safe synchronous candidate-preflight wrapper; accepts only candidate-preflight-v1."
  value       = aws_lambda_function.candidate_preflight.function_name
}
