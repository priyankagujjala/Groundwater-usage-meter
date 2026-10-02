import json
from unittest.mock import MagicMock, patch
from backend.models import db, Bill, Reading, Device
from backend.billing import process_reading, get_device_state

def test_health_check(client):
    """GET /health returns 200 with {'ok': True}."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True}


def test_get_usage_structure_and_values(client, app):
    """GET /api/usage/<device> returns strict contract structure."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        db.session.add(Reading(device_id=device.id, litres=2.5, total_l=132.5))
        db.session.commit()
        process_reading("device1", litres=2.5, total_l=132.5, flow_lpm=2.4)

    res = client.get("/api/usage/device1")
    assert res.status_code == 200
    data = res.get_json()

    assert data["device"] == "device1"
    assert data["total"] == 132.5
    assert data["limit"] == 500.0
    assert data["flow_lpm"] == 2.4
    assert data["relay"] == "ON"
    assert data["online"] is True


def test_get_readings_chronological_order(client, app):
    """GET /api/readings/<device> returns list ordered oldest first."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        db.session.add(Reading(device_id=device.id, litres=1.0, total_l=10.0))
        db.session.add(Reading(device_id=device.id, litres=2.0, total_l=12.0))
        db.session.add(Reading(device_id=device.id, litres=3.0, total_l=15.0))
        db.session.commit()

    res = client.get("/api/readings/device1")
    assert res.status_code == 200
    readings = res.get_json()

    assert isinstance(readings, list)
    assert len(readings) == 3
    assert readings[0]["total"] == 10.0
    assert readings[1]["total"] == 12.0
    assert readings[2]["total"] == 15.0
    assert "ts" in readings[0]
    assert "litres" in readings[0]


def test_post_relay_toggle(client):
    """POST /api/relay/<device> toggles relay and validates payload."""
    # Valid OFF
    res_off = client.post("/api/relay/device1", json={"relay": "OFF"})
    assert res_off.status_code == 200
    assert res_off.get_json()["relay"] == "OFF"

    # Verify usage reflects OFF state
    res_usage = client.get("/api/usage/device1")
    assert res_usage.get_json()["relay"] == "OFF"

    # Valid ON
    res_on = client.post("/api/relay/device1", json={"relay": "ON"})
    assert res_on.status_code == 200
    assert res_on.get_json()["relay"] == "ON"

    # Invalid payload - bad value
    res_bad_val = client.post("/api/relay/device1", json={"relay": "MAYBE"})
    assert res_bad_val.status_code == 400

    # Invalid payload - missing key
    res_bad_key = client.post("/api/relay/device1", json={})
    assert res_bad_key.status_code == 400


def test_get_bills_newest_first(client, app):
    """GET /api/bills/<device> returns list of bills, newest first."""
    with app.app_context():
        # Trigger bill
        process_reading("device1", litres=30.0, total_l=530.0, flow_lpm=2.4)

    res = client.get("/api/bills/device1")
    assert res.status_code == 200
    bills = res.get_json()

    assert isinstance(bills, list)
    assert len(bills) == 1
    assert bills[0]["excess_l"] == 30.0
    assert bills[0]["amount"] == 3.00
    assert bills[0]["status"] == "unpaid"
    assert "ts" in bills[0]


def test_create_order_missing_bill_id(client):
    """POST /api/pay/create-order returns 400 when bill_id is missing or invalid."""
    res_empty = client.post("/api/pay/create-order", json={})
    assert res_empty.status_code == 400

    res_null = client.post("/api/pay/create-order", json={"bill_id": None})
    assert res_null.status_code == 400

    res_bad_str = client.post("/api/pay/create-order", json={"bill_id": "not-an-int"})
    assert res_bad_str.status_code == 400


def test_create_order_nonexistent_bill(client):
    """POST /api/pay/create-order returns 404 for nonexistent bill_id."""
    res = client.post("/api/pay/create-order", json={"bill_id": 9999})
    assert res.status_code == 404


def test_create_order_already_paid_bill(client, app):
    """POST /api/pay/create-order returns 400 if the bill is already paid."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        bill = Bill(device_id=device.id, excess_l=10.0, amount=1.0, status="paid")
        db.session.add(bill)
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/create-order", json={"bill_id": bill_id})
    assert res.status_code == 400
    assert "already paid" in res.get_json()["error"]


