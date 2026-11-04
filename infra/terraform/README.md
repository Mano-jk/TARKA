# Terraform Dev Environment

This root creates a private VPC, an internal HTTPS ALB, private ECS Fargate tasks, ECR, CloudWatch logs, and two Secrets Manager values. It does not create or apply an AWS environment automatically.

## Required approvals and inputs

- An AWS account, region, credentials, spend approval, and an existing S3 state bucket approved for Terraform state.
- A private Route 53 hosted zone associated with the VPC, plus an ACM certificate in the same region whose name covers `proxy_domain_name`.
- Approved client CIDRs. The example defaults to an empty list, which leaves the private ALB inaccessible until the owner supplies allowed ranges.
- A source-commit image tag pushed to the created ECR repository before the full service apply.

The ALB is internal and has no port-80 listener. TLS terminates at the ALB; ALB-to-task traffic is HTTP inside private subnets. Task egress is blocked by default. The cloud owner must supply the approved IPv4 destination CIDRs for the upstream in `allowed_upstream_cidrs`; only TCP 443 to those CIDRs is enabled. Provider address ranges can change, so the owner must maintain the allow-list or use an approved egress proxy/firewall. Empty CIDRs intentionally prevent upstream requests. The dev service uses one Fargate task and one NAT Gateway, so it is not highly available. NAT and ALB resources incur ongoing charges.

## State and secrets

Copy the templates, then fill in the backend bucket, private DNS/certificate, approved client CIDRs, and approved upstream CIDRs:

```powershell
Copy-Item backend.hcl.example backend.hcl
Copy-Item dev.tfvars.example dev.tfvars
```

The S3 bucket must be bootstrapped separately or provided by the cloud owner. Require bucket versioning, server-side encryption, S3 Block Public Access, and an IAM policy restricted to the deployment role. Native S3 lockfiles are enabled in `backend.hcl`.

Terraform manages secret versions from sensitive input variables. The values are redacted in normal CLI output but are stored in Terraform state. Protect the state bucket accordingly, do not commit state, `.tfvars`, backend config, or saved plan files, and do not share plans or state exports.

Choose exactly one client authentication mode:

- Single tenant: provide `TF_VAR_proxy_api_key` as a key with at least 32 characters.
- Multiple tenants: provide `TF_VAR_tenant_api_keys_json` as a JSON object mapping unique tenant IDs to keys with at least 32 characters. The container receives this value as `TARKA_TENANT_API_KEYS`; do not also set the single-tenant key.

The service enforces a process-local per-tenant token bucket, configured by `rate_limit_per_minute` (default 60). It is not a distributed quota; retain an approved edge/API-gateway limit when adding replicas.
AWS deployments require `fail_policy = "fail_closed"`; Terraform rejects fail-open configuration because it can return an unverified completion after a verification failure. Use fail-open only for isolated local experiments, never for a protected side effect.

For example, set the multi-tenant variable from a secure prompt rather than putting the value in command history:

```powershell
$secureTenantKeys = Read-Host "Tenant API keys JSON" -AsSecureString
$env:TF_VAR_tenant_api_keys_json = [System.Net.NetworkCredential]::new("", $secureTenantKeys).Password
```

## Build and deploy

Run these commands from `infra/terraform` after configuring AWS CLI credentials and editing `dev.tfvars` and `backend.hcl`:

```powershell
$secureUpstreamKey = Read-Host "Upstream API key" -AsSecureString
$env:TF_VAR_upstream_api_key = [System.Net.NetworkCredential]::new("", $secureUpstreamKey).Password
# Set exactly one client authentication variable as described above.
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
Remove-Item Env:TF_VAR_upstream_api_key,Env:TF_VAR_proxy_api_key,Env:TF_VAR_tenant_api_keys_json
```

Clear these environment variables if you abort before destroy as well. Secrets Manager uses a seven-day recovery window, so destroyed secrets are scheduled for deletion rather than immediately reusable.
