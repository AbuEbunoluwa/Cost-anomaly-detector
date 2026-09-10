"""
alert.py — Cloud Cost Anomaly Detector, Week 3

Formats detected anomalies into a Slack message and sends it via an
incoming webhook. Kept separate from detect.py so alerting logic can be
tested/swapped independently (e.g. adding email or SNS later without
touching detection code).
"""

import json
import urllib.request
import urllib.error


def format_slack_message(anomalies: list[dict]) -> dict:
    """Build a Slack Block Kit payload summarizing the anomalies found.

    Returns a dict ready to be JSON-encoded and POSTed to a Slack webhook.
    If `anomalies` is empty, returns a minimal payload (caller should
    generally skip sending in that case — see send_alerts below).
    """
    if not anomalies:
        return {"text": "Cloud Cost Anomaly Detector: no anomalies found today."}

    lines = [f"*Cloud Cost Anomaly Detector — {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} found*"]
    for a in anomalies:
        lines.append(
            f"• *{a['service']}* on {a['date']}: ${a['cost']:.2f} "
            f"(usual ~${a['rolling_mean']:.2f}, +{a['pct_increase_vs_mean']:.1f}%)"
        )

    return {
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ],
        # Plain-text fallback for notification previews / clients that don't render blocks
        "text": lines[0],
    }


def send_slack_alert(payload: dict, webhook_url: str) -> bool:
    """POST a pre-built payload to a Slack incoming webhook. Returns True on success."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=data, headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        print(f"Slack webhook returned an error: {e.code} {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"Failed to reach Slack webhook: {e.reason}")
        return False


def send_alerts(anomalies: list[dict], webhook_url: str, notify_on_clean_run: bool = False) -> bool:
    """Send a Slack alert for the given anomalies.

    By default, sends nothing if there are no anomalies (avoids daily noise).
    Set notify_on_clean_run=True to get a confirmation message even when
    nothing was found (useful early on, to confirm the pipeline is actually
    running on schedule).
    """
    if not anomalies and not notify_on_clean_run:
        return True  # nothing to send, not a failure

    payload = format_slack_message(anomalies)
    return send_slack_alert(payload, webhook_url)
