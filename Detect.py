"""
detect.py — Cloud Cost Anomaly Detector, Week 2

Loads the daily cost records produced by ingest.py, computes a rolling
mean + standard deviation per service, and flags any day where cost
exceeds (mean + threshold * std_dev) over the trailing window. Results
are written to DynamoDB (one item per service/date) so history builds
up over time and later runs can query trends.

Usage:
    python detect.py --input data/cost_data.json --window 14 --threshold 2.0
"""

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

DEFAULT_WINDOW = 14
DEFAULT_THRESHOLD = 2.0
DEFAULT_TABLE_NAME = "cost-anomaly-detector"


def load_records(input_path: str) -> list[dict]:
    """Load the flat list of {date, service, cost} records from ingest.py's output."""
    with open(input_path) as f:
        return json.load(f)


def group_by_service(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by service, sorted chronologically within each group."""
    grouped = defaultdict(list)
    for record in records:
        grouped[record["service"]].append(record)

    for service_records in grouped.values():
        service_records.sort(key=lambda r: r["date"])

    return dict(grouped)


def detect_anomalies(
    records: list[dict], window: int = DEFAULT_WINDOW, threshold: float = DEFAULT_THRESHOLD
) -> list[dict]:
    """Flag anomalous days per service using a trailing rolling mean/std dev.

    For each day, looks at the `window` days immediately before it (not
    including the day itself) and flags it if its cost exceeds
    mean + threshold * std_dev of that trailing window. Days without a
    full window of history yet are skipped (not enough data to judge).

    Returns a list of result records, each tagged with is_anomaly plus
    the mean/std_dev that were computed, so every day (not just anomalies)
    can be written to DynamoDB for historical trend tracking.
    """
    grouped = group_by_service(records)
    results = []

    for service, service_records in grouped.items():
        costs = [r["cost"] for r in service_records]

        for i, record in enumerate(service_records):
            if i < window:
                # Not enough trailing history yet to judge this day fairly.
                continue

            trailing_costs = costs[i - window : i]
            mean = statistics.mean(trailing_costs)
            # A single-value stdev is undefined; guard against a degenerate window.
            std_dev = statistics.stdev(trailing_costs) if len(set(trailing_costs)) > 1 else 0.0

            current_cost = record["cost"]
            upper_bound = mean + (threshold * std_dev)
            is_anomaly = current_cost > upper_bound

            pct_increase = ((current_cost - mean) / mean * 100) if mean > 0 else 0.0

            results.append(
                {
                    "date": record["date"],
                    "service": service,
                    "cost": round(current_cost, 4),
                    "rolling_mean": round(mean, 4),
                    "rolling_std_dev": round(std_dev, 4),
                    "threshold": threshold,
                    "is_anomaly": is_anomaly,
                    "pct_increase_vs_mean": round(pct_increase, 2),
                }
            )

    return results


def write_to_dynamodb(results: list[dict], table_name: str = DEFAULT_TABLE_NAME) -> int:
    """Write each result record to DynamoDB. Partition key: service, sort key: date.

    Returns the number of items successfully written.
    """
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
    except NoCredentialsError:
        raise SystemExit(
            "No AWS credentials found. Configure them via `aws configure` "
            "or environment variables before running this script."
        )

    written = 0
    for result in results:
        try:
            # DynamoDB's Python SDK doesn't accept native floats — cast to
            # Decimal-safe strings for the numeric fields, or use Decimal directly.
            from decimal import Decimal

            item = {
                "service": result["service"],
                "date": result["date"],
                "cost": Decimal(str(result["cost"])),
                "rolling_mean": Decimal(str(result["rolling_mean"])),
                "rolling_std_dev": Decimal(str(result["rolling_std_dev"])),
                "threshold": Decimal(str(result["threshold"])),
                "is_anomaly": result["is_anomaly"],
                "pct_increase_vs_mean": Decimal(str(result["pct_increase_vs_mean"])),
                "written_at": datetime.now(timezone.utc).isoformat(),
            }
            table.put_item(Item=item)
            written += 1
        except ClientError as e:
            print(f"Failed to write {result['service']}/{result['date']}: {e}")

    return written


def main():
    parser = argparse.ArgumentParser(description="Detect cost anomalies from ingested data.")
    parser.add_argument("--input", type=str, default="data/cost_data.json", help="Path to ingest.py's JSON output")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Trailing window size in days")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Std dev multiplier for flagging")
    parser.add_argument("--table", type=str, default=DEFAULT_TABLE_NAME, help="DynamoDB table name")
    parser.add_argument("--skip-dynamodb", action="store_true", help="Only print results, don't write to DynamoDB")
    args = parser.parse_args()

    records = load_records(args.input)
    print(f"Loaded {len(records)} records from {args.input}")

    results = detect_anomalies(records, window=args.window, threshold=args.threshold)
    anomalies = [r for r in results if r["is_anomaly"]]

    print(f"Evaluated {len(results)} service-days; found {len(anomalies)} anomalies")
    for a in anomalies:
        print(
            f"  ANOMALY: {a['date']} {a['service']} — ${a['cost']} "
            f"(mean ${a['rolling_mean']}, +{a['pct_increase_vs_mean']}%)"
        )

    if not args.skip_dynamodb:
        written = write_to_dynamodb(results, table_name=args.table)
        print(f"Wrote {written}/{len(results)} records to DynamoDB table '{args.table}'")


if __name__ == "__main__":
    main()
