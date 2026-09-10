# Cloud Cost Anomaly Detector

A serverless tool that watches AWS spend day-to-day, flags unusual cost
spikes automatically, and alerts a Slack channel, so you find out about
a runaway EC2 instance or misconfigured Lambda the same day, not when
the bill arrives.

## Why this exists

I woke up to a massive AWS bill that had spiked overnight. The cause:
orphaned EC2 and S3 resources, left running after I'd launched a few
instances to set up a multi-node, highly available kubeadm cluster. I
never expected that setup to leave anything behind, but it did, and the
bill made it very clear.

That morning made one thing obvious. I needed to know about a cost spike
before it hit my invoice, not after. So I built this. It watches my AWS
spend every day and reports anything unusual straight to Slack, ahead of
the bill, not in reaction to it.

## Architecture

```
EventBridge (daily cron trigger)
        │
        ▼
   Lambda (handler.py)
        │
        ├──> Cost Explorer API (ingest.py): pulls last 30 days of cost
        │      data, grouped by AWS service
        │
        ├──> Detection logic (detect.py): rolling mean/std-dev per
        │      service, flags days that exceed the threshold
        │
        ├──> DynamoDB: every evaluated service-day is written here,
        │      building a queryable history over time
        │
        └──> SSM Parameter Store: Slack webhook URL is stored here as
               a SecureString and fetched + decrypted at runtime, never
               hardcoded or passed as a plaintext env var
                        │
                        ▼
                 Slack (alert.py): posts a message only when an
                 anomaly is actually found (silent on clean runs,
                 to avoid daily noise)
```

Everything is provisioned via Terraform: the DynamoDB table, the IAM
role (scoped to exactly the four permissions the Lambda needs, nothing
broader), the Lambda function itself, and the EventBridge schedule that
triggers it once a day.

## Why EventBridge

EventBridge's scheduled rule replaces what would otherwise be a cron job
on a server you'd have to provision, patch, and pay for continuously.
The rule fires on a schedule (`cron(0 8 * * ? *)`, daily at 08:00 UTC)
and invokes the Lambda directly. No server ever sits idle waiting for
that moment. This is the standard AWS pattern for "run this small thing
on a schedule" in a serverless architecture.

## Project structure

```
anomaly-detector/
├── src/
│   ├── ingest.py                   # Pulls cost data from Cost Explorer
│   ├── detect.py                   # Rolling mean/std-dev anomaly detection
│   ├── alert.py                    # Slack webhook formatting + sending
│   ├── handler.py                  # Lambda entrypoint, ties it all together
│   └── generate_synthetic_data.py  # Dev/testing tool, see note below
├── terraform/
│   ├── dynamodb.tf                 # Storage for cost snapshots + anomalies
│   └── lambda.tf                   # Lambda, IAM role, EventBridge schedule
├── tests/                          # 44 tests covering all of the above
└── requirements.txt
```

## Setup

```
pip install -r requirements.txt
```

AWS credentials must be configured (`aws configure` or environment
variables), and Cost Explorer must be enabled once in the AWS Billing
console (can take up to 24h to activate on a new account).

Create a Slack incoming webhook (Slack workspace, Apps, Incoming
Webhooks) and store it in a gitignored `terraform.tfvars`:

```
slack_webhook_url = "https://hooks.slack.com/services/..."
```

Deploy everything:

```
cd terraform
terraform init
terraform apply
```

## Running it

The Lambda runs automatically once a day via EventBridge. To test it
manually rather than waiting for the schedule:

```
aws lambda invoke --function-name cost-anomaly-detector --region us-east-1 response.json
cat response.json
```

Each individual step can also be run locally for development:

```
python src/ingest.py --days 30
python src/detect.py --input data/cost_data.json
```

## Real-world validation

`ingest.py` runs against my actual AWS account via the Cost Explorer API,
and every day's real spend, service by service, flows through the same
detection and alerting logic described above. My current spend is low
day to day, so most runs correctly find nothing to flag. That is what
"working as designed" looks like day to day. The exceptional case (a
real spike like the one that inspired this project) is exactly what the
rolling mean/std-dev logic is built to catch.

## Testing without a live spike

Since my account isn't spiking on demand for a demo, `generate_synthetic_data.py`
produces a dataset shaped like the incident that started this project
(a flat baseline, then a sudden multi-times jump in a service like EC2),
so the detection logic can be verified end-to-end without waiting for
another real bill spike to happen:

```
python src/generate_synthetic_data.py --days 30
python src/detect.py --input data/synthetic_cost_data.json --skip-dynamodb
```

This is a development/testing tool only, clearly separate from the
production ingestion path. It's documented here rather than hidden, since
being upfront about testing constraints (and how they were worked around)
is more credible than pretending an account had real spend it didn't.

## Tests

```
pytest tests/ -v
```

44 tests covering ingestion, detection math (including edge cases like
zero-variance windows), DynamoDB writes (including partial-failure
handling), Slack alerting, the Lambda handler's orchestration logic, and
the SSM-backed webhook retrieval with in-memory caching.

## Security notes

- The Lambda's IAM role is scoped to exactly four actions: reading Cost
  Explorer, writing to this one DynamoDB table, reading this one SSM
  parameter, and writing its own CloudWatch Logs. No wildcard resource
  access beyond what Cost Explorer itself requires (it doesn't support
  resource-level restriction).
- The Slack webhook URL is stored as an SSM SecureString, decrypted only
  at runtime inside the Lambda, and cached in memory rather than
  refetched on every invocation.
- `terraform.tfvars` (containing the webhook URL) is gitignored and never
  committed.
