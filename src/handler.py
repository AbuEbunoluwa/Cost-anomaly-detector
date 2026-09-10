"""
handler.py — Cloud Cost Anomaly Detector, Week 3

Lambda entrypoint. On each scheduled invocation:
  1. Pulls the last LOOKBACK_DAYS of cost data from Cost Explorer
  2. Runs anomaly detection over it
  3. Writes every evaluated service-day to DynamoDB
  4. Sends a Slack alert if any anomalies were found

Configuration is via environment variables (set in Terraform):
  DYNAMODB_TABLE_NAME     — DynamoDB table to write results to
  SLACK_WEBHOOK_SSM_PARAM — SSM parameter name holding the Slack webhook URL
                            (SecureString, fetched + decrypted at runtime)
  LOOKBACK_DAYS           — how many days of cost data to pull (default: 30)
  DETECTION_WINDOW        — trailing window size in days (default: 14)
  DETECTION_THRESHOLD     — std dev multiplier for flagging (default: 2.0)
  NOTIFY_ON_CLEAN_RUN     — "true" to get a Slack message even with 0 anomalies
"""

import os

import boto3

from alert import send_alerts
from detect import detect_anomalies, write_to_dynamodb
from ingest import fetch_daily_cost_by_service, get_date_range, parse_daily_costs

# Cached across warm Lambda invocations so we don't call SSM on every run.
_cached_webhook_url = None


def get_slack_webhook_url() -> str:
    """Fetch the Slack webhook URL from SSM Parameter Store (SecureString),
    decrypting via KMS. Cached in memory for the lifetime of the Lambda
    execution environment (warm starts reuse it; cold starts fetch fresh).
    """
    global _cached_webhook_url
    if _cached_webhook_url is not None:
        return _cached_webhook_url

    parameter_name = os.environ.get(
        "SLACK_WEBHOOK_SSM_PARAM", "/cost-anomaly-detector/slack-webhook-url"
    )
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    _cached_webhook_url = response["Parameter"]["Value"]
    return _cached_webhook_url


def lambda_handler(event, context):
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "30"))
    window = int(os.environ.get("DETECTION_WINDOW", "14"))
    threshold = float(os.environ.get("DETECTION_THRESHOLD", "2.0"))
    table_name = os.environ["DYNAMODB_TABLE_NAME"]
    webhook_url = get_slack_webhook_url()
    notify_on_clean_run = os.environ.get("NOTIFY_ON_CLEAN_RUN", "false").lower() == "true"

    start_date, end_date = get_date_range(lookback_days)
    print(f"Fetching cost data from {start_date} to {end_date}...")

    raw_response = fetch_daily_cost_by_service(start_date, end_date)
    records = parse_daily_costs(raw_response)
    print(f"Parsed {len(records)} cost records")

    results = detect_anomalies(records, window=window, threshold=threshold)
    anomalies = [r for r in results if r["is_anomaly"]]
    print(f"Evaluated {len(results)} service-days; found {len(anomalies)} anomalies")

    written = write_to_dynamodb(results, table_name=table_name)
    print(f"Wrote {written}/{len(results)} records to DynamoDB")

    alert_sent = send_alerts(anomalies, webhook_url, notify_on_clean_run=notify_on_clean_run)
    if not alert_sent:
        print("Warning: Slack alert failed to send")

    return {
        "statusCode": 200,
        "records_evaluated": len(results),
        "anomalies_found": len(anomalies),
        "records_written": written,
        "alert_sent": alert_sent,
    }
