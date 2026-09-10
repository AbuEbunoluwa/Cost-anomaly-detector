# dynamodb.tf — storage for the Cloud Cost Anomaly Detector
#
# Partition key: service (string) — e.g. "Amazon EC2"
# Sort key:      date (string, YYYY-MM-DD)
#
# On-demand billing since usage is one write per service/day — far too
# low and spiky to justify provisioned capacity.

resource "aws_dynamodb_table" "cost_anomaly_detector" {
  name         = "cost-anomaly-detector"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "service"
  range_key = "date"

  attribute {
    name = "service"
    type = "S"
  }

  attribute {
    name = "date"
    type = "S"
  }

  # Point-in-time recovery is cheap insurance for a table you'll query
  # for historical trend analysis later.
  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project   = "cloud-cost-anomaly-detector"
    ManagedBy = "terraform"
  }
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table — pass this to detect.py via --table if you change it here."
  value       = aws_dynamodb_table.cost_anomaly_detector.name
}
