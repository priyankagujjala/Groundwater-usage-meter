import os
import logging
from datetime import datetime, timezone
from flask import has_app_context

try:
    from .models import db, Device, Reading, Bill
except ImportError:
    from models import db, Device, Reading, Bill

logger = logging.getLogger("billing")

# Re-billing threshold constant (default: 10.0 Litres)
MIN_REBILL_L = float(os.getenv("MIN_REBILL_L", "10.0"))

# Gracefully import publish_relay from mqtt_client, fallback to mqtt_stub if not yet present
try:
    from .mqtt_client import publish_relay
except (ImportError, ModuleNotFoundError):
    try:
        from .mqtt_stub import publish_relay
    except (ImportError, ModuleNotFoundError):
        try:
            from mqtt_client import publish_relay
        except (ImportError, ModuleNotFoundError):
            from mqtt_stub import publish_relay

# In-memory device runtime states (latest telemetry & relay state)
# Keyed by device name string (e.g. 'device1')
device_states = {}

def get_device_state(device_name: str) -> dict:
    """Retrieve or initialize in-memory runtime state for a device name."""
    if device_name not in device_states:
        device_states[device_name] = {
            "flow_lpm": 0.0,
            "relay": "ON",
            "last_seen": None,
        }
    return device_states[device_name]


def set_device_relay(device_name: str, state: str) -> bool:
    """
    Manually update device relay state and dispatch MQTT command.
    """
    state_upper = state.upper()
    if state_upper not in ("ON", "OFF"):
        raise ValueError(f"Invalid relay state: '{state}'. Must be 'ON' or 'OFF'.")

    # Publish MQTT command
    publish_relay(device_name, state_upper)

    # Update in-memory state
    dev_state = get_device_state(device_name)
    dev_state["relay"] = state_upper
    if state_upper == "OFF":
        dev_state["flow_lpm"] = 0.0
    return True


def is_device_online(device_name: str, max_silence_seconds: int = 30) -> bool:
    """
    Check if the device is online via MQTT status topic first,
    falling back to recent telemetry timestamp.
    """
    try:
        from .mqtt_client import get_device_status
    except (ImportError, ModuleNotFoundError):
        try:
            from mqtt_client import get_device_status
        except (ImportError, ModuleNotFoundError):
            get_device_status = None

    if get_device_status:
        status = get_device_status(device_name)
        if status == "online":
            return True
        elif status == "offline":
            return False

    dev_state = get_device_state(device_name)
    last_seen = dev_state.get("last_seen")
    if not last_seen:
        return False
    now = datetime.now(timezone.utc)
    # Ensure last_seen is timezone-aware
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen).total_seconds() <= max_silence_seconds


def process_reading(device_name: str, litres: float, total_l: float, flow_lpm: float) -> dict:
    """
    Entry point for processing a new telemetry reading:
    Ensures execution within a valid Flask application context (thread-safe for MQTT listener).
    """
    if not has_app_context():
        try:
            from .app import _global_app, create_app
        except ImportError:
            from app import _global_app, create_app
        app_instance = _global_app or create_app()
        with app_instance.app_context():
            return _execute_process_reading(device_name, litres, total_l, flow_lpm)
    else:
        return _execute_process_reading(device_name, litres, total_l, flow_lpm)


def _execute_process_reading(device_name: str, litres: float, total_l: float, flow_lpm: float) -> dict:
    """
    Updates runtime state, checks monthly limit threshold, creates bill (once per interval),
    accounting for already-billed excess after payments, and enforces relay cut-off.

    Note: The reading itself is already persisted by mqtt_client.py (_insert_reading_to_db)
    so it is NOT duplicated here.
    """
    # 1. Update in-memory runtime telemetry
    dev_state = get_device_state(device_name)
    dev_state["flow_lpm"] = round(float(flow_lpm), 2)
    dev_state["last_seen"] = datetime.now(timezone.utc)

    # 2. Lookup or seed device entity in SQLAlchemy DB by device name ('device1')
    device = Device.query.filter_by(name=device_name).first()
    if not device:
        logger.info(f"Auto-registering device '{device_name}' with default limits.")
        device = Device(name=device_name, monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device)
        db.session.commit()

    action = "RECORDED"
    bill_created = None

    # 3. Limit and Billing Enforcement Logic:
    if total_l >= device.monthly_limit_l:
        # Check if an unpaid bill already exists using integer device.id
        existing_unpaid_bill = Bill.query.filter_by(device_id=device.id, status="unpaid").first()

        if not existing_unpaid_bill:
            # Calculate total excess already billed across all past bills (paid or unpaid)
            all_bills = Bill.query.filter_by(device_id=device.id).all()
            already_billed_l = sum(b.excess_l for b in all_bills)
            unbilled_excess_l = (total_l - device.monthly_limit_l) - already_billed_l

            should_create_bill = False
            excess_l_to_bill = 0.0

            if len(all_bills) == 0:
                # First breach: initial limit crossing
                should_create_bill = True
                excess_l_to_bill = round(total_l - device.monthly_limit_l, 2)
                # Guarantee minimum measurable excess if at exact boundary
                if excess_l_to_bill <= 0.0:
                    excess_l_to_bill = round(float(litres) if litres > 0 else 0.1, 2)
            elif unbilled_excess_l >= MIN_REBILL_L:
                # Subsequent breach after earlier bill(s) paid and unbilled excess reaches MIN_REBILL_L
                should_create_bill = True
                excess_l_to_bill = round(unbilled_excess_l, 2)

            if should_create_bill:
                amount = round(excess_l_to_bill * device.rate_per_l, 2)
                if amount <= 0.0:
                    amount = 0.01

                new_bill = Bill(
                    device_id=device.id,
                    excess_l=excess_l_to_bill,
                    amount=amount,
                    status="unpaid",
                    ts=datetime.now(timezone.utc)
                )
                db.session.add(new_bill)
                db.session.flush() # assign new_bill.id

                # Cut off relay
                publish_relay(device_name, "OFF")
                dev_state["relay"] = "OFF"
                dev_state["flow_lpm"] = 0.0

                action = "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
                bill_created = new_bill.to_dict()
                logger.warning(
                    f"[QUOTA BREACH] Device '{device_name}' exceeded limit with unbilled excess {excess_l_to_bill:.2f}L (Total: {total_l}L). "
                    f"Generated Bill #{new_bill.id} for ₹{amount:.2f} ({excess_l_to_bill}L excess). Relay turned OFF."
                )
        else:
            # Unpaid bill exists, ensure relay remains OFF
            if dev_state["relay"] != "OFF":
                publish_relay(device_name, "OFF")
                dev_state["relay"] = "OFF"
                dev_state["flow_lpm"] = 0.0
            action = "UNPAID_BILL_PENDING_RELAY_OFF"

    db.session.commit()

    return {
        "action": action,
        "device_id": device_name,
        "total_l": total_l,
        "limit_l": device.monthly_limit_l,
        "relay": dev_state["relay"],
        "flow_lpm": dev_state["flow_lpm"],
        "bill": bill_created,
    }
