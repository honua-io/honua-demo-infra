variable "region" {
  description = "AWS region containing the exact candidate and live alias."
  type        = string
  default     = "us-west-2"
}

variable "primary_state_backend" {
  description = "Backend used to read the authoritative demo root outputs. local is test-only."
  type        = string
  default     = "s3"

  validation {
    condition     = contains(["s3", "local"], var.primary_state_backend)
    error_message = "primary_state_backend must be s3 or local."
  }
}

variable "primary_state_config" {
  description = "Read-only backend configuration for the authoritative demo root state."
  type        = map(string)
  default = {
    bucket       = "honua-tfstate-585192672263"
    key          = "demo/aws-demo/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = "true"
    use_lockfile = "true"
  }
}

variable "offline_plan" {
  description = "Disable AWS provider account checks only for the local-state executable plan contract."
  type        = bool
  default     = false
}

check "offline_plan_is_local_only" {
  assert {
    condition     = !var.offline_plan || var.primary_state_backend == "local"
    error_message = "offline_plan may be enabled only with the local test-state backend."
  }
}
