# Candidate-preflight Terraform root

This root owns only the credential-safe candidate-preflight Lambda, its role,
inline policy, and log group. Its S3 state key is
`demo/aws-demo/candidate-preflight.tfstate`, separate from the primary demo
root at `demo/aws-demo/terraform.tfstate`.

The helper reads `admin_password_secret_arn`, `lambda_function_arn`, and
`lambda_function_name` from the primary root's persisted outputs. In the
primary configuration, `admin_password_secret_arn` is exactly
`module.honua.admin_password_secret_arn`; the helper never hard-codes or
reconstructs the random-suffix secret ARN.

Do not plan or apply this root until the output-handoff gate in
`runbook/candidate-preflight-v1.md` passes. Every saved plan must pass the
plan-JSON allowlist before an independently authorized apply.
