from unittest.mock import patch
import pytest
from backend.billing import process_reading, get_device_state, set_device_relay, is_device_online, MIN_REBILL_L
from backend.models import db, Bill, Reading, Device

def test_process_reading_below_limit(app):
    """Readings below monthly limit should keep relay ON with no bill."""
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


def test_process_reading_first_breach_creates_bill_and_turns_relay_off(app):
    """(a) First breach creates one bill and turns relay OFF."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

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
        bills = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills) == 1
        assert bills[0].amount == 1.50
        assert bills[0].status == "unpaid"


def test_post_payment_readings_below_rebill_threshold_create_no_bill_and_keep_relay_on(app):
    """
    (b) After a bill is marked paid, the next readings a few litres higher
    create NO new bill and the relay is NOT turned OFF.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # 1. First breach at 515.0 L (excess 15.0 L)
        process_reading("device1", litres=15.0, total_l=515.0, flow_lpm=2.4)
        first_bill = Bill.query.filter_by(device_id=device.id, status="unpaid").first()
        assert first_bill is not None
        assert first_bill.excess_l == 15.0
        assert get_device_state("device1")["relay"] == "OFF"

        # 2. Mark bill as paid and turn relay back ON (simulating payment completion)
        first_bill.status = "paid"
        first_bill.payment_id = "pay_test_123"
        db.session.commit()
        set_device_relay("device1", "ON")
        assert get_device_state("device1")["relay"] == "ON"

        # 3. Next readings a fraction of a litre higher (515.5 L -> only 0.5 L unbilled excess, < 1.0 L MIN_REBILL_L)
        res = process_reading("device1", litres=0.5, total_l=515.5, flow_lpm=2.4)

        # Assert no new bill and relay remains ON
        assert res["action"] == "RECORDED"
        assert res["bill"] is None
        assert res["relay"] == "ON"
        assert get_device_state("device1")["relay"] == "ON"

        # Still only the original paid bill in DB
        bills = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills) == 1
        assert bills[0].status == "paid"


def test_unbilled_excess_reaching_rebill_threshold_creates_bill_for_only_new_litres(app):
    """
    (c) Once unbilled excess reaches MIN_REBILL_L (1.0 L), exactly one new bill
    is created whose excess_l equals ONLY the new unbilled litres.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # 1. First breach at 520.0 L (excess 20.0 L)
        process_reading("device1", litres=20.0, total_l=520.0, flow_lpm=2.4)
        bill1 = Bill.query.filter_by(device_id=device.id, status="unpaid").first()
        assert bill1.excess_l == 20.0

        # 2. Pay first bill and restore flow
        bill1.status = "paid"
        db.session.commit()
        set_device_relay("device1", "ON")

        # 3. Usage reaches 521.0 L (unbilled excess = (521 - 500) - 20 = 1.0 L == MIN_REBILL_L)
        with patch("backend.billing.publish_relay") as mock_publish:
            res = process_reading("device1", litres=1.0, total_l=521.0, flow_lpm=2.4)

            # Assert new bill is for exactly 1.0 L (not 21.0 L!) and relay is cut OFF
            mock_publish.assert_called_once_with("device1", "OFF")
            assert res["action"] == "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
            assert res["relay"] == "OFF"
            assert res["bill"] is not None
            assert res["bill"]["excess_l"] == 1.0
            assert res["bill"]["amount"] == 0.10 # 1.0 L * 0.10 Rs/L
            assert res["bill"]["status"] == "unpaid"

            # DB check: 2 total bills (1 paid, 1 unpaid)
            all_bills = Bill.query.filter_by(device_id=device.id).order_by(Bill.id.asc()).all()
            assert len(all_bills) == 2
            assert all_bills[0].excess_l == 20.0
            assert all_bills[0].status == "paid"
            assert all_bills[1].excess_l == 1.0
            assert all_bills[1].status == "unpaid"


def test_bill_created_exactly_once_unpaid_never_duplicated(app):
    """(d) An unpaid bill is never duplicated while pending."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()

        # Reading 1: crosses limit
        process_reading("device1", litres=10.0, total_l=505.0, flow_lpm=2.4)
        bills_after_first = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills_after_first) == 1

        # Reading 2: more usage arrives while bill is still unpaid
        res2 = process_reading("device1", litres=5.0, total_l=510.0, flow_lpm=0.0)
        assert res2["action"] == "UNPAID_BILL_PENDING_RELAY_OFF"
        assert res2["relay"] == "OFF"

        # Still only 1 bill in database
        bills_after_second = Bill.query.filter_by(device_id=device.id).all()
        assert len(bills_after_second) == 1


def test_custom_rate_calculation(app):
    """Bill calculation handles custom rates accurately."""
    with app.app_context():
        # Create device with custom limit and rate
        custom_device = Device(name="device_custom", monthly_limit_l=100.0, rate_per_l=0.25)
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


def test_mqtt_ingestion_and_billing_integration(app):
    """
    Integration test:
    Insert device named 'device1', save a reading through the same persistence
    logic mqtt_client uses, call process_reading with total over limit, and verify
    that exactly one bill exists and publish_relay('device1', 'OFF') was called.
    """
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        assert device is not None
        assert device.name == "device1"

        # 1. Save reading into database matching mqtt_client._insert_reading_to_db
        reading = Reading(device_id=device.id, litres=20.0, total_l=520.0)
        db.session.add(reading)
        db.session.commit()

        # 2. Call process_reading crossing 500L monthly quota
        with patch("backend.billing.publish_relay") as mock_publish:
            res = process_reading("device1", litres=20.0, total_l=520.0, flow_lpm=2.4)

            # 3. Assertions
            mock_publish.assert_called_once_with("device1", "OFF")
            assert res["relay"] == "OFF"
            assert res["action"] == "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"

            bills = Bill.query.filter_by(device_id=device.id).all()
            assert len(bills) == 1
            assert bills[0].excess_l == 20.0
            assert bills[0].amount == 2.00
            assert bills[0].status == "unpaid"
