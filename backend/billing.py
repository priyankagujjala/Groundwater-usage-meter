import logging
from datetime import datetime, timezone

try:
    from .models import db, Device, Reading, Bill
except ImportError:
    from models import db, Device, Reading, Bill

logger = logging.getLogger("billing")

# Gracefully import publish_relay and getters from mqtt_client, fallback to mqtt_stub if not yet present
try:
    from .mqtt_client import publish_relay, get_relay_state, get_device_status, get_last_flow_lpm
except (ImportError, ModuleNotFoundError):
    try:
        from .mqtt_stub import publish_relay
        get_relay_state = None
        get_device_status = None
        get_last_flow_lpm = None
    except (ImportError, ModuleNotFoundError):
        try:
            from mqtt_client import publish_relay, get_relay_state, get_device_status, get_last_flow_lpm
        except (ImportError, ModuleNotFoundError):
            from mqtt_stub import publish_relay
            get_relay_state = None
            get_device_status = None
            get_last_flow_lpm = None

# In-memory device runtime states (latest telemetry & relay state)
# Structure: { device_id: {"flow_lpm": float, "relay": "ON"|"OFF", "last_seen": datetime} }
device_states = {}

def get_device_state(device_id: str) -> dict:
    """Retrieve or initialize in-memory runtime state for a device."""
    if device_id not in device_states:
        device_states[device_id] = {
            "flow_lpm": 0.0,
            "relay": "ON",
            "last_seen": None,
        }

    dev_state = device_states[device_id]

    # Sync with live mqtt_client getters if available
    if get_relay_state is not None:
        try:
            live_relay = get_relay_state(device_id)
            if live_relay:
                dev_state["relay"] = live_relay
        except Exception:
            pass

    if get_last_flow_lpm is not None:
        try:
            live_flow = get_last_flow_lpm(device_id)
            if live_flow is not None:
                dev_state["flow_lpm"] = round(float(live_flow), 2)
        except Exception:
            pass

    return dev_state


def set_device_relay(device_id: str, state: str) -> bool:
    """
    Manually update device relay state and dispatch MQTT command.
    """
    state_upper = state.upper()
    if state_upper not in ("ON", "OFF"):
        raise ValueError(f"Invalid relay state: '{state}'. Must be 'ON' or 'OFF'.")

    # Publish MQTT command
    publish_relay(device_id, state_upper)

    # Update in-memory state
    dev_state = get_device_state(device_id)
    dev_state["relay"] = state_upper
    if state_upper == "OFF":
        dev_state["flow_lpm"] = 0.0
    return True


def is_device_online(device_id: str, max_silence_seconds: int = 30) -> bool:
    """Check if the device has transmitted telemetry recently."""
    if get_device_status is not None:
        try:
            status = get_device_status(device_id)
            if status in ("online", "offline"):
                return status == "online"
        except Exception:
            pass

    dev_state = get_device_state(device_id)
    last_seen = dev_state.get("last_seen")
    if not last_seen:
        return False
    now = datetime.now(timezone.utc)
    # Ensure last_seen is timezone-aware
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen).total_seconds() <= max_silence_seconds


def process_reading(device_id: str, litres: float, total_l: float, flow_lpm: float) -> dict:
    """
    Ingest a telemetry reading, persist it to the database, update runtime state,
    and enforce monthly quota limit and billing logic.

    Args:
        device_id (str): ID of the device (e.g., 'device1')
        litres (float): Litres extracted during the last measurement interval
        total_l (float): Cumulative total litres extracted
        flow_lpm (float): Current flow rate in Litres per minute

    Returns:
        dict: Result summary with reading info, quota status, and billing action taken.
    """
    # 1. Update in-memory runtime telemetry
    dev_state = get_device_state(device_id)
    dev_state["flow_lpm"] = round(float(flow_lpm), 2)
    dev_state["last_seen"] = datetime.now(timezone.utc)

    # 2. Lookup or seed device entity
    device = db.session.get(Device, device_id)
    if not device:
        logger.info(f"Auto-registering device '{device_id}' with default limits.")
        device = Device(id=device_id, name=f"Meter {device_id}", monthly_limit_l=500.0, rate_per_l=0.10)
        db.session.add(device)
        db.session.commit()

    # 3. Persist new reading record
    reading = Reading(
        device_id=device_id,
        litres=float(litres),
        total_l=float(total_l),
        ts=datetime.now(timezone.utc)
    )
    db.session.add(reading)

    action = "RECORDED"
    bill_created = None

    # 4. Limit and Billing Enforcement Logic:
    # Calculate total excess litres across all readings
    total_excess_l = max(0.0, round(total_l - device.monthly_limit_l, 2))

    # Calculate total excess litres already billed across all existing bills (paid + unpaid)
    existing_bills = Bill.query.filter_by(device_id=device_id).all()
    already_billed_excess = sum(b.excess_l for b in existing_bills)
    new_unbilled_excess = max(0.0, round(total_excess_l - already_billed_excess, 2))

    # Check if an unpaid bill already exists
    existing_unpaid_bill = next((b for b in existing_bills if b.status == "unpaid"), None)

    if existing_unpaid_bill:
        # Unpaid bill exists, ensure relay remains OFF
        if dev_state["relay"] != "OFF":
            publish_relay(device_id, "OFF")
            dev_state["relay"] = "OFF"
            dev_state["flow_lpm"] = 0.0
        action = "UNPAID_BILL_PENDING_RELAY_OFF"
    elif total_l >= device.monthly_limit_l:
        # No unpaid bill exists. Determine whether to create a new bill:
        # - For first quota crossing (no previous bills exist): create bill for new_unbilled_excess.
        # - For follow-up bills (previous bills exist): create bill only if new_unbilled_excess >= 10.0 L.
        is_first_bill = len(existing_bills) == 0
        should_create_bill = is_first_bill or (new_unbilled_excess >= 10.0)

        if should_create_bill:
            excess_l = new_unbilled_excess
            if excess_l <= 0.0:
                excess_l = round(float(litres) if litres > 0 else 0.1, 2)

            amount = round(excess_l * device.rate_per_l, 2)
            if amount <= 0.0:
                amount = 0.01

            new_bill = Bill(
                device_id=device_id,
                excess_l=excess_l,
                amount=amount,
                status="unpaid",
                ts=datetime.now(timezone.utc)
            )
            db.session.add(new_bill)
            db.session.flush()  # assign new_bill.id

            # Cut off relay
            publish_relay(device_id, "OFF")
            dev_state["relay"] = "OFF"
            dev_state["flow_lpm"] = 0.0

            action = "LIMIT_BREACHED_BILL_CREATED_RELAY_OFF"
            bill_created = new_bill.to_dict()
            logger.warning(
                f"[QUOTA BREACH] Device '{device_id}' exceeded {device.monthly_limit_l}L (Total: {total_l}L). "
                f"Generated Bill #{new_bill.id} for ₹{amount:.2f} ({excess_l}L excess). Relay turned OFF."
            )

    db.session.commit()

    return {
        "action": action,
        "device_id": device_id,
        "total_l": total_l,
        "limit_l": device.monthly_limit_l,
        "relay": dev_state["relay"],
        "flow_lpm": dev_state["flow_lpm"],
        "bill": bill_created,
    }

