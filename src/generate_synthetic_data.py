"""
generate_synthetic_data.py — Cloud Cost Anomaly Detector, testing helper

Generates a realistic cost_data.json for services + a date range, with a
flat/mildly-noisy baseline and one or two deliberate spikes injected, so
detect.py has something real to actually flag. Useful when your real AWS
account (e.g. free tier) doesn't generate enough spend to test against.

This is NOT part of the production pipeline — it's a dev/testing tool.
Document its use in your README/demo notes rather than passing its output
off as real billing data.

Usage:
    python generate_synthetic_data.py --days 30 --output data/synthetic_cost_data.json
"""

import argparse
import json
import random
from datetime import date, timedelta

# Baseline daily cost per service, with the spike day/multiplier for each.
# Edit this to shape whatever demo scenario you want.
SERVICES = {
    "Amazon EC2": {"baseline": 12.00, "noise_pct": 0.10, "spike_day": 24, "spike_multiplier": 6.0},
    "Amazon S3": {"baseline": 3.50, "noise_pct": 0.08, "spike_day": None, "spike_multiplier": 1.0},
    "AWS Lambda": {"baseline": 1.20, "noise_pct": 0.15, "spike_day": 27, "spike_multiplier": 9.0},
    "Amazon RDS": {"baseline": 8.75, "noise_pct": 0.06, "spike_day": None, "spike_multiplier": 1.0},
}


def generate_records(days: int, seed: int = 42) -> list[dict]:
    """Generate `days` of synthetic records for each service in SERVICES.

    Each day's cost is baseline +/- random noise (noise_pct of baseline).
    On a service's spike_day (if set), cost is multiplied by spike_multiplier
    instead, simulating a real cost anomaly.
    """
    random.seed(seed)  # reproducible output across runs
    start = date.today() - timedelta(days=days)
    records = []

    for service, config in SERVICES.items():
        for day_index in range(days):
            current_date = start + timedelta(days=day_index)
            baseline = config["baseline"]
            noise = baseline * config["noise_pct"]
            cost = baseline + random.uniform(-noise, noise)

            if config["spike_day"] is not None and day_index == config["spike_day"]:
                cost = baseline * config["spike_multiplier"]

            records.append(
                {"date": current_date.isoformat(), "service": service, "cost": round(cost, 4)}
            )

    return records


def save_to_json(records: list[dict], output_path: str) -> None:
    import os

    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Saved {len(records)} synthetic records to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic cost data for testing.")
    parser.add_argument("--days", type=int, default=30, help="Number of days to generate (default: 30)")
    parser.add_argument(
        "--output", type=str, default="data/synthetic_cost_data.json", help="Path to write JSON output"
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    records = generate_records(args.days, seed=args.seed)
    save_to_json(records, args.output)

    spikes = [(svc, cfg["spike_day"]) for svc, cfg in SERVICES.items() if cfg["spike_day"] is not None]
    print("Injected spikes at day index (0-based, from start of range):")
    for svc, day_idx in spikes:
        print(f"  {svc}: day index {day_idx}")


if __name__ == "__main__":
    main()
