from unittest.mock import patch
import pytest
from backend.billing import process_reading, get_device_state, set_device_relay, is_device_online
from backend.models import Bill, Reading, Device

def test_process_reading_below_limit(app):
    """Readings below monthly limit should keep relay ON with no bill."""
    with app.app_context():
        res = process_reading("device1", litres=5.0, total_l=450.0, flow_lpm=2.4)

        assert res["action"] == "RECORDED"
        assert res["relay"] == "ON"
        assert res["flow_lpm"] == 2.4
        assert res["bill"] is None

        # Check DB
        bills = Bill.query.filter_by(device_id="device1").all()
        assert len(bills) == 0


def test_process_reading_crosses_limit_creates_bill_and_turns_relay_off(app):
    """Crossing 500L threshold should create bill for excess amount and turn relay OFF."""
    with app.app_context():
        # First reading below limit
        process_reading("device1", litres=10.0, total_l=490.0, flow_lpm=2.4)
        assert get_device_state("device1")["relay"] == "ON"

        # Second reading crosses limit (515.0 L -> 15.0 L excess)
        res = process_reading("device1", litres=25.0, total_l=515.0, flow_lpm=2.4)

        assert res["action"] == "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
        assert res["relay"] == "OFF"
        assert res["flow_lpm"] == 0.0
        assert res["bill"] is not None

        # 15.0 L excess * Rs 0.10/L = Rs 1.50
        assert res["bill"]["excess_l"] == 15.0
        assert res["bill"]["amount"] == 1.50
        assert res["bill"]["status"] == "unpaid"

        # Check in-memory runtime state
        assert get_device_state("device1")["relay"] == "OFF"

        # Check DB
        bills = Bill.query.filter_by(device_id="device1").all()
        assert len(bills) == 1
        assert bills[0].amount == 1.50
        assert bills[0].status == "unpaid"


def test_bill_created_exactly_once(app):
    """Subsequent readings above limit should not duplicate unpaid bills."""
    with app.app_context():
        # Reading 1: crosses limit
        process_reading("device1", litres=10.0, total_l=505.0, flow_lpm=2.4)
        bills_after_first = Bill.query.filter_by(device_id="device1").all()
        assert len(bills_after_first) == 1

        # Reading 2: more usage arrives while bill is still unpaid
        res2 = process_reading("device1", litres=5.0, total_l=510.0, flow_lpm=0.0)
        assert res2["action"] == "UNPAID_BILL_PENDING_RELAY_OFF"
        assert res2["relay"] == "OFF"

        # Still only 1 bill in database
        bills_after_second = Bill.query.filter_by(device_id="device1").all()
        assert len(bills_after_second) == 1


def test_custom_rate_calculation(app):
    """Bill calculation handles custom rates accurately."""
    with app.app_context():
        # Create device with custom limit and rate
        custom_device = Device(id="device_custom", name="Custom Farm Meter", monthly_limit_l=100.0, rate_per_l=0.25)
        from backend.models import db
        db.session.add(custom_device)
        db.session.commit()

        # Cross limit: 120.5 L -> 20.5 L excess * 0.25 = 5.125 -> rounded to 5.12
        res = process_reading("device_custom", litres=20.5, total_l=120.5, flow_lpm=3.0)
        assert res["bill"] is not None
        assert res["bill"]["excess_l"] == 20.5
        assert res["bill"]["amount"] == 5.12


def test_limit_crossing_calls_publish_relay_off(app):
    """Verify that crossing limit invokes publish_relay('device1', 'OFF')."""
    with app.app_context():
        with patch("backend.billing.publish_relay") as mock_publish:
            process_reading("device1", litres=15.0, total_l=510.0, flow_lpm=2.4)
            mock_publish.assert_called_once_with("device1", "OFF")


def test_online_status_from_mqtt(app):
    """Verify that is_device_online reflects MQTT status."""
    with app.app_context():
        with patch("backend.mqtt_client.get_device_status", return_value="online"):
            assert is_device_online("device1") is True

        with patch("backend.mqtt_client.get_device_status", return_value="offline"):
            assert is_device_online("device1") is False
