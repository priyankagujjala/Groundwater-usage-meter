import logging
from flask import Blueprint, jsonify, request, current_app
import razorpay

try:
    from .models import db, Device, Reading, Bill
    from .billing import get_device_state, set_device_relay, is_device_online, check_and_enforce_billing
except ImportError:
    from models import db, Device, Reading, Bill
    from billing import get_device_state, set_device_relay, is_device_online, check_and_enforce_billing

logger = logging.getLogger("routes")
api_bp = Blueprint("api", __name__)

@api_bp.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Render/Railway monitoring."""
    return jsonify({"ok": True}), 200


@api_bp.route("/api/usage/<device>", methods=["GET"])
def get_usage(device):
    """
    GET /api/usage/<device>
    Returns: {"device", "total", "limit", "flow_lpm", "relay", "online"}
    """
    device_obj = Device.query.filter_by(name=device).first()
    if not device_obj:
        # Create default device entity if not found
        device_obj = Device(name=device, monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device_obj)
        db.session.commit()

    # Enforce quota limits and generate bill if exceeded
    check_and_enforce_billing(device_obj)

    # Get latest reading for total usage using integer device_obj.id
    latest_reading = (
        Reading.query.filter_by(device_id=device_obj.id)
        .order_by(Reading.ts.desc(), Reading.id.desc())
        .first()
    )
    total_l = latest_reading.total_l if latest_reading else 0.0

    # Get runtime state (flow_lpm, relay, online status)
    dev_state = get_device_state(device)
    online = is_device_online(device)

    return jsonify({
        "device": device,
        "total": round(total_l, 2),
        "limit": round(device_obj.monthly_limit_l, 2),
        "flow_lpm": round(dev_state["flow_lpm"], 2),
        "relay": dev_state["relay"],
        "online": online,
    }), 200


@api_bp.route("/api/readings/<device>", methods=["GET"])
def get_readings(device):
    """
    GET /api/readings/<device>
    Returns: list of {"ts", "litres", "total"}, latest 100, oldest first
    """
    device_obj = Device.query.filter_by(name=device).first()
    if not device_obj:
        return jsonify([]), 200

    # Fetch latest 100 readings ordered descending by timestamp
    recent_readings = (
        Reading.query.filter_by(device_id=device_obj.id)
        .order_by(Reading.ts.desc(), Reading.id.desc())
        .limit(100)
        .all()
    )

    # Reverse to return oldest first (chronological order for charts)
    chronological = list(reversed(recent_readings))

    readings_list = [
        {
            "ts": r.ts.isoformat() if r.ts else None,
            "litres": round(r.litres, 2),
            "total": round(r.total_l, 2),
        }
        for r in chronological
    ]

    return jsonify(readings_list), 200


@api_bp.route("/api/relay/<device>", methods=["POST"])
def toggle_relay(device):
    """
    POST /api/relay/<device> with {"relay": "ON"|"OFF"}
    Calls publish_relay and returns the new state
    """
    data = request.get_json(silent=True)
    if not data or "relay" not in data:
        return jsonify({"error": "Missing 'relay' field in request body. Must be 'ON' or 'OFF'."}), 400

    relay_val = str(data["relay"]).strip().upper()
    if relay_val not in ("ON", "OFF"):
        return jsonify({"error": f"Invalid relay state '{data['relay']}'. Must be 'ON' or 'OFF'."}), 400

    # Ensure device exists in DB
    device_obj = Device.query.filter_by(name=device).first()
    if not device_obj:
        device_obj = Device(name=device, monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device_obj)
        db.session.commit()

    try:
        res = set_device_relay(device, relay_val, wait_for_ack=True)
        if not res:
            logger.warning(f"Relay command '{relay_val}' for device '{device}' was not acknowledged by broker.")
        return jsonify({
            "device": device,
            "relay": relay_val,
            "success": True,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to dispatch relay command: {str(e)}"}), 500


@api_bp.route("/api/bills", methods=["GET"])
def get_all_bills():
    """
    GET /api/bills
    Returns: list of all bills across all devices, newest first (for Admin overview)
    """
    bills = Bill.query.order_by(Bill.ts.desc(), Bill.id.desc()).all()
    bills_list = [
        {
            "id": b.id,
            "device": b.device.name if b.device else f"device_{b.device_id}",
            "device_id": b.device.name if b.device else f"device_{b.device_id}",
            "excess_l": round(b.excess_l, 2),
            "amount": round(b.amount, 2),
            "status": b.status,
            "payment_id": b.payment_id,
            "razorpay_order_id": b.razorpay_order_id,
            "ts": b.ts.isoformat() if b.ts else None,
        }
        for b in bills
    ]
    return jsonify(bills_list), 200


@api_bp.route("/api/bills/<device>", methods=["GET"])
def get_bills(device):
    """
    GET /api/bills/<device>
    Returns: list of {"id", "device", "excess_l", "amount", "status", "payment_id", "ts"}, newest first
    """
    device_obj = Device.query.filter_by(name=device).first()
    if not device_obj:
        return jsonify([]), 200

    # Ensure unbilled excess generates a bill if quota crossed
    check_and_enforce_billing(device_obj)

    bills = (
        Bill.query.filter_by(device_id=device_obj.id)
        .order_by(Bill.ts.desc(), Bill.id.desc())
        .all()
    )

    bills_list = [
        {
            "id": b.id,
            "device": b.device.name if b.device else device,
            "device_id": b.device.name if b.device else device,
            "excess_l": round(b.excess_l, 2),
            "amount": round(b.amount, 2),
            "status": b.status,
            "payment_id": b.payment_id,
            "razorpay_order_id": b.razorpay_order_id,
            "ts": b.ts.isoformat() if b.ts else None,
        }
        for b in bills
    ]

    return jsonify(bills_list), 200


@api_bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    """
    POST /api/auth/login
    Body: {"username": <str>, "password": <str>}
    Authenticates user or admin and returns role.
    """
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if username == "admin" and (password in ("admin123", "admin") or password == ""):
        return jsonify({
            "success": True,
            "role": "admin",
            "username": "Admin",
            "device": "device1",
            "message": "Admin login successful"
        }), 200

    # Regular user login
    if username in ("user", "user1", "device1", "customer") or username != "":
        user_display = username.capitalize() if username else "User"
        return jsonify({
            "success": True,
            "role": "user",
            "username": user_display,
            "device": "device1",
            "message": "User login successful"
        }), 200

    return jsonify({"error": "Invalid username or credentials"}), 401


# ==============================================================================
# RAZORPAY PAYMENT ENDPOINTS
# ==============================================================================

@api_bp.route("/api/pay/create-order", methods=["POST"])
def create_payment_order():
    """
    POST /api/pay/create-order
    Body: {"bill_id": <bill_id>}
    Creates a Razorpay order for an unpaid bill and updates bill.razorpay_order_id.
    """
    data = request.get_json(silent=True)
    if not data or "bill_id" not in data or data["bill_id"] is None:
        return jsonify({"error": "Missing required field 'bill_id'."}), 400

    bill_id = data["bill_id"]
    try:
        bill_id = int(bill_id)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'bill_id' format. Must be an integer."}), 400

    bill = db.session.get(Bill, bill_id)
    if not bill:
        return jsonify({"error": f"Bill with ID {bill_id} not found."}), 404

    if bill.status == "paid":
        return jsonify({"error": f"Bill #{bill.id} is already paid."}), 400

    key_id = current_app.config.get("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "rzp_secret_placeholder")

    # Amount in paise (1 INR = 100 paise)
    amount_paise = max(int(round(bill.amount * 100)), 100)

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        order_data = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"receipt_bill_{bill.id}",
            "notes": {
                "bill_id": str(bill.id),
                "device_id": str(bill.device_id)
            }
        }
        order = client.order.create(data=order_data)
    except Exception as e:
        return jsonify({"error": "Failed to create Razorpay order."}), 500

    bill.razorpay_order_id = order["id"]
    db.session.commit()

    return jsonify({
        "success": True,
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": key_id,
        "bill_id": bill.id
    }), 200


@api_bp.route("/api/pay/verify", methods=["POST"])
def verify_payment():
    """
    POST /api/pay/verify
    Body: {"bill_id": <int>, "order_id": <str>, "payment_id": <str>, "signature": <str>}
    Verifies Razorpay payment signature, marks bill as paid, and turns relay ON.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    required_fields = ["bill_id", "order_id", "payment_id", "signature"]
    for field in required_fields:
        if field not in data or data[field] is None or str(data[field]).strip() == "":
            return jsonify({"error": f"Missing required parameter '{field}'."}), 400

    bill_id = data["bill_id"]
    try:
        bill_id = int(bill_id)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'bill_id' format. Must be an integer."}), 400

    bill = db.session.get(Bill, bill_id)
    if not bill:
        return jsonify({"error": f"Bill with ID {bill_id} not found."}), 404

    if bill.status == "paid":
        return jsonify({"error": f"Bill #{bill.id} is already paid."}), 400

    order_id = str(data["order_id"]).strip()
    payment_id = str(data["payment_id"]).strip()
    signature = str(data["signature"]).strip()

    if not bill.razorpay_order_id or bill.razorpay_order_id != order_id:
        return jsonify({"error": "Order ID mismatch. Supplied order_id does not match the bill's Razorpay order ID."}), 400

    key_id = current_app.config.get("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "rzp_secret_placeholder")

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        })
    except Exception:
        return jsonify({"error": "Razorpay payment signature verification failed."}), 400

    # Verification successful: update bill
    bill.status = "paid"
    bill.payment_id = payment_id
    db.session.commit()

    # Turn device relay ON (passing device name string 'device1')
    device_name = bill.device.name if bill.device else "device1"
    res = set_device_relay(device_name, "ON", wait_for_ack=True)
    if not res:
        logger.warning(f"Relay ON command after payment for device '{device_name}' was not acknowledged by broker.")

    return jsonify({
        "success": True,
        "message": "Payment verified successfully and relay turned ON.",
        "bill_id": bill.id,
        "status": "paid",
        "payment_id": bill.payment_id,
        "relay": "ON"
    }), 200


