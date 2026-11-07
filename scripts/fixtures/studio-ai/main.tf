terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.0, < 7.0" }
  }
}

variable "name_prefix" { default = "honua-demo" }
variable "environment" { default = "demo" }

locals {
  honua_lambda_role_name = "fixture-serving-role"
}
