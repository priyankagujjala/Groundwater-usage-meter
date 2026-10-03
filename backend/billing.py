import os
import logging
from datetime import datetime, timezone
from flask import has_app_context

try:
    from .models import db, Device, Reading, Bill
except ImportError:
    from models import db, Device, Reading, Bill

logger = logging.getLogger("billing")

# Re-billing threshold constant (generates a new bill for every 1.0 Litre excess after prior bills)
MIN_REBILL_L = float(os.getenv("MIN_REBILL_L", "1.0"))

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


def set_device_relay(device_name: str, state: str, wait_for_ack: bool = False) -> bool:
    """
    Manually update device relay state and dispatch MQTT command.
    """
    state_upper = state.upper()
    if state_upper not in ("ON", "OFF"):
        raise ValueError(f"Invalid relay state: '{state}'. Must be 'ON' or 'OFF'.")

    # Publish MQTT command
    published = publish_relay(device_name, state_upper, wait_for_ack=wait_for_ack)

    # Update in-memory state
    dev_state = get_device_state(device_name)
    dev_state["relay"] = state_upper
    if state_upper == "OFF":
        dev_state["flow_lpm"] = 0.0
    return published


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
    Updates runtime state, checks free limit threshold for per-litre billing,
    and enforces automatic relay shut-off when the monthly limit is reached.

    Dual-limit Policy:
    1. 0 to free_limit_l: Free water allowance, no bills, relay ON.
    2. free_limit_l to monthly_limit_l: Free limit crossed; generates bills per litre excess, relay remains ON.
    3. >= monthly_limit_l: Monthly cutoff reached; automatic relay valve shut OFF.
    """
    # 1. Update in-memory runtime telemetry
    dev_state = get_device_state(device_name)
    dev_state["flow_lpm"] = round(float(flow_lpm), 2)
    dev_state["last_seen"] = datetime.now(timezone.utc)

    # 2. Lookup or seed device entity in SQLAlchemy DB by device name ('device1')
    device = Device.query.filter_by(name=device_name).first()
    if not device:
        logger.info(f"Auto-registering device '{device_name}' with default limits.")
        device = Device(name=device_name, free_limit_l=500.0, monthly_limit_l=1000.0, rate_per_l=0.10)
        db.session.add(device)
        db.session.commit()

    action = "RECORDED"
    bill_created = None

    # 3. Billing Generation Logic (Triggers when usage exceeds free_limit_l)
    if total_l > device.free_limit_l:
        all_bills = Bill.query.filter_by(device_id=device.id).all()
        already_billed_l = sum(b.excess_l for b in all_bills)
        raw_excess = total_l - device.free_limit_l
        unbilled_excess_l = max(0.0, raw_excess - already_billed_l)

        should_create_bill = False
        excess_l_to_bill = 0.0

        is_monthly_cutoff = total_l >= device.monthly_limit_l

        if len(all_bills) == 0:
            # First breach of free tier
            should_create_bill = True
            excess_l_to_bill = round(unbilled_excess_l, 2)
            if excess_l_to_bill <= 0.0:
                excess_l_to_bill = round(float(litres) if litres > 0 else 0.1, 2)
        elif is_monthly_cutoff and unbilled_excess_l > 0:
            # Monthly cutoff reached: bill all remaining unbilled excess immediately
            should_create_bill = True
            excess_l_to_bill = round(unbilled_excess_l, 2)
        elif unbilled_excess_l >= MIN_REBILL_L:
            # Accumulated unbilled litres after prior bills
            should_create_bill = True
            excess_l_to_bill = round(unbilled_excess_l, 2)

        if should_create_bill and excess_l_to_bill > 0:
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

            action = "MONTHLY_LIMIT_EXCEEDED_BILL_CREATED" if is_monthly_cutoff else "FREE_LIMIT_EXCEEDED_BILL_CREATED"
            bill_created = new_bill.to_dict()
            logger.warning(
                f"[BILL GENERATED] Device '{device_name}' (Total: {total_l:.2f}L, Free: {device.free_limit_l:.2f}L, Cutoff: {device.monthly_limit_l:.2f}L). "
                f"Generated Bill #{new_bill.id} for {excess_l_to_bill:.2f}L excess = ₹{amount:.2f}."
            )
        else:
            action = "PAID_TIER_USAGE"

    # 4. Relay Cutoff Enforcement (Triggers when total_l hits monthly_limit_l)
    if total_l >= device.monthly_limit_l:
        if dev_state["relay"] != "OFF":
            publish_relay(device_name, "OFF")
            dev_state["relay"] = "OFF"
            dev_state["flow_lpm"] = 0.0
            logger.warning(
                f"[MONTHLY LIMIT BREACH] Device '{device_name}' hit monthly limit {device.monthly_limit_l}L (Total: {total_l}L). Motor relay valve turned OFF."
            )
        action = "MONTHLY_LIMIT_BREACHED_RELAY_OFF"

    db.session.commit()

    return {
        "action": action,
        "device_id": device_name,
        "total_l": total_l,
        "free_limit_l": device.free_limit_l,
        "limit_l": device.monthly_limit_l,
        "monthly_limit_l": device.monthly_limit_l,
        "relay": dev_state["relay"],
        "flow_lpm": dev_state["flow_lpm"],
        "bill": bill_created,
    }


def check_and_enforce_billing(device_obj):
    """
    Evaluates latest reading for device against free limit and monthly cutoff.
    - If total_l > free_limit_l: generates bill for unbilled excess (especially upon reaching monthly cutoff).
    - If total_l >= monthly_limit_l: automatically turns relay OFF.
    """
    latest_reading = (
        Reading.query.filter_by(device_id=device_obj.id)
        .order_by(Reading.ts.desc(), Reading.id.desc())
        .first()
    )
    if not latest_reading:
        return None

    total_l = latest_reading.total_l
    created_bill = None

    # 1. Billing generation for excess over free limit
    if total_l > device_obj.free_limit_l:
        all_bills = Bill.query.filter_by(device_id=device_obj.id).all()
        already_billed_l = sum(b.excess_l for b in all_bills)
        raw_excess = total_l - device_obj.free_limit_l
        unbilled_excess_l = max(0.0, raw_excess - already_billed_l)

        is_monthly_breach = total_l >= device_obj.monthly_limit_l
        if (len(all_bills) == 0 and unbilled_excess_l > 0) or (is_monthly_breach and unbilled_excess_l > 0) or unbilled_excess_l >= MIN_REBILL_L:
            excess_l_to_bill = round(unbilled_excess_l, 2)
            if excess_l_to_bill <= 0.0:
                excess_l_to_bill = 0.1
            amount = round(excess_l_to_bill * device_obj.rate_per_l, 2)
            if amount <= 0.0:
                amount = 0.01

            new_bill = Bill(
                device_id=device_obj.id,
                excess_l=excess_l_to_bill,
                amount=amount,
                status="unpaid",
                ts=datetime.now(timezone.utc)
            )
            db.session.add(new_bill)
            db.session.commit()
            created_bill = new_bill

    # 2. Relay cutoff enforcement for monthly limit
    if total_l >= device_obj.monthly_limit_l:
        dev_state = get_device_state(device_obj.name)
        if dev_state["relay"] != "OFF":
            publish_relay(device_obj.name, "OFF")
            dev_state["relay"] = "OFF"
            dev_state["flow_lpm"] = 0.0

    return created_bill


def reset_device_litres(device_name: str) -> dict:
    """
    Resets the monthly usage litres counter for a device:
    1. Records a 0.0L reading to reset the usage counter for this month to 0.
    2. Restores in-memory relay state to 'ON' and clears flow rate.
    3. Dispatches MQTT command with reset: true so ESP32 local hardware counter resets and valve opens.
    """
    device = Device.query.filter_by(name=device_name).first()
    if not device:
        device = Device(name=device_name, free_limit_l=500.0, monthly_limit_l=1000.0, rate_per_l=0.10)
        db.session.add(device)

    # Insert fresh 0.0L cycle start reading
    reset_reading = Reading(
        device_id=device.id,
        litres=0.0,
        total_l=0.0,
        ts=datetime.now(timezone.utc)
    )
    db.session.add(reset_reading)

    # Settle any open unpaid bills
    unpaid_bills = Bill.query.filter_by(device_id=device.id, status="unpaid").all()
    for ub in unpaid_bills:
        ub.status = "paid"
        ub.payment_id = "admin_reset"

    db.session.commit()

    # Reset in-memory device state
    dev_state = get_device_state(device_name)
    dev_state["relay"] = "ON"
    dev_state["flow_lpm"] = 0.0

    # Publish MQTT reset command (relay: ON, reset: true)
    try:
        publish_relay(device_name, "ON", wait_for_ack=True, reset_cycle=True)
    except TypeError:
        publish_relay(device_name, "ON", wait_for_ack=True)

    logger.info(f"[LITRES RESET] Device '{device_name}' monthly litres reset to 0.0L. Relay turned ON.")
    return {
        "success": True,
        "device": device_name,
        "total": 0.0,
        "relay": "ON",
        "message": f"Monthly litres usage for '{device_name}' has been successfully reset to 0.0 L."
    }


# Backwards compatibility alias
reset_device_month = reset_device_litres


