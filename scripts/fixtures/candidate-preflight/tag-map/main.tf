terraform {
  required_version = ">= 1.10, < 2.0"
}

variable "provider_tags" {
  description = "Provider-shaped secret tags."
  type        = map(string)
}

locals {
  expected_tags = {
    Project     = "honua-server"
    Environment = "demo"
    ManagedBy   = "terraform"
    Purpose     = "public-demo"
  }
}

resource "terraform_data" "exact_tag_map" {
  input = var.provider_tags

  lifecycle {
    precondition {
      condition     = var.provider_tags == tomap(local.expected_tags)
      error_message = "provider-shaped tags must exactly match the expected map."
    }
  }
}

output "legacy_object_comparison" {
  value = var.provider_tags == local.expected_tags
}

output "exact_map_comparison" {
  value = var.provider_tags == tomap(local.expected_tags)
}
