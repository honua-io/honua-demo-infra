output "honua_url" {
  description = "Public URL for the demo environment (https://demo.honua.io once DNS is live)."
  value       = "https://demo.honua.io"
}

output "api_gateway_endpoint" {
  description = "Raw API Gateway HTTP API endpoint (use for health checks before Route53 propagates)."
  value       = module.honua.api_endpoint
}

output "custom_domain_target" {
  description = "API Gateway regional domain name — the demo.honua.io alias target only while route_demo_dns_to_cloudfront=false (steady state points at CloudFront instead)."
  value       = aws_apigatewayv2_domain_name.demo.domain_name_configuration[0].target_domain_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (use for cache invalidations after reseeds)."
  value       = aws_cloudfront_distribution.demo.id
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain (*.cloudfront.net) — use to validate the CDN before/without the demo.honua.io DNS swap."
  value       = aws_cloudfront_distribution.demo.domain_name
}

output "certificate_arn" {
  description = "ACM certificate ARN issued for demo.honua.io."
  value       = aws_acm_certificate.demo.arn
}

output "lambda_function_name" {
  description = "Lambda function name (for aws lambda commands and EventBridge invoke targets)."
  value       = module.honua.lambda_function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN."
  value       = module.honua.lambda_function_arn
}

output "lambda_alias_name" {
  description = "Stable Lambda alias used by API Gateway."
  value       = module.honua.lambda_alias_name
}

output "lambda_alias_arn" {
  description = "Lambda alias ARN."
  value       = module.honua.lambda_alias_arn
}

output "db_endpoint" {
  description = "RDS endpoint (sensitive)."
  value       = module.honua.db_endpoint
  sensitive   = true
}

output "db_connection_string" {
  description = "Database connection string (sensitive)."
  value       = module.honua.db_connection_string
  sensitive   = true
}

output "control_plane_target_kind" {
  description = "Honua control-plane target kind hint."
  value       = module.honua.control_plane_target_kind
}

output "control_plane_backend_name" {
  description = "Honua control-plane backend identifier hint."
  value       = module.honua.control_plane_backend_name
}

output "control_plane_target_id" {
  description = "Honua control-plane target ID."
  value       = module.honua.control_plane_target_id
}

output "gp_batch_enabled" {
  description = "Whether GP-over-Batch was provisioned for the demo."
  value       = module.honua.gp_batch_enabled
}

output "gp_job_queue_arn" {
  description = "GP Fargate Spot Batch job queue ARN (null unless enable_gp_batch)."
  value       = module.honua.gp_job_queue_arn
}

output "gp_job_definition_arns" {
  description = "Map of GP job-definition size tier => ARN ({ s, m, l, xl }); null unless enable_gp_batch."
  value       = module.honua.gp_job_definition_arns
}

# ---------------------------------------------------------------------------
# Pro + AI demo add-ons (see README → "Pro + AI demo drift")
# ---------------------------------------------------------------------------

output "pro_license_enabled" {
  description = "Whether the demo Lambda is configured to load a signed Pro license from Secrets Manager."
  value       = module.honua.pro_license_enabled
}

output "pro_license_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the signed Pro license envelope (null unless enable_pro_license). The secret is managed OUTSIDE Terraform and adopted by ARN — there is nothing to import, and Terraform never reads or rewrites its value."
  value       = module.honua.pro_license_secret_arn
}

output "redis_connection_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the Redis connection string (null unless enable_redis)."
  value       = module.honua.redis_connection_secret_arn
  sensitive   = true
}

output "bedrock_vpc_endpoint_id" {
  description = "ID of the bedrock-runtime interface VPC endpoint (null unless enable_bedrock_ai). Live id: vpce-003090af73dc835fe — adopt via terraform import."
  value       = var.enable_bedrock_ai ? aws_vpc_endpoint.bedrock_runtime[0].id : null
}
