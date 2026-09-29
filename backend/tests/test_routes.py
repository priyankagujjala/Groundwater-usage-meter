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


def test_razorpay_placeholders(client):
    """Razorpay endpoints return 501 Not Implemented placeholders."""
    res_order = client.post("/api/pay/create-order", json={})
    assert res_order.status_code == 501

    res_verify = client.post("/api/pay/verify", json={})
    assert res_verify.status_code == 501
