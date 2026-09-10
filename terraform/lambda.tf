# lambda.tf — packaging, IAM, and scheduling for the Cloud Cost Anomaly Detector
#
# Packages src/ into a zip, deploys it as a Lambda function with a
# least-privilege IAM role, and triggers it daily via EventBridge.
#
# Requires: DynamoDB table already defined in dynamodb.tf, and a Slack
# incoming webhook URL stored in SSM Parameter Store (see slack_webhook_url
# variable below — passed in via terraform.tfvars or -var, never committed).

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL — pass via terraform.tfvars (gitignored) or -var, never commit this."
  type        = string
  sensitive   = true
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

# ---------------------------------------------------------------------------
# Store the webhook URL in SSM Parameter Store rather than a plaintext
# Lambda environment variable, so it's not visible in the Lambda console's
# environment variable list or in `terraform show` output by default.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "slack_webhook_url" {
  name  = "/cost-anomaly-detector/slack-webhook-url"
  type  = "SecureString"
  value = var.slack_webhook_url

  tags = {
    Project = "cloud-cost-anomaly-detector"
  }
}

# ---------------------------------------------------------------------------
# Package src/ into a deployable zip
# ---------------------------------------------------------------------------
data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/lambda_package.zip"
}

# ---------------------------------------------------------------------------
# IAM role — least privilege: only what the Lambda actually needs
# ---------------------------------------------------------------------------
resource "aws_iam_role" "lambda_exec" {
  name = "cost-anomaly-detector-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "cost-anomaly-detector-lambda-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CostExplorerReadOnly"
        Effect   = "Allow"
        Action   = ["ce:GetCostAndUsage"]
        Resource = "*" # Cost Explorer does not support resource-level restriction
      },
      {
        Sid      = "DynamoDbWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.cost_anomaly_detector.arn
      },
      {
        Sid      = "SsmReadWebhook"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.slack_webhook_url.arn
      },
      {
        # SecureString parameters are encrypted with the AWS-managed SSM key
        # by default. Reading with WithDecryption=true requires this too —
        # scoped via condition to calls made through SSM, not a blanket grant.
        Sid      = "KmsDecryptForSsm"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com" }
        }
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/cost-anomaly-detector*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda function
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "cost_anomaly_detector" {
  function_name    = "cost-anomaly-detector"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE_NAME     = aws_dynamodb_table.cost_anomaly_detector.name
      LOOKBACK_DAYS           = "30"
      DETECTION_WINDOW        = "14"
      DETECTION_THRESHOLD     = "2.0"
      NOTIFY_ON_CLEAN_RUN     = "false"
      SLACK_WEBHOOK_SSM_PARAM = aws_ssm_parameter.slack_webhook_url.name
    }
  }
}

# ---------------------------------------------------------------------------
# EventBridge — daily trigger
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "daily_trigger" {
  name                = "cost-anomaly-detector-daily"
  description         = "Triggers the cost anomaly detector once a day"
  schedule_expression = "cron(0 8 * * ? *)" # 08:00 UTC daily — adjust to your timezone preference
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.daily_trigger.name
  target_id = "cost-anomaly-detector-lambda"
  arn       = aws_lambda_function.cost_anomaly_detector.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_anomaly_detector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_trigger.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.cost_anomaly_detector.function_name
}
