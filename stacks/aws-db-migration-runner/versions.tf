terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }

  backend "s3" {
    bucket       = "honua-tfstate-585192672263"
    key          = "demo/aws-demo/db-migration-runner.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-west-2"
  allowed_account_ids = ["585192672263"]
}
