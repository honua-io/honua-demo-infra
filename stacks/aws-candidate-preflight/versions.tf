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
  region              = "us-west-2"
  allowed_account_ids = ["585192672263"]
}
