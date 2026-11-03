resource "aws_ecs_cluster" "tarka" {
  name = local.name
}

resource "aws_security_group" "load_balancer" {
  name        = "${local.name}-alb"
  description = "Private HTTPS access from approved client CIDRs."
  vpc_id      = aws_vpc.main.id

  dynamic "ingress" {
    for_each = var.allowed_client_cidrs

    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name        = "${local.name}-task"
  description = "Allow application traffic only from the private load balancer."
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.load_balancer.id]
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "tarka" {
  name               = "${local.name}-alb"
  internal           = true
  load_balancer_type = "application"
  subnets            = [for subnet in aws_subnet.private : subnet.id]
  security_groups    = [aws_security_group.load_balancer.id]
}

resource "aws_lb_target_group" "tarka" {
  name        = "${local.name}-tg"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    enabled             = true
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.tarka.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.tarka.arn
  }
}

resource "aws_route53_record" "tarka" {
  zone_id = var.private_hosted_zone_id
  name    = var.proxy_domain_name
  type    = "A"

  alias {
    name                   = aws_lb.tarka.dns_name
    zone_id                = aws_lb.tarka.zone_id
    evaluate_target_health = true
  }
}

resource "aws_ecs_task_definition" "tarka" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "tarka"
    image     = "${aws_ecr_repository.tarka.repository_url}:${var.image_tag}"
    essential = true
    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]
    environment = [
      { name = "TARKA_UPSTREAM_BASE_URL", value = var.upstream_base_url },
      { name = "TARKA_MAX_RETRIES", value = tostring(var.max_retries) },
      { name = "TARKA_FAIL_POLICY", value = var.fail_policy },
    ]
    secrets = [
      {
        name      = "TARKA_UPSTREAM_API_KEY"
        valueFrom = aws_secretsmanager_secret.upstream_api_key.arn
      },
      {
        name      = "TARKA_PROXY_API_KEY"
        valueFrom = aws_secretsmanager_secret.proxy_api_key.arn
      }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tarka.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }])
}

resource "aws_ecs_service" "tarka" {
  name             = local.name
  cluster          = aws_ecs_cluster.tarka.id
  task_definition  = aws_ecs_task_definition.tarka.arn
  desired_count    = 1
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets          = [for subnet in aws_subnet.private : subnet.id]
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.tarka.arn
    container_name   = "tarka"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https]
}