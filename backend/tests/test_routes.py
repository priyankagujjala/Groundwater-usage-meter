import json
from backend.billing import process_reading

def test_health_check(client):
    """GET /health returns 200 with {'ok': True}."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True}


def test_get_usage_structure_and_values(client, app):
    """GET /api/usage/<device> returns strict contract structure."""
    with app.app_context():
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
        process_reading("device1", litres=1.0, total_l=10.0, flow_lpm=2.0)
        process_reading("device1", litres=2.0, total_l=12.0, flow_lpm=2.2)
        process_reading("device1", litres=3.0, total_l=15.0, flow_lpm=2.5)

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


from unittest.mock import MagicMock, patch
from backend.models import db, Bill, Reading, Device
from backend.billing import get_device_state


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
        bill = Bill(device_id="device1", excess_l=10.0, amount=1.0, status="paid")
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
        bill = Bill(device_id="device1", excess_l=100.0, amount=10.0, status="unpaid")
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
        bill = Bill(device_id="device1", excess_l=10.0, amount=1.0, status="paid", razorpay_order_id="ord_paid")
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
        bill = Bill(device_id="device1", excess_l=10.0, amount=1.0, status="unpaid", razorpay_order_id="ord_expected")
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
        bill = Bill(device_id="device1", excess_l=10.0, amount=1.0, status="unpaid", razorpay_order_id="ord_mock_123")
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

        bill = Bill.query.filter_by(device_id="device1", status="unpaid").first()
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

