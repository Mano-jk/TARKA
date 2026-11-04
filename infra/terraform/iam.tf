resource "aws_ecr_repository" "tarka" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "tarka" {
  repository = aws_ecr_repository.tarka.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the most recent 20 images."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "tarka" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

resource "aws_secretsmanager_secret" "upstream_api_key" {
  name                    = "${local.name}/upstream-api-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "upstream_api_key" {
  secret_id     = aws_secretsmanager_secret.upstream_api_key.id
  secret_string = var.upstream_api_key
}

resource "terraform_data" "auth_config" {
  input = "validated"

  lifecycle {
    precondition {
      condition     = (var.proxy_api_key == null) != (var.tenant_api_keys_json == null)
      error_message = "Configure exactly one of proxy_api_key or tenant_api_keys_json."
    }
  }
}

resource "aws_secretsmanager_secret" "proxy_api_key" {
  count                   = var.proxy_api_key == null ? 0 : 1
  name                    = "${local.name}/proxy-api-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "proxy_api_key" {
  count         = var.proxy_api_key == null ? 0 : 1
  secret_id     = aws_secretsmanager_secret.proxy_api_key[0].id
  secret_string = var.proxy_api_key
}

resource "aws_secretsmanager_secret" "tenant_api_keys" {
  count                   = var.tenant_api_keys_json == null ? 0 : 1
  name                    = "${local.name}/tenant-api-keys"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "tenant_api_keys" {
  count         = var.tenant_api_keys_json == null ? 0 : 1
  secret_id     = aws_secretsmanager_secret.tenant_api_keys[0].id
  secret_string = var.tenant_api_keys_json
}

data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_application_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = flatten([
      [aws_secretsmanager_secret.upstream_api_key.arn],
      aws_secretsmanager_secret.proxy_api_key[*].arn,
      aws_secretsmanager_secret.tenant_api_keys[*].arn,
    ])
  }
}

resource "aws_iam_role_policy" "read_application_secrets" {
  name   = "read-application-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_application_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}