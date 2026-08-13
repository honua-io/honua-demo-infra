# Candidate-preflight Terraform root

This root owns only the credential-safe candidate-preflight Lambda, its role,
inline policy, and log group. Its S3 state key is
`demo/aws-demo/candidate-preflight.tfstate`, separate from the primary demo
root at `demo/aws-demo/terraform.tfstate`.

The helper never reads the primary Terraform state. It uses metadata-only
`DescribeSecret` for the exact module-owned, account/region-unique name
`honua-demo-demo/admin-password`. That returns the authoritative ARN without
reading `SecretString`; the helper never hard-codes or reconstructs the random
suffix.

Every saved plan must pass the plan-JSON and reviewed-source allowlists before
an independently authorized apply.
