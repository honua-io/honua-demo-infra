terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4, < 3.0"
    }
  }

  # The helper is deliberately outside the demo application's dependency
  # graph. Never move this state back into demo/aws-demo/terraform.tfstate.
  backend "s3" {
    bucket       = "honua-tfstate-585192672263"
    key          = "demo/aws-demo/candidate-preflight.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  # These switches exist only for the executable offline-plan contract test.
  # The guard in variables.tf rejects offline mode with the live S3 handoff.
  skip_credentials_validation = var.offline_plan
  skip_metadata_api_check     = var.offline_plan
  skip_region_validation      = var.offline_plan
  skip_requesting_account_id  = var.offline_plan
}