@patch("razorpay.Client")
def test_create_order_success(mock_razorpay_client, client, app):
    """POST /api/pay/create-order creates Razorpay order and updates bill."""
    mock_instance = MagicMock()
    mock_instance.order.create.return_value = {"id": "order_mock_12345", "amount": 1000}
    mock_razorpay_client.return_value = mock_instance

    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        bill = Bill(device_id=device.id, excess_l=100.0, amount=10.0, status="unpaid")
        db.session.add(bill)
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/create-order", json={"bill_id": bill_id})
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    assert data["order_id"] == "order_mock_12345"
    assert data["amount"] == 1000
    assert data["currency"] == "INR"
    assert data["bill_id"] == bill_id

    # Verify DB updated
    with app.app_context():
        updated_bill = db.session.get(Bill, bill_id)
        assert updated_bill.razorpay_order_id == "order_mock_12345"


def test_verify_payment_missing_fields(client):
    """POST /api/pay/verify returns 400 when required fields are missing."""
    res_empty = client.post("/api/pay/verify", json={})
    assert res_empty.status_code == 400

    res_partial = client.post("/api/pay/verify", json={"bill_id": 1, "order_id": "ord_1"})
    assert res_partial.status_code == 400


def test_verify_payment_nonexistent_bill(client):
    """POST /api/pay/verify returns 404 for non-existent bill."""
    res = client.post("/api/pay/verify", json={
        "bill_id": 9999,
        "order_id": "ord_123",
        "payment_id": "pay_123",
        "signature": "sig_123"
    })
    assert res.status_code == 404


def test_verify_payment_already_paid_bill(client, app):
    """POST /api/pay/verify returns 400 if bill is already paid."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        bill = Bill(device_id=device.id, excess_l=10.0, amount=1.0, status="paid", razorpay_order_id="ord_paid")
        db.session.add(bill)
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/verify", json={
        "bill_id": bill_id,
        "order_id": "ord_paid",
        "payment_id": "pay_123",
        "signature": "sig_123"
    })
    assert res.status_code == 400
    assert "already paid" in res.get_json()["error"]


def test_verify_payment_mismatched_order_id(client, app):
    """POST /api/pay/verify returns 400 if order_id does not match bill's order ID."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        bill = Bill(device_id=device.id, excess_l=10.0, amount=1.0, status="unpaid", razorpay_order_id="ord_expected")
        db.session.add(bill)
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/verify", json={
        "bill_id": bill_id,
        "order_id": "ord_WRONG_123",
        "payment_id": "pay_123",
        "signature": "sig_123"
    })
    assert res.status_code == 400
    assert "mismatch" in res.get_json()["error"].lower()

    # Verify status remains unpaid
    with app.app_context():
        bill_check = db.session.get(Bill, bill_id)
        assert bill_check.status == "unpaid"


@patch("razorpay.Client")
def test_verify_payment_invalid_signature(mock_razorpay_client, client, app):
    """POST /api/pay/verify returns 400 when Razorpay signature verification fails."""
    mock_instance = MagicMock()
    mock_instance.utility.verify_payment_signature.side_effect = Exception("Signature verification failed")
    mock_razorpay_client.return_value = mock_instance

    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        bill = Bill(device_id=device.id, excess_l=10.0, amount=1.0, status="unpaid", razorpay_order_id="ord_mock_123")
        db.session.add(bill)
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/verify", json={
        "bill_id": bill_id,
        "order_id": "ord_mock_123",
        "payment_id": "pay_mock_456",
        "signature": "invalid_signature"
    })
    assert res.status_code == 400
    assert "verification failed" in res.get_json()["error"].lower()

    # Verify status remains unpaid
    with app.app_context():
        bill_check = db.session.get(Bill, bill_id)
        assert bill_check.status == "unpaid"


