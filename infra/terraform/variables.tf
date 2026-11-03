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

variable "upstream_api_key" {
  type        = string
  sensitive   = true
  description = "Stored in Secrets Manager and Terraform state; secure the remote state bucket."
}

variable "proxy_api_key" {
  type        = string
  sensitive   = true
  description = "Client bearer key stored in Secrets Manager and Terraform state."

  validation {
    condition     = length(var.proxy_api_key) >= 32
    error_message = "proxy_api_key must contain at least 32 characters."
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
  type    = string
  default = "fail_closed"

  validation {
    condition     = contains(["fail_closed", "fail_open"], var.fail_policy)
    error_message = "fail_policy must be fail_closed or fail_open."
  }
}