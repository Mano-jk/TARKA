mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b"]
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = jsonencode({
        Version = "2012-10-17"
        Statement = [{
          Effect    = "Allow"
          Action    = "sts:AssumeRole"
          Principal = { Service = "ecs-tasks.amazonaws.com" }
        }]
      })
    }
  }
}

run "rejects_fail_open_deployment" {
  command = plan

  variables {
    certificate_arn        = "arn:aws:acm:us-east-1:123456789012:certificate/test"
    proxy_domain_name      = "tarka.test.internal"
    private_hosted_zone_id = "ZTEST"
    image_tag              = "test"
    upstream_api_key       = "test-upstream-key"
    proxy_api_key          = "test-proxy-api-key-0123456789012345"
    fail_policy            = "fail_open"
  }

  expect_failures = [var.fail_policy]
}