@patch("razorpay.Client")
def test_verify_payment_success_marks_paid_and_turns_relay_on(mock_razorpay_client, client, app):
    """POST /api/pay/verify marks bill paid, saves payment_id, and sets relay to ON."""
    mock_instance = MagicMock()
    mock_instance.utility.verify_payment_signature.return_value = True
    mock_razorpay_client.return_value = mock_instance

    with app.app_context():
        # Trigger bill and turn relay OFF
        process_reading("device1", litres=20.0, total_l=520.0, flow_lpm=2.4)
        assert get_device_state("device1")["relay"] == "OFF"

        device = Device.query.filter_by(name="device1").first()
        bill = Bill.query.filter_by(device_id=device.id, status="unpaid").first()
        bill.razorpay_order_id = "order_mock_999"
        db.session.commit()
        bill_id = bill.id

    res = client.post("/api/pay/verify", json={
        "bill_id": bill_id,
        "order_id": "order_mock_999",
        "payment_id": "pay_mock_888",
        "signature": "valid_signature_hash"
    })
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    assert data["status"] == "paid"
    assert data["relay"] == "ON"
    assert data["payment_id"] == "pay_mock_888"

    # Verify DB state updated
    with app.app_context():
        bill_check = db.session.get(Bill, bill_id)
        assert bill_check.status == "paid"
        assert bill_check.payment_id == "pay_mock_888"
        # Verify device relay state is turned ON
        assert get_device_state("device1")["relay"] == "ON"


def test_update_device_quota_success(client, app):
    """POST /api/device/<device>/quota updates limit and rate in DB."""
    res = client.post("/api/device/device1/quota", json={
        "monthly_limit_l": 1200.0,
        "rate_per_l": 0.25
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["monthly_limit_l"] == 1200.0
    assert data["rate_per_l"] == 0.25

    # Verify subsequent GET /api/usage/device1 reflects new limit
    usage_res = client.get("/api/usage/device1")
    assert usage_res.status_code == 200
    assert usage_res.get_json()["limit"] == 1200.0


def test_update_device_quota_validation_errors(client):
    """POST /api/device/<device>/quota returns 400 on invalid input."""
    # Empty JSON
    res_empty = client.post("/api/device/device1/quota", json=None)
    assert res_empty.status_code == 400

    # Negative limit
    res_neg_limit = client.post("/api/device/device1/quota", json={"monthly_limit_l": -50})
    assert res_neg_limit.status_code == 400

    # Non-numeric rate
    res_bad_rate = client.post("/api/device/device1/quota", json={"rate_per_l": "invalid"})
    assert res_bad_rate.status_code == 400


def test_get_all_bills_admin(client, app):
    """GET /api/bills returns all bills in the system."""
    with app.app_context():
        device = Device(name="dev_admin_test")
        db.session.add(device)
        db.session.commit()

        b1 = Bill(device_id=device.id, excess_l=15.0, amount=1.5, status="paid", payment_id="pay_test_1")
        b2 = Bill(device_id=device.id, excess_l=25.0, amount=2.5, status="unpaid")
        db.session.add_all([b1, b2])
        db.session.commit()

    res = client.get("/api/bills")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data) >= 2
    assert any(b["payment_id"] == "pay_test_1" for b in data)


def test_auth_login_endpoints(client):
    """POST /api/auth/login handles admin and user roles."""
    # Admin login
    res_admin = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res_admin.status_code == 200
    assert res_admin.get_json()["role"] == "admin"

    # User login
    res_user = client.post("/api/auth/login", json={"username": "customer", "password": "user123"})
    assert res_user.status_code == 200
    assert res_user.get_json()["role"] == "user"

    # Empty username rejected
    res_bad = client.post("/api/auth/login", json={"username": ""})
    assert res_bad.status_code == 401


def test_admin_reset_month(client, app):
    """POST /api/device/<device>/reset-month resets usage to 0.0 and turns relay ON."""
    with app.app_context():
        device = Device.query.filter_by(name="device1").first()
        r = Reading(device_id=device.id, litres=50.0, total_l=550.0)
        db.session.add(r)
        db.session.commit()

    res = client.post("/api/device/device1/reset-month")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["total"] == 0.0
    assert data["relay"] == "ON"

    # Verify subsequent GET /api/usage reflects 0.0
    usage_res = client.get("/api/usage/device1")
    assert usage_res.status_code == 200
    assert usage_res.get_json()["total"] == 0.0

