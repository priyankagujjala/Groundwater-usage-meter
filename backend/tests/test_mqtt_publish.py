import logging
from unittest.mock import MagicMock, patch
import pytest
import paho.mqtt.client as mqtt

from backend import mqtt_client
from backend.billing import set_device_relay, process_reading, get_device_state


def test_publish_relay_client_none():
    """When MQTT client is not running (_mqtt_client is None), publish_relay returns False without raising."""
    with patch.object(mqtt_client, "_mqtt_client", None):
        result = mqtt_client.publish_relay("device1", "ON")
        assert result is False
        assert mqtt_client.get_relay_state("device1") == "ON"


def test_publish_relay_not_connected_and_publish_fails():
    """When client is not connected and publish returns error code, returns False."""
    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    mock_msg_info = MagicMock()
    mock_msg_info.rc = mqtt.MQTT_ERR_NO_CONN
    mock_msg_info.mid = 10
    mock_client.publish.return_value = mock_msg_info

    with patch.object(mqtt_client, "_mqtt_client", mock_client):
        result = mqtt_client.publish_relay("device1", "OFF", wait_for_ack=False)
        assert result is False
        mock_client.publish.assert_called_once_with("gw/device1/cmd", payload='{"relay": "OFF"}', qos=1, retain=True)
        # Should not wait when wait_for_ack=False
        mock_msg_info.wait_for_publish.assert_not_called()


def test_publish_relay_wait_for_ack_acknowledged():
    """When wait_for_ack=True and broker acknowledges publish, returns True."""
    mock_client = MagicMock()
    mock_client.is_connected.return_value = True
    mock_msg_info = MagicMock()
    mock_msg_info.rc = mqtt.MQTT_ERR_SUCCESS
    mock_msg_info.mid = 42
    mock_msg_info.is_published.return_value = True
    mock_client.publish.return_value = mock_msg_info

    with patch.object(mqtt_client, "_mqtt_client", mock_client):
        result = mqtt_client.publish_relay("device1", "ON", wait_for_ack=True)
        assert result is True
        mock_msg_info.wait_for_publish.assert_called_once_with(timeout=3.0)


def test_publish_relay_wait_for_ack_not_acknowledged():
    """When wait_for_ack=True and broker does not acknowledge / wait times out, returns False."""
    mock_client = MagicMock()
    mock_client.is_connected.return_value = True
    mock_msg_info = MagicMock()
    mock_msg_info.rc = mqtt.MQTT_ERR_SUCCESS
    mock_msg_info.mid = 43
    mock_msg_info.wait_for_publish.side_effect = RuntimeError("Timeout waiting for PUBACK")
    mock_msg_info.is_published.return_value = False
    mock_client.publish.return_value = mock_msg_info

    with patch.object(mqtt_client, "_mqtt_client", mock_client):
        result = mqtt_client.publish_relay("device1", "OFF", wait_for_ack=True)
        assert result is False
        mock_msg_info.wait_for_publish.assert_called_once_with(timeout=3.0)


def test_billing_process_reading_off_never_waits(app):
    """
    Billing limit enforcement runs in paho's on_message network thread.
    Verify that publish_relay is called with wait_for_ack=False (never blocking for PUBACK).
    """
    with app.app_context():
        with patch("backend.billing.publish_relay") as mock_publish:
            res = process_reading("device1", litres=20.0, total_l=1020.0, flow_lpm=2.4)
            assert res["action"] == "MONTHLY_LIMIT_BREACHED_RELAY_OFF"
            assert res["relay"] == "OFF"

            # Check that publish_relay was called once for OFF
            assert mock_publish.call_count == 1
            args, kwargs = mock_publish.call_args
            assert args[0] == "device1"
            assert args[1] == "OFF"
            # wait_for_ack must be False or omitted (defaulting to False)
            assert kwargs.get("wait_for_ack", False) is False


def test_set_device_relay_propagates_wait_for_ack_and_return_value():
    """set_device_relay forwards wait_for_ack to publish_relay and returns its real result."""
    with patch("backend.billing.publish_relay", return_value=True) as mock_pub:
        res = set_device_relay("device1", "ON", wait_for_ack=True)
        assert res is True
        mock_pub.assert_called_once_with("device1", "ON", wait_for_ack=True)

    with patch("backend.billing.publish_relay", return_value=False) as mock_pub:
        res = set_device_relay("device1", "OFF", wait_for_ack=False)
        assert res is False
        mock_pub.assert_called_once_with("device1", "OFF", wait_for_ack=False)


def test_on_publish_callback_logs_puback(caplog):
    """Verify that _on_publish callback logs [PUBACK] at INFO."""
    with caplog.at_level(logging.INFO):
        mqtt_client._on_publish(MagicMock(), None, 99, 0)
    assert "[PUBACK] mid=99 acknowledged by broker" in caplog.text


def test_on_disconnect_callback_logs_warning(caplog):
    """Verify that _on_disconnect logs connected state and reason_code at WARNING."""
    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    with caplog.at_level(logging.WARNING):
        mqtt_client._on_disconnect(mock_client, None, 0, 7)
    assert "connected=False" in caplog.text
    assert "7" in caplog.text
