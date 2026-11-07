mock_provider "aws" {}

run "enabled" {
  command = apply
  variables {
    enable_studio_ai = true
  }

  assert {
    condition = local.studio_ai_environment == tomap({
      StudioAiProxy__Enabled                   = "true"
      StudioAiProxy__DefaultProvider           = "opus"
      StudioAiProxy__Providers__opus__Kind     = "anthropic"
      StudioAiProxy__Providers__opus__Endpoint = "https://api.anthropic.com"
      StudioAiProxy__Providers__opus__Model    = "claude-opus-5-5"
      StudioAiProxy__Providers__opus__ApiKey   = "aws:secretsmanager:${aws_secretsmanager_secret.studio_ai[0].arn}"
    })
    error_message = "Studio must use the Anthropic API, approved model and a secret reference."
  }

  assert {
    condition     = aws_secretsmanager_secret.studio_ai[0].name == "honua-demo-demo/studio-anthropic-api-key"
    error_message = "The operator's secret name must match the provisioned container."
  }

  assert {
    condition = jsondecode(aws_iam_role_policy.lambda_studio_ai_secret[0].policy).Statement == [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.studio_ai[0].arn]
    }]
    error_message = "The serving role must read only the Studio key container."
  }

  assert {
    condition     = aws_iam_role_policy.lambda_studio_ai_secret[0].role == local.honua_lambda_role_name
    error_message = "The read grant must reach the serving Lambda role."
  }
}

run "disabled" {
  command = plan
  variables {
    enable_studio_ai = false
  }
  assert {
    condition     = length(local.studio_ai_environment) == 0 && length(aws_secretsmanager_secret.studio_ai) == 0 && length(aws_iam_role_policy.lambda_studio_ai_secret) == 0
    error_message = "Disabling Studio must remove its environment and read grant."
  }
}

run "reject_bedrock_model" {
  command = plan
  variables {
    studio_ai_model = "us.anthropic.claude-opus-5"
  }
  expect_failures = [var.studio_ai_model]
}
