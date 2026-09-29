import logging

logger = logging.getLogger("mqtt_stub")

def publish_relay(device: str, state: str) -> bool:
    """
    Fallback MQTT relay publishing stub.
    Used when backend/mqtt_client.py is not yet available.
    """
    logger.info(f"[MQTT STUB] Command dispatched -> Topic: gw/{device}/cmd | Payload: {{'relay': '{state}'}}")
    return True
