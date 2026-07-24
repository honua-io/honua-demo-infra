terraform {
  # >= 1.10 for S3-native state locking (use_lockfile) in the backend below.
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
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5, < 4.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2, < 4.0"
    }
  }

  # Remote state. The demo's state previously lived only as a local
  # terraform.tfstate on one workstation — live infrastructure with a
  # bus-factor of one and no history. This adopts the existing
  # honua-tfstate-<account-id> bucket (versioned, SSE-AES256), matching the
  # cert environment's key convention (<env>/<example>/terraform.tfstate).
  #
  # region is the BUCKET's region (us-east-1), not var.region (us-west-2)
  # where the demo's resources live. These are deliberately different.
  #
  # use_lockfile is S3-native conditional-write locking (Terraform >= 1.10),
  # which replaces the dynamodb_table this block used to name — that table
  # (honua-tfstate-lock) never existed, so the documented config could not
  # have worked as written.
  backend "s3" {
    bucket       = "honua-tfstate-585192672263"
    key          = "demo/aws-demo/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}
