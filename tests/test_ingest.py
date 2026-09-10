"""
test_ingest.py — unit tests for src/ingest.py

Run with:
    pytest tests/test_ingest.py -v

These tests mock all AWS calls (boto3) so they run with no credentials
and no network access.
"""

import json
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Make src/ importable when running pytest from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ingest  # noqa: E402


# ---------------------------------------------------------------------------
# get_date_range
# ---------------------------------------------------------------------------

def test_get_date_range_returns_correct_window():
    start_str, end_str = ingest.get_date_range(30)

    expected_end = date.today()
    expected_start = expected_end - timedelta(days=30)

    assert start_str == expected_start.isoformat()
    assert end_str == expected_end.isoformat()


def test_get_date_range_zero_days():
    start_str, end_str = ingest.get_date_range(0)
    assert start_str == end_str


# ---------------------------------------------------------------------------
# parse_daily_costs
# ---------------------------------------------------------------------------

def test_parse_daily_costs_flattens_response_correctly():
    raw_response = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-08-20", "End": "2026-08-21"},
                "Groups": [
                    {
                        "Keys": ["Amazon EC2"],
                        "Metrics": {"UnblendedCost": {"Amount": "12.3456", "Unit": "USD"}},
                    },
                    {
                        "Keys": ["Amazon S3"],
                        "Metrics": {"UnblendedCost": {"Amount": "0.5", "Unit": "USD"}},
                    },
                ],
            }
        ]
    }

    records = ingest.parse_daily_costs(raw_response)

    assert len(records) == 2
    assert records[0] == {"date": "2026-08-20", "service": "Amazon EC2", "cost": 12.3456}
    assert records[1] == {"date": "2026-08-20", "service": "Amazon S3", "cost": 0.5}


def test_parse_daily_costs_skips_zero_cost_entries():
    raw_response = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-08-20", "End": "2026-08-21"},
                "Groups": [
                    {
                        "Keys": ["Amazon EC2"],
                        "Metrics": {"UnblendedCost": {"Amount": "0.0", "Unit": "USD"}},
                    },
                ],
            }
        ]
    }

    records = ingest.parse_daily_costs(raw_response)
    assert records == []


def test_parse_daily_costs_handles_empty_response():
    assert ingest.parse_daily_costs({"ResultsByTime": []}) == []
    assert ingest.parse_daily_costs({}) == []


def test_parse_daily_costs_rounds_cost_to_four_decimal_places():
    raw_response = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-08-20", "End": "2026-08-21"},
                "Groups": [
                    {
                        "Keys": ["AWS Lambda"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.123456789", "Unit": "USD"}},
                    },
                ],
            }
        ]
    }

    records = ingest.parse_daily_costs(raw_response)
    assert records[0]["cost"] == 1.1235


# ---------------------------------------------------------------------------
# fetch_daily_cost_by_service
# ---------------------------------------------------------------------------

@patch("ingest.boto3.client")
def test_fetch_daily_cost_by_service_calls_cost_explorer_correctly(mock_client_factory):
    mock_ce_client = MagicMock()
    mock_ce_client.get_cost_and_usage.return_value = {"ResultsByTime": []}
    mock_client_factory.return_value = mock_ce_client

    result = ingest.fetch_daily_cost_by_service("2026-08-01", "2026-08-30")

    mock_client_factory.assert_called_once_with("ce")
    mock_ce_client.get_cost_and_usage.assert_called_once_with(
        TimePeriod={"Start": "2026-08-01", "End": "2026-08-30"},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    assert result == {"ResultsByTime": []}


@patch("ingest.boto3.client")
def test_fetch_daily_cost_by_service_raises_systemexit_on_no_credentials(mock_client_factory):
    from botocore.exceptions import NoCredentialsError

    mock_ce_client = MagicMock()
    mock_ce_client.get_cost_and_usage.side_effect = NoCredentialsError()
    mock_client_factory.return_value = mock_ce_client

    with pytest.raises(SystemExit):
        ingest.fetch_daily_cost_by_service("2026-08-01", "2026-08-30")


@patch("ingest.boto3.client")
def test_fetch_daily_cost_by_service_raises_systemexit_on_client_error(mock_client_factory):
    from botocore.exceptions import ClientError

    mock_ce_client = MagicMock()
    mock_ce_client.get_cost_and_usage.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetCostAndUsage"
    )
    mock_client_factory.return_value = mock_ce_client

    with pytest.raises(SystemExit):
        ingest.fetch_daily_cost_by_service("2026-08-01", "2026-08-30")


# ---------------------------------------------------------------------------
# save_to_json
# ---------------------------------------------------------------------------

def test_save_to_json_writes_expected_content(tmp_path):
    records = [{"date": "2026-08-20", "service": "Amazon EC2", "cost": 12.34}]
    output_path = tmp_path / "nested" / "cost_data.json"

    ingest.save_to_json(records, str(output_path))

    assert output_path.exists()
    with open(output_path) as f:
        saved = json.load(f)
    assert saved == records


def test_save_to_json_creates_missing_directories(tmp_path):
    output_path = tmp_path / "a" / "b" / "c" / "cost_data.json"
    ingest.save_to_json([], str(output_path))
    assert output_path.exists()

