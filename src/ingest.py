"""
ingest.py — Cloud Cost Anomaly Detector, Week 1

Pulls daily AWS cost data (grouped by service) from the Cost Explorer API
for a given lookback window, and writes it to a local JSON file so we can
inspect the shape of the data before wiring up detection logic.

Required IAM permission: ce:GetCostAndUsage
(Cost Explorer must also be enabled once in the AWS Billing console —
it can take up to 24 hours to activate on a brand-new account.)

Usage:
    python ingest.py --days 30 --output data/cost_data.json
"""

import argparse
import json
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


def get_date_range(days: int) -> tuple[str, str]:
    """Return (start_date, end_date) as YYYY-MM-DD strings.

    Cost Explorer's 'End' date is exclusive, so end_date is today.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    return start_date.isoformat(), end_date.isoformat()


def fetch_daily_cost_by_service(start_date: str, end_date: str) -> dict:
    """Call Cost Explorer and return raw daily cost data grouped by service."""
    client = boto3.client("ce")  # Cost Explorer is a global/us-east-1 service

    try:
        response = client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
    except NoCredentialsError:
        raise SystemExit(
            "No AWS credentials found. Configure them via `aws configure` "
            "or environment variables before running this script."
        )
    except ClientError as e:
        raise SystemExit(f"AWS API error calling Cost Explorer: {e}")

    return response


def parse_daily_costs(raw_response: dict) -> list[dict]:
    """Flatten the Cost Explorer response into a simple list of records:
    [{"date": "2026-08-20", "service": "Amazon EC2", "cost": 12.34}, ...]
    """
    records = []
    for day in raw_response.get("ResultsByTime", []):
        day_date = day["TimePeriod"]["Start"]
        for group in day.get("Groups", []):
            service_name = group["Keys"][0]
            cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
            # Skip zero-cost noise to keep the dataset readable
            if cost > 0:
                records.append(
                    {"date": day_date, "service": service_name, "cost": round(cost, 4)}
                )
    return records


def save_to_json(records: list[dict], output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if os.path.dirname(output_path) else None
    with open(output_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Saved {len(records)} records to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Pull AWS daily cost data by service.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument(
        "--output", type=str, default="data/cost_data.json", help="Path to write JSON output"
    )
    args = parser.parse_args()

    start_date, end_date = get_date_range(args.days)
    print(f"Fetching cost data from {start_date} to {end_date}...")

    raw_response = fetch_daily_cost_by_service(start_date, end_date)
    records = parse_daily_costs(raw_response)

    if not records:
        print(
            "No cost records returned. This can happen on a brand-new account, "
            "or if Cost Explorer was only just enabled (it can take up to 24h)."
        )

    save_to_json(records, args.output)


if __name__ == "__main__":
    main()