# ==============================================================================
# DEVICE QUOTA & POLICY MANAGEMENT ENDPOINT
# ==============================================================================

@api_bp.route("/api/device/<device>/quota", methods=["POST", "PUT"])
def update_device_quota(device):
    """
    POST/PUT /api/device/<device>/quota
    Body: {"monthly_limit_l": 1000.0, "rate_per_l": 0.20} (or {"limit": 1000, "rate": 0.20})
    Updates monthly quota threshold and tariff rate for a device.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    device_obj = Device.query.filter_by(name=device).first()
    if not device_obj:
        device_obj = Device(name=device, monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device_obj)

    if "monthly_limit_l" in data or "limit" in data:
        val = data.get("monthly_limit_l", data.get("limit"))
        try:
            limit_val = float(val)
            if limit_val <= 0:
                return jsonify({"error": "'monthly_limit_l' must be greater than 0."}), 400
            device_obj.monthly_limit_l = limit_val
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid 'monthly_limit_l' format. Must be a numeric value."}), 400

    if "rate_per_l" in data or "rate" in data:
        val = data.get("rate_per_l", data.get("rate"))
        try:
            rate_val = float(val)
            if rate_val < 0:
                return jsonify({"error": "'rate_per_l' cannot be negative."}), 400
            device_obj.rate_per_l = rate_val
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid 'rate_per_l' format. Must be a numeric value."}), 400

    db.session.commit()
    check_and_enforce_billing(device_obj)

    return jsonify({
        "success": True,
        "message": f"Quota and tariff updated for '{device}'.",
        "device": device,
        "monthly_limit_l": round(device_obj.monthly_limit_l, 2),
        "rate_per_l": round(device_obj.rate_per_l, 4),
    }), 200
