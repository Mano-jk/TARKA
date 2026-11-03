# Terraform Dev Environment

This root creates a private VPC, an internal HTTPS ALB, private ECS Fargate tasks, ECR, CloudWatch logs, and two Secrets Manager values. It does not create or apply an AWS environment automatically.

## Required approvals and inputs

- An AWS account, region, credentials, spend approval, and an existing S3 state bucket approved for Terraform state.
- A private Route 53 hosted zone associated with the VPC, plus an ACM certificate in the same region whose name covers `proxy_domain_name`.
- Approved client CIDRs. The example defaults to an empty list, which leaves the private ALB inaccessible until the owner supplies allowed ranges.
- A source-commit image tag pushed to the created ECR repository before the full service apply.

The ALB is internal and has no port-80 listener. TLS terminates at the ALB; ALB-to-task traffic is HTTP inside private subnets. The task security group permits outbound TCP 443 to any IPv4 destination through NAT to support configurable upstreams and AWS services; this is not a destination allow-list. The cloud owner must approve this egress or require a controlled egress proxy/firewall before production. The dev service uses one Fargate task and one NAT Gateway, so it is not highly available. NAT and ALB resources incur ongoing charges.

## State and secrets

Copy the templates, then fill in the backend bucket, private DNS/certificate, and approved client CIDRs:

```powershell
Copy-Item backend.hcl.example backend.hcl
Copy-Item dev.tfvars.example dev.tfvars
```

The S3 bucket must be bootstrapped separately or provided by the cloud owner. Require bucket versioning, server-side encryption, S3 Block Public Access, and an IAM policy restricted to the deployment role. Native S3 lockfiles are enabled in `backend.hcl`.

Terraform manages secret versions from sensitive input variables. The values are redacted in normal CLI output but are stored in Terraform state. Protect the state bucket accordingly, do not commit state, `.tfvars`, backend config, or saved plan files, and do not share plans or state exports.

## Build and deploy

Run these commands from `infra/terraform` after configuring AWS CLI credentials and editing `dev.tfvars` and `backend.hcl`:

```powershell
$secureUpstreamKey = Read-Host "Upstream API key" -AsSecureString
$env:TF_VAR_upstream_api_key = [System.Net.NetworkCredential]::new("", $secureUpstreamKey).Password
$secureProxyKey = Read-Host "Proxy API key (at least 32 characters)" -AsSecureString
$env:TF_VAR_proxy_api_key = [System.Net.NetworkCredential]::new("", $secureProxyKey).Password
$commit = (git rev-parse --short HEAD).Trim()
$buildStamp = [DateTime]::UtcNow.ToString("yyyyMMddHHmmssfff")
$buildSuffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
$imageTag = "$commit-$buildStamp-$buildSuffix"

terraform init -backend-config=backend.hcl
terraform validate
terraform apply -target=aws_ecr_repository.tarka -target=aws_ecr_lifecycle_policy.tarka -var-file=dev.tfvars -var "image_tag=$imageTag"

$region = (terraform output -raw aws_region).Trim()
$repository = (terraform output -raw ecr_repository_url).Trim()
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $repository
$image = "${repository}:$imageTag"
docker build --tag $image ../..
docker push $image

terraform plan -var-file=dev.tfvars -var "image_tag=$imageTag"
terraform apply -var-file=dev.tfvars -var "image_tag=$imageTag"
terraform output -raw proxy_private_url
```

The targeted apply bootstraps only the ECR repository so the immutable image can be pushed before ECS starts. The unique tag starts with the commit SHA and avoids collisions when rebuilding the same commit. Review the full plan before applying. Terraform destroy is available after approval:

```powershell
terraform destroy -var-file=dev.tfvars -var "image_tag=$imageTag"
Remove-Item Env:TF_VAR_upstream_api_key,Env:TF_VAR_proxy_api_key
```

Clear these environment variables if you abort before destroy as well. Secrets Manager uses a seven-day recovery window, so destroyed secrets are scheduled for deletion rather than immediately reusable.
