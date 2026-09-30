import pytest
from backend.billing import process_reading, get_device_state, set_device_relay
from backend.models import Bill, Reading, Device

def test_process_reading_below_limit(app):
    """Readings below monthly limit should record reading and keep relay ON with no bill."""
    with app.app_context():
        res = process_reading("device1", litres=5.0, total_l=450.0, flow_lpm=2.4)

        assert res["action"] == "RECORDED"
        assert res["relay"] == "ON"
        assert res["flow_lpm"] == 2.4
        assert res["bill"] is None

        # Check DB
        bills = Bill.query.filter_by(device_id="device1").all()
        assert len(bills) == 0

        readings = Reading.query.filter_by(device_id="device1").all()
        assert len(readings) == 1
        assert readings[0].total_l == 450.0


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


def test_first_excess_bill(app):
    """500 L limit -> 510 L usage creates a 10 L excess bill."""
    with app.app_context():
        res = process_reading("device1", litres=10.0, total_l=510.0, flow_lpm=2.4)
        assert res["action"] == "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
        assert res["bill"] is not None
        assert res["bill"]["excess_l"] == 10.0
        assert res["bill"]["amount"] == 1.00
        assert res["relay"] == "OFF"


def test_paid_bill_followed_by_reading_and_threshold(app):
    """510 L -> 10 L bill (paid). 519 L -> no new bill (< 10 L threshold). 520 L -> new 10 L bill."""
    with app.app_context():
        # 1. First bill at 510 L (10 L excess)
        res1 = process_reading("device1", litres=10.0, total_l=510.0, flow_lpm=2.4)
        bill1 = Bill.query.filter_by(device_id="device1", status="unpaid").first()
        assert bill1 is not None
        assert bill1.excess_l == 10.0

        # Mark bill paid & turn relay back ON
        bill1.status = "paid"
        set_device_relay("device1", "ON")
        from backend.models import db
        db.session.commit()

        # 2. Next reading at 519 L (9 L new excess, below 10 L threshold)
        res2 = process_reading("device1", litres=9.0, total_l=519.0, flow_lpm=2.4)
        assert res2["action"] == "RECORDED"
        assert res2["bill"] is None
        bills = Bill.query.filter_by(device_id="device1").all()
        assert len(bills) == 1

        # 3. Next reading at 520 L (10 L new unbilled excess, reaches threshold)
        res3 = process_reading("device1", litres=1.0, total_l=520.0, flow_lpm=2.4)
        assert res3["action"] == "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
        assert res3["bill"] is not None
        assert res3["bill"]["excess_l"] == 10.0
        assert res3["bill"]["amount"] == 1.00

        all_bills = Bill.query.filter_by(device_id="device1").order_by(Bill.id.asc()).all()
        assert len(all_bills) == 2
        assert all_bills[0].excess_l == 10.0
        assert all_bills[0].status == "paid"
        assert all_bills[1].excess_l == 10.0
        assert all_bills[1].status == "unpaid"


def test_no_double_charging_under_threshold(app):
    """510 L -> 10 L bill (paid). 515 L -> 5 L unbilled excess -> no new bill, no double charge."""
    with app.app_context():
        process_reading("device1", litres=10.0, total_l=510.0, flow_lpm=2.4)
        bill = Bill.query.filter_by(device_id="device1").first()
        bill.status = "paid"
        set_device_relay("device1", "ON")
        from backend.models import db
        db.session.commit()

        res = process_reading("device1", litres=5.0, total_l=515.0, flow_lpm=2.4)
        assert res["action"] == "RECORDED"
        assert res["bill"] is None
        assert res["relay"] == "ON"
        bills = Bill.query.filter_by(device_id="device1").all()
        assert len(bills) == 1
