"""
test_generate_synthetic_data.py — unit tests for src/generate_synthetic_data.py

Run with:
    pytest tests/test_generate_synthetic_data.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import generate_synthetic_data as gen  # noqa: E402


def test_generate_records_produces_expected_count():
    records = gen.generate_records(days=10, seed=1)
    # One record per service per day
    assert len(records) == 10 * len(gen.SERVICES)


def test_generate_records_is_reproducible_with_same_seed():
    records_a = gen.generate_records(days=15, seed=7)
    records_b = gen.generate_records(days=15, seed=7)
    assert records_a == records_b


def test_generate_records_differs_with_different_seed():
    records_a = gen.generate_records(days=15, seed=1)
    records_b = gen.generate_records(days=15, seed=2)
    assert records_a != records_b


def test_generate_records_injects_spike_at_correct_day():
    days = 30
    records = gen.generate_records(days=days, seed=1)

    ec2_config = gen.SERVICES["Amazon EC2"]
    spike_day_index = ec2_config["spike_day"]
    expected_spike_date = None

    from datetime import date, timedelta
    start = date.today() - timedelta(days=days)
    expected_spike_date = (start + timedelta(days=spike_day_index)).isoformat()

    spike_record = next(
        r for r in records if r["service"] == "Amazon EC2" and r["date"] == expected_spike_date
    )
    expected_min = ec2_config["baseline"] * ec2_config["spike_multiplier"] * 0.99
    assert spike_record["cost"] >= expected_min


def test_generate_records_non_spike_services_stay_near_baseline():
    records = gen.generate_records(days=30, seed=1)
    s3_costs = [r["cost"] for r in records if r["service"] == "Amazon S3"]

    baseline = gen.SERVICES["Amazon S3"]["baseline"]
    noise_pct = gen.SERVICES["Amazon S3"]["noise_pct"]

    for cost in s3_costs:
        assert baseline * (1 - noise_pct) * 0.99 <= cost <= baseline * (1 + noise_pct) * 1.01


def test_save_to_json_writes_valid_json(tmp_path):
    records = gen.generate_records(days=5, seed=1)
    output_path = tmp_path / "nested" / "synthetic.json"

    gen.save_to_json(records, str(output_path))

    assert output_path.exists()
    with open(output_path) as f:
        loaded = json.load(f)
    assert loaded == records
