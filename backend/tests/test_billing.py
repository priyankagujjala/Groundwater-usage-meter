from unittest.mock import patch
import pytest
from backend.billing import process_reading, get_device_state, set_device_relay, is_device_online, MIN_REBILL_L
from backend.models import db, Bill, Reading, Device

def test_process_reading_below_free_limit(app):
    """Readings below free limit should keep relay ON with no bill (green zone)."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        res = process_reading("device1", litres=5.0, total_l=450.0, flow_lpm=2.4)

        assert res["action"] == "RECORDED"
        assert res["relay"] == "ON"
        assert res["flow_lpm"] == 2.4
        assert res["bill"] is None

        # Check DB
        bills = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills) == 0


def test_process_reading_crossing_free_limit_generates_bill_and_keeps_relay_on(app):
    """
    Crossing the free limit (500L) creates a bill for excess litres,
    but keeps the relay ON so water usage continues up to monthly limit.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # First reading below free limit
        process_reading("device1", litres=10.0, total_l=490.0, flow_lpm=2.4)
        assert get_device_state("device1")["relay"] == "ON"

        # Second reading crosses free limit (515.0 L -> 15.0 L excess over 500L free tier)
        res = process_reading("device1", litres=25.0, total_l=515.0, flow_lpm=2.4)

        assert res["action"] == "FREE_LIMIT_EXCEEDED_BILL_CREATED"
        # Relay must stay ON (monthly limit is 1000L)
        assert res["relay"] == "ON"
        assert res["flow_lpm"] == 2.4
        assert res["bill"] is not None

        # 15.0 L excess * Rs 0.10/L = Rs 1.50
        assert res["bill"]["excess_l"] == 15.0
        assert res["bill"]["amount"] == 1.50
        assert res["bill"]["status"] == "unpaid"

        # Check in-memory runtime state
        assert get_device_state("device1")["relay"] == "ON"

        # Check DB
        bills = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills) == 1
        assert bills[0].amount == 1.50
        assert bills[0].status == "unpaid"


def test_process_reading_hitting_monthly_limit_shuts_off_relay(app):
    """
    When usage reaches monthly limit (1000L), relay is automatically cut OFF.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # Crossing monthly cutoff at 1005.0 L
        with patch("backend.billing.publish_relay") as mock_publish:
            res = process_reading("device1", litres=15.0, total_l=1005.0, flow_lpm=2.4)

            # Assert relay turned OFF and command published
            mock_publish.assert_called_once_with("device1", "OFF")
            assert res["action"] == "MONTHLY_LIMIT_BREACHED_RELAY_OFF"
            assert res["relay"] == "OFF"
            assert res["flow_lpm"] == 0.0
            assert get_device_state("device1")["relay"] == "OFF"


def test_per_litre_rebilling_between_free_and_monthly_limit(app):
    """
    Subsequent readings generate bills for each 1.0L excess above free tier.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # 1. First breach at 520.0 L (excess 20.0 L)
        process_reading("device1", litres=20.0, total_l=520.0, flow_lpm=2.4)
        bill1 = Bill.query.filter_by(device_id=device.id, status="unpaid").first()
        assert bill1.excess_l == 20.0

        # 2. Mark first bill as paid
        bill1.status = "paid"
        db.session.commit()

        # 3. Next reading with 0.5L (< 1.0L MIN_REBILL_L) -> no new bill
        res_sub = process_reading("device1", litres=0.5, total_l=520.5, flow_lpm=2.4)
        assert res_sub["action"] == "PAID_TIER_USAGE"
        assert res_sub["bill"] is None
        assert res_sub["relay"] == "ON"

        # 4. Usage reaches 521.0 L (unbilled excess = (521 - 500) - 20 = 1.0 L == MIN_REBILL_L)
        res_new_bill = process_reading("device1", litres=0.5, total_l=521.0, flow_lpm=2.4)
        assert res_new_bill["action"] == "FREE_LIMIT_EXCEEDED_BILL_CREATED"
        assert res_new_bill["bill"] is not None
        assert res_new_bill["bill"]["excess_l"] == 1.0
        assert res_new_bill["bill"]["amount"] == 0.10
        assert res_new_bill["relay"] == "ON"


def test_custom_rate_and_dual_limits_calculation(app):
    """Bill calculation handles custom free limit and rates accurately."""
    with app.app_context():
        # Create device with custom limits: free 100L, monthly 200L, rate 0.25
        custom_device = Device(name="device_custom", free_limit_l=100.0, monthly_limit_l=200.0, rate_per_l=0.25)
        db.session.add(custom_device)
        db.session.commit()

        # Cross free limit: 120.5 L -> 20.5 L excess * 0.25 = 5.125 -> rounded to 5.12
        res = process_reading("device_custom", litres=20.5, total_l=120.5, flow_lpm=3.0)
        assert res["bill"] is not None
        assert res["bill"]["excess_l"] == 20.5
        assert res["bill"]["amount"] == 5.12
        assert res["relay"] == "ON"


def test_monthly_limit_crossing_calls_publish_relay_off(app):
    """Verify that crossing monthly limit invokes publish_relay('device1', 'OFF')."""
    with app.app_context():
        with patch("backend.billing.publish_relay") as mock_publish:
            process_reading("device1", litres=15.0, total_l=1010.0, flow_lpm=2.4)
            mock_publish.assert_called_once_with("device1", "OFF")


def test_online_status_from_mqtt(app):
    """Verify that is_device_online reflects MQTT status."""
    with app.app_context():
        with patch("backend.mqtt_client.get_device_status", return_value="online"):
            assert is_device_online("device1") is True

        with patch("backend.mqtt_client.get_device_status", return_value="offline"):
            assert is_device_online("device1") is False


def test_mqtt_ingestion_and_billing_integration(app):
    """
    Integration test:
    Save reading, call process_reading crossing 1000L monthly cutoff,
    verify bill created for excess over free limit (500L) and publish_relay('device1', 'OFF') called.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        assert device is not None
        assert device.name == "device1"

        # 1. Save reading into database matching mqtt_client._insert_reading_to_db
        reading = Reading(device_id=device.id, litres=20.0, total_l=1020.0)
        db.session.add(reading)
        db.session.commit()

        # 2. Call process_reading crossing 1000L monthly cutoff (520L excess over 500L free tier)
        with patch("backend.billing.publish_relay") as mock_publish:
            res = process_reading("device1", litres=20.0, total_l=1020.0, flow_lpm=2.4)

            # 3. Assertions
            mock_publish.assert_called_once_with("device1", "OFF")
            assert res["relay"] == "OFF"
            assert res["action"] == "MONTHLY_LIMIT_BREACHED_RELAY_OFF"

            bills = Bill.query.filter_by(device_id=device.id).all()
            assert len(bills) == 1
            assert bills[0].excess_l == 520.0
            assert bills[0].amount == 52.00
            assert bills[0].status == "unpaid"

