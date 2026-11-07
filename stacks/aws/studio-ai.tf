resource "aws_secretsmanager_secret" "studio_ai" {
  count                   = var.enable_studio_ai ? 1 : 0
  name                    = "${var.name_prefix}-${var.environment}/studio-anthropic-api-key"
  description             = "Anthropic API key for the demo Studio provider; value managed by the operator."
  recovery_window_in_days = 30
}

locals {
  studio_ai_environment = var.enable_studio_ai ? {
    StudioAiProxy__Enabled                   = "true"
    StudioAiProxy__DefaultProvider           = "opus"
    StudioAiProxy__Providers__opus__Kind     = "anthropic"
    StudioAiProxy__Providers__opus__Endpoint = "https://api.anthropic.com"
    StudioAiProxy__Providers__opus__Model    = var.studio_ai_model
    StudioAiProxy__Providers__opus__ApiKey   = "aws:secretsmanager:${aws_secretsmanager_secret.studio_ai[0].arn}"
  } : {}
}

resource "aws_iam_role_policy" "lambda_studio_ai_secret" {
  count = var.enable_studio_ai ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-lambda-studio-ai-secret"
  role  = local.honua_lambda_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.studio_ai[0].arn]
    }]
  })
}
