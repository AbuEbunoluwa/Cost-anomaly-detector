"""
test_handler.py — unit tests for src/handler.py

Run with:
    pytest tests/test_handler.py -v

Mocks every external call (Cost Explorer, DynamoDB, SSM, Slack) so this runs
with no AWS credentials, no network, and no real webhook.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import handler  # noqa: E402


REQUIRED_ENV = {
    "DYNAMODB_TABLE_NAME": "test-table",
}


@patch("handler.send_alerts")
@patch("handler.get_slack_webhook_url")
@patch("handler.write_to_dynamodb")
@patch("handler.detect_anomalies")
@patch("handler.parse_daily_costs")
@patch("handler.fetch_daily_cost_by_service")
def test_lambda_handler_happy_path(
    mock_fetch, mock_parse, mock_detect, mock_write, mock_get_webhook, mock_alert
):
    mock_fetch.return_value = {"ResultsByTime": []}
    mock_parse.return_value = [{"date": "2026-09-08", "service": "Amazon EC2", "cost": 12.0}]
    mock_detect.return_value = [
        {"date": "2026-09-08", "service": "Amazon EC2", "cost": 12.0, "is_anomaly": False}
    ]
    mock_write.return_value = 1
    mock_get_webhook.return_value = "https://hooks.slack.com/fake"
    mock_alert.return_value = True

    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        result = handler.lambda_handler({}, None)

    assert result["statusCode"] == 200
    assert result["records_evaluated"] == 1
    assert result["anomalies_found"] == 0
    assert result["records_written"] == 1
    assert result["alert_sent"] is True

    mock_write.assert_called_once_with(mock_detect.return_value, table_name="test-table")
    mock_alert.assert_called_once()


@patch("handler.send_alerts")
@patch("handler.get_slack_webhook_url")
@patch("handler.write_to_dynamodb")
@patch("handler.detect_anomalies")
@patch("handler.parse_daily_costs")
@patch("handler.fetch_daily_cost_by_service")
def test_lambda_handler_counts_anomalies_correctly(
    mock_fetch, mock_parse, mock_detect, mock_write, mock_get_webhook, mock_alert
):
    mock_fetch.return_value = {"ResultsByTime": []}
    mock_parse.return_value = []
    mock_detect.return_value = [
        {"date": "2026-09-08", "service": "Amazon EC2", "cost": 72.0, "is_anomaly": True},
        {"date": "2026-09-08", "service": "Amazon S3", "cost": 3.5, "is_anomaly": False},
    ]
    mock_write.return_value = 2
    mock_get_webhook.return_value = "https://hooks.slack.com/fake"
    mock_alert.return_value = True

    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        result = handler.lambda_handler({}, None)

    assert result["anomalies_found"] == 1
    # Only the anomalous record should be passed to send_alerts
    alerted_anomalies = mock_alert.call_args[0][0]
    assert len(alerted_anomalies) == 1
    assert alerted_anomalies[0]["service"] == "Amazon EC2"


@patch("handler.send_alerts")
@patch("handler.get_slack_webhook_url")
@patch("handler.write_to_dynamodb")
@patch("handler.detect_anomalies")
@patch("handler.parse_daily_costs")
@patch("handler.fetch_daily_cost_by_service")
def test_lambda_handler_uses_env_var_overrides(
    mock_fetch, mock_parse, mock_detect, mock_write, mock_get_webhook, mock_alert
):
    mock_fetch.return_value = {"ResultsByTime": []}
    mock_parse.return_value = []
    mock_detect.return_value = []
    mock_write.return_value = 0
    mock_get_webhook.return_value = "https://hooks.slack.com/fake"
    mock_alert.return_value = True

    custom_env = {**REQUIRED_ENV, "DETECTION_WINDOW": "7", "DETECTION_THRESHOLD": "1.5"}
    with patch.dict(os.environ, custom_env, clear=False):
        handler.lambda_handler({}, None)

    _, kwargs = mock_detect.call_args
    assert kwargs["window"] == 7
    assert kwargs["threshold"] == 1.5


@patch("handler.send_alerts")
@patch("handler.get_slack_webhook_url")
@patch("handler.write_to_dynamodb")
@patch("handler.detect_anomalies")
@patch("handler.parse_daily_costs")
@patch("handler.fetch_daily_cost_by_service")
def test_lambda_handler_returns_ok_even_when_alert_fails(
    mock_fetch, mock_parse, mock_detect, mock_write, mock_get_webhook, mock_alert
):
    mock_fetch.return_value = {"ResultsByTime": []}
    mock_parse.return_value = []
    mock_detect.return_value = []
    mock_write.return_value = 0
    mock_get_webhook.return_value = "https://hooks.slack.com/fake"
    mock_alert.return_value = False  # Slack send failed

    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        result = handler.lambda_handler({}, None)

    # A failed Slack notification shouldn't crash the whole Lambda invocation —
    # the run itself (ingest/detect/write) still succeeded.
    assert result["statusCode"] == 200
    assert result["alert_sent"] is False


# ---------------------------------------------------------------------------
# get_slack_webhook_url — SSM fetch + in-memory caching
# ---------------------------------------------------------------------------

def setup_function(function):
    # Reset the module-level cache before each test in this file so tests
    # don't leak state into one another.
    handler._cached_webhook_url = None


@patch("handler.boto3.client")
def test_get_slack_webhook_url_fetches_from_ssm(mock_client_factory):
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "https://hooks.slack.com/real"}}
    mock_client_factory.return_value = mock_ssm

    with patch.dict(os.environ, {"SLACK_WEBHOOK_SSM_PARAM": "/custom/param"}, clear=False):
        url = handler.get_slack_webhook_url()

    mock_ssm.get_parameter.assert_called_once_with(Name="/custom/param", WithDecryption=True)
    assert url == "https://hooks.slack.com/real"


@patch("handler.boto3.client")
def test_get_slack_webhook_url_uses_default_param_name(mock_client_factory):
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "https://hooks.slack.com/real"}}
    mock_client_factory.return_value = mock_ssm

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLACK_WEBHOOK_SSM_PARAM", None)
        handler.get_slack_webhook_url()

    mock_ssm.get_parameter.assert_called_once_with(
        Name="/cost-anomaly-detector/slack-webhook-url", WithDecryption=True
    )


@patch("handler.boto3.client")
def test_get_slack_webhook_url_caches_after_first_call(mock_client_factory):
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "https://hooks.slack.com/real"}}
    mock_client_factory.return_value = mock_ssm

    first = handler.get_slack_webhook_url()
    second = handler.get_slack_webhook_url()

    # boto3.client should only be constructed once — second call hits the cache.
    mock_client_factory.assert_called_once()
    assert first == second == "https://hooks.slack.com/real"
