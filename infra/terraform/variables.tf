variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "tarka"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "allowed_client_cidrs" {
  type        = list(string)
  default     = []
  description = "Approved private client CIDRs allowed to reach the internal HTTPS load balancer."

  validation {
    condition     = alltrue([for cidr in var.allowed_client_cidrs : can(cidrhost(cidr, 0))])
    error_message = "Every allowed_client_cidrs entry must be a valid CIDR."
  }
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate ARN for the internal HTTPS listener."
}

variable "proxy_domain_name" {
  type        = string
  description = "Private DNS name covered by certificate_arn."
}

variable "private_hosted_zone_id" {
  type        = string
  description = "Route 53 private hosted zone associated with the VPC."
}

variable "image_tag" {
  type        = string
  description = "Immutable container tag, preferably the source commit SHA."
}

variable "upstream_base_url" {
  type    = string
  default = "https://api.openai.com/v1"
}

variable "allowed_upstream_cidrs" {
  type        = list(string)
  default     = []
  description = "Approved IPv4 destinations allowed TCP 443 egress from tasks; empty blocks outbound application traffic."

  validation {
    condition     = alltrue([for cidr in var.allowed_upstream_cidrs : can(cidrnetmask(cidr))])
    error_message = "Every allowed_upstream_cidrs entry must be a valid CIDR."
  }
}

variable "upstream_api_key" {
  type        = string
  sensitive   = true
  description = "Stored in Secrets Manager and Terraform state; secure the remote state bucket."
}

variable "proxy_api_key" {
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
  description = "Single-tenant bearer key. Set this or tenant_api_keys_json."

  validation {
    condition     = var.proxy_api_key == null ? true : length(var.proxy_api_key) >= 32
    error_message = "proxy_api_key must be null or contain at least 32 characters."
  }
}

variable "tenant_api_keys_json" {
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
  description = "JSON object mapping tenant IDs to unique bearer keys; stored in Terraform state."

  validation {
    condition = var.tenant_api_keys_json == null ? true : try(
      length(keys(jsondecode(var.tenant_api_keys_json))) > 0 &&
      length(keys(jsondecode(var.tenant_api_keys_json))) <= 100 &&
      alltrue([
        for tenant_id, api_key in jsondecode(var.tenant_api_keys_json) :
        length(tenant_id) > 0 &&
        length(tenant_id) <= 128 &&
        length(api_key) >= 32
      ]) &&
      length(distinct(values(jsondecode(var.tenant_api_keys_json)))) ==
      length(keys(jsondecode(var.tenant_api_keys_json))),
      false
    )
    error_message = "tenant_api_keys_json must map 1-100 tenant IDs to unique keys of at least 32 characters."
  }
}

variable "max_retries" {
  type    = number
  default = 2

  validation {
    condition     = var.max_retries >= 0 && floor(var.max_retries) == var.max_retries
    error_message = "max_retries must be a non-negative integer."
  }
}

variable "fail_policy" {
  type        = string
  default     = "fail_closed"
  description = "AWS deployments must fail closed; fail-open behavior is only for local experimentation."

  validation {
    condition     = var.fail_policy == "fail_closed"
    error_message = "AWS deployments must use fail_closed; fail_open is not permitted."
  }
}

variable "rate_limit_per_minute" {
  type    = number
  default = 60

  validation {
    condition     = var.rate_limit_per_minute >= 1 && var.rate_limit_per_minute <= 10000 && floor(var.rate_limit_per_minute) == var.rate_limit_per_minute
    error_message = "rate_limit_per_minute must be an integer from 1 to 10000."
  }
}