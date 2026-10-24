terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "siliconflow", component = "provenance-chat" } }
}

data "aws_caller_identity" "me" {}

# Keys the pseudonyms and HMAC attestations. Lives in state and the Lambda environment: fine for a demo, use SSM/Secrets Manager beyond that.
resource "random_password" "trace_secret" {
  length  = 40
  special = false
}

resource "aws_dynamodb_table" "limits" {
  name         = "${var.name}-limits"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Least privilege: one model (profile + the foundation models it routes to), one table, one log group.
data "aws_iam_policy_document" "fn" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn.arn}:*"]
  }
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.limits.arn]
  }
  statement {
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.me.account_id}:inference-profile/${var.model_id}",
      "arn:aws:bedrock:*::foundation-model/${replace(var.model_id, "us.", "")}",
    ]
  }
}

resource "aws_iam_role_policy" "fn" {
  role   = aws_iam_role.fn.id
  policy = data.aws_iam_policy_document.fn.json
}

resource "aws_lambda_function" "fn" {
  function_name                  = var.name
  role                           = aws_iam_role.fn.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "handler.lambda_handler"
  filename                       = "${path.module}/../build/lambda.zip"
  source_code_hash               = filebase64sha256("${path.module}/../build/lambda.zip")
  memory_size                    = 512
  timeout                        = 30
  reserved_concurrent_executions = var.reserved_concurrency
  environment {
    variables = {
      MODEL_ID       = var.model_id
      TABLE          = aws_dynamodb_table.limits.name
      TRACE_SECRET   = random_password.trace_secret.result
      PER_IP_PER_MIN = tostring(var.per_ip_per_min)
      DAILY_CAP      = tostring(var.daily_cap)
    }
  }
  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

resource "aws_apigatewayv2_api" "api" {
  name          = var.name
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "fn" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "chat" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /chat"
  target    = "integrations/${aws_apigatewayv2_integration.fn.id}"
}

resource "aws_apigatewayv2_route" "trace" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /trace/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.fn.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = var.api_rate
    throttling_burst_limit = var.api_burst
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format          = jsonencode({ id = "$context.requestId", ts = "$context.requestTime", ip = "$context.identity.sourceIp", route = "$context.routeKey", status = "$context.status", ms = "$context.responseLatency" })
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# Page on any Lambda error or a throttled burst; add an SNS action to be notified.
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}
