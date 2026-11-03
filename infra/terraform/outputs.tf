output "proxy_private_url" {
  description = "Private URL reachable from within the VPC or an approved connection."
  value       = "https://${var.proxy_domain_name}"
}

output "ecr_repository_url" {
  description = "ECR repository used for the TARKA proxy image."
  value       = aws_ecr_repository.tarka.repository_url
}

output "aws_region" {
  description = "AWS region configured for the Terraform provider."
  value       = var.aws_region
}