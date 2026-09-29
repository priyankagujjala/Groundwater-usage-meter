from flask import Blueprint, jsonify, request
try:
    from .models import db, Device, Reading, Bill
    from .billing import get_device_state, set_device_relay, is_device_online
except ImportError:
    from models import db, Device, Reading, Bill
    from billing import get_device_state, set_device_relay, is_device_online

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
    device_obj = db.session.get(Device, device)
    if not device_obj:
        # Create default device entity if not found
        device_obj = Device(id=device, name=f"Meter {device}", monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device_obj)
        db.session.commit()

    # Get latest reading for total usage
    latest_reading = (
        Reading.query.filter_by(device_id=device)
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
    # Fetch latest 100 readings ordered descending by timestamp
    recent_readings = (
        Reading.query.filter_by(device_id=device)
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
    device_obj = db.session.get(Device, device)
    if not device_obj:
        device_obj = Device(id=device, name=f"Meter {device}", monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device_obj)
        db.session.commit()

    try:
        set_device_relay(device, relay_val)
        return jsonify({
            "device": device,
            "relay": relay_val,
            "success": True,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to dispatch relay command: {str(e)}"}), 500


@api_bp.route("/api/bills/<device>", methods=["GET"])
def get_bills(device):
    """
    GET /api/bills/<device>
    Returns: list of {"id", "excess_l", "amount", "status", "ts"}, newest first
    """
    bills = (
        Bill.query.filter_by(device_id=device)
        .order_by(Bill.ts.desc(), Bill.id.desc())
        .all()
    )

    bills_list = [
        {
            "id": b.id,
            "excess_l": round(b.excess_l, 2),
            "amount": round(b.amount, 2),
            "status": b.status,
            "ts": b.ts.isoformat() if b.ts else None,
        }
        for b in bills
    ]

    return jsonify(bills_list), 200


# ==============================================================================
# RAZORPAY PAYMENT ENDPOINTS (TODO: To be implemented in Task 4 / Final Task)
# ==============================================================================

@api_bp.route("/api/pay/create-order", methods=["POST"])
def create_payment_order():
    """
    POST /api/pay/create-order
    TODO: Razorpay Order Creation (Final Task)
    """
    return jsonify({
        "error": "Not Implemented",
        "message": "Razorpay order creation will be implemented in the final Razorpay task."
    }), 501


@api_bp.route("/api/pay/verify", methods=["POST"])
def verify_payment():
    """
    POST /api/pay/verify
    TODO: Razorpay Signature Verification & Bill Mark-Paid (Final Task)
    """
    return jsonify({
        "error": "Not Implemented",
        "message": "Razorpay signature verification will be implemented in the final Razorpay task."
    }), 501
