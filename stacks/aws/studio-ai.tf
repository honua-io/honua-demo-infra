###############################################################################
# Studio AI proxy — live AI generation for Honua Studio via Amazon Bedrock.
#
# honua-server#3000 ships a provider-agnostic Studio AI proxy
# (src/Honua.Ai/Features/StudioAiProxy). Its `bedrock` adapter kind
# authenticates via the AWS credential chain (here: the Lambda execution role)
# and targets the regional bedrock-runtime endpoint derived from the
# provider's `Region` — no endpoint URL and no API key, so nothing secret
# lands in Terraform. This file wires the demo Lambda for it, gated on
# var.enable_studio_ai (default off), mirroring the enable_bedrock_ai
# (WorkflowGeneration) add-on but kept independent of it — separate toggle,
# model, and IAM grant, so the two AI features can point at different models
# and be enabled/disabled without touching each other:
#
#   1. Least-privilege bedrock:InvokeModel / InvokeModelWithResponseStream on
#      the Lambda execution role, scoped to the configured model's (and the
#      declared fallback models') inference-profile + foundation-model ARNs.
#   2. StudioAiProxy__* environment — the exact configuration keys the server
#      binds (StudioAiProxyConfiguration, section "StudioAiProxy") — merged
#      into the module's additional_env in main.tf.
#   3. Network path: Bedrock is reached through the fck-nat egress
#      (nat-instance.tf) — the bedrock-runtime interface endpoint this
#      originally shared with enable_bedrock_ai was removed in the 2026-07-24
#      cost round. studio_ai_region stays us-west-2 (= var.region) to keep
#      the invocation local to the demo's region.
###############################################################################

data "aws_caller_identity" "current" {}

locals {
  # The IAM grant covers the active model AND the declared fallbacks, so an
  # operator can flip studio_ai_model between them (e.g. Opus 5 <-> the
  # verified Sonnet 4.6 fallback while the Opus 5 entitlement propagates)
  # with a plain env change and no IAM edit.
  studio_ai_granted_models = distinct(concat(
    [var.studio_ai_model],
    var.studio_ai_fallback_models
  ))

  # Member regions a `us.` cross-region inference profile may dispatch the
  # request to. Invoking through an inference profile requires BOTH the
  # inference-profile ARN (in the calling region) AND the foundation-model
  # ARNs in EVERY member region — Bedrock routes wherever capacity is, and
  # granting only the profile (or only one region's foundation model) yields
  # AccessDenied. Same shape as the aws-serverless module's
  # WorkflowGeneration grant (bedrock.tf). Verified against the live account
  # (2026-07-24, `aws bedrock get-inference-profile`): us.anthropic.
  # claude-opus-5 and us.anthropic.claude-sonnet-4-6 are both ACTIVE and
  # route to foundation-model/<id without the us. prefix> in exactly these
  # three regions.
  studio_ai_member_regions = ["us-east-1", "us-east-2", "us-west-2"]

  studio_ai_invoke_resources = distinct(concat(
    # 1. The inference-profile ARNs in the calling region — account-scoped.
    [
      for model in local.studio_ai_granted_models :
      "arn:aws:bedrock:${var.studio_ai_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${model}"
    ],
    # 2. The foundation-model ARNs (account-agnostic) in each member region.
    #    The `us.` (or other geo) prefix denotes a cross-region inference
    #    profile; the underlying foundation-model id is the same id with
    #    that prefix stripped.
    flatten([
      for model in local.studio_ai_granted_models : [
        for region in local.studio_ai_member_regions :
        "arn:aws:bedrock:${region}::foundation-model/${replace(model, "/^[a-z]{2}\\./", "")}"
      ]
    ])
  ))

  # Exact server configuration keys (StudioAiProxyConfiguration binds section
  # "StudioAiProxy"; provider blocks live under Providers__<name>), expressed
  # in ASP.NET Core double-underscore env-var form. The `bedrock` kind needs
  # no Endpoint and no ApiKey; MaxTokens (4096) and TimeoutSeconds (120) stay
  # at the server defaults.
  studio_ai_environment = var.enable_studio_ai ? {
    StudioAiProxy__Enabled                    = "true"
    StudioAiProxy__DefaultProvider            = "bedrock"
    StudioAiProxy__Providers__bedrock__Kind   = "bedrock"
    StudioAiProxy__Providers__bedrock__Model  = var.studio_ai_model
    StudioAiProxy__Providers__bedrock__Region = var.studio_ai_region
  } : {}
}

# Least-privilege Bedrock invoke grant for the Studio AI proxy. Deliberately a
# separate inline policy from the module-managed WorkflowGeneration grant
# (aws_iam_role_policy.lambda_bedrock_invoke inside the module) so the two
# AI add-ons toggle and version their model grants independently. The role
# name is derived in seed-data.tf (local.honua_lambda_role_name) from the
# deployed function's role ARN — the module does not export the role name.
resource "aws_iam_role_policy" "lambda_studio_ai_bedrock" {
  count = var.enable_studio_ai ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-lambda-studio-ai-bedrock"
  role  = local.honua_lambda_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Bedrock has no separate Converse IAM actions — the Converse /
        # ConverseStream APIs authorize against bedrock:InvokeModel and
        # bedrock:InvokeModelWithResponseStream on the same resources.
        Sid    = "StudioAiInvokeBedrockClaudeModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = local.studio_ai_invoke_resources
      }
    ]
  })
}
