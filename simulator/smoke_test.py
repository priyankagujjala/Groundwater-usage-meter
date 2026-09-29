"""
MQTT Connectivity Smoke Test
Groundwater Usage Meter with Pay-on-Excess

Validates end-to-end MQTT connectivity to EMQX Cloud Serverless broker:
1. Loads credentials from .env (MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, etc.)
2. Connects using paho-mqtt v2.x with TLS (certifi or custom CA) and credentials.
3. Subscribes to topic 'gw/device1/usage'.
4. Publishes a test message to 'gw/device1/usage'.
5. Verifies receipt of its own published test message.
6. Cleanly disconnects and prints PASS or detailed diagnostic FAIL message.
"""

import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path

import certifi
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("smoke_test")

# 1. Load .env file (check current directory, parent directory, and workspace root)
search_dirs = [
    Path.cwd(),
    Path.cwd().parent,
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent.parent
]
env_loaded = False
for d in search_dirs:
    candidate = d / ".env"
    if candidate.is_file():
        load_dotenv(dotenv_path=candidate, override=False)
        logger.info(f"Loaded environment variables from: {candidate}")
        env_loaded = True
        break

if not env_loaded:
    load_dotenv()
    logger.info("Default load_dotenv() invoked (no explicit .env file found in parent paths).")


def run_smoke_test(timeout_sec: float = 12.0) -> int:
    """
    Executes the MQTT connectivity smoke test.
    Returns 0 on PASS, 1 on FAIL.
    """
    logger.info("==================================================")
    logger.info("   GROUNDWATER METER - MQTT CONNECTIVITY SMOKE TEST")
    logger.info("==================================================")

    # 2. Read environment configuration
    mqtt_host = os.getenv("MQTT_HOST") or os.getenv("MQTT_BROKER")
    mqtt_port_raw = os.getenv("MQTT_PORT", "8883")
    try:
        mqtt_port = int(mqtt_port_raw)
    except ValueError:
        logger.error(f"FAIL [Config]: Invalid MQTT_PORT '{mqtt_port_raw}' in environment. Must be an integer.")
        return 1

    mqtt_username = os.getenv("MQTT_USERNAME", "").strip()
    mqtt_password = os.getenv("MQTT_PASSWORD", "").strip()
    mqtt_ca_cert = os.getenv("MQTT_CA_CERT", "").strip()
    mqtt_use_tls_raw = os.getenv("MQTT_USE_TLS", "true").strip().lower()
    mqtt_use_tls = mqtt_use_tls_raw in ("true", "1", "yes")

    # Validate essential configuration
    if not mqtt_host or mqtt_host.strip() in ("", "your-emqx-deployment.emqx.cloud"):
        logger.error(
            "FAIL [Config]: MQTT_HOST (or MQTT_BROKER) is not set or has placeholder value.\n"
            "  Please set MQTT_HOST in your .env file with your EMQX Serverless broker hostname."
        )
        return 1

    logger.info(f"Target Broker : {mqtt_host}:{mqtt_port}")
    logger.info(f"TLS Enabled   : {mqtt_use_tls}")
    logger.info(f"Username      : {mqtt_username if mqtt_username else '(none)'}")
    logger.info(f"Password      : {'********' if mqtt_password else '(none)'}")

    test_topic = "gw/device1/usage"
    test_nonce = f"smoke_{uuid.uuid4().hex[:8]}"
    test_payload = {
        "device": "device1",
        "flow_lpm": 0.0,
        "litres": 0.0,
        "total": 0.0,
        "test": "connectivity_smoke_test",
        "nonce": test_nonce,
        "timestamp": time.time()
    }

    # Synchronization primitives
    connect_event = threading.Event()
    subscribe_event = threading.Event()
    message_event = threading.Event()
    state = {
        "connected": False,
        "error_reason": None,
        "received_payload": None
    }

    # 3. Create paho-mqtt client using CallbackAPIVersion.VERSION2
    client_id = f"smoke_tester_{uuid.uuid4().hex[:6]}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311
    )

    if mqtt_username:
        client.username_pw_set(mqtt_username, mqtt_password)

    # 4. Configure TLS
    if mqtt_use_tls:
        try:
            if mqtt_ca_cert and os.path.exists(mqtt_ca_cert):
                logger.info(f"Using custom CA certificate: {mqtt_ca_cert}")
                client.tls_set(ca_certs=mqtt_ca_cert, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            else:
                ca_bundle = certifi.where()
                logger.info(f"Using certifi CA bundle: {ca_bundle}")
                client.tls_set(ca_certs=ca_bundle, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        except Exception as tls_err:
            logger.error(f"FAIL [TLS Config]: Error configuring TLS: {tls_err}")
            return 1

    # 5. Define paho-mqtt v2 callbacks
    def on_connect(c, userdata, flags, reason_code, properties):
        rc_val = getattr(reason_code, 'value', reason_code)
        if reason_code.is_failure or rc_val != 0:
            if rc_val in (4, 5, 134, 135) or any(k in str(reason_code).lower() for k in ("authoriz", "password", "credential")):
                state["error_reason"] = (
                    f"Bad credentials (rc {rc_val}: {reason_code}). "
                    f"Authentication was rejected for username '{mqtt_username}'. "
                    f"Verify MQTT_USERNAME and MQTT_PASSWORD in your .env."
                )
            elif rc_val in (3, 136, 137):
                state["error_reason"] = f"Broker unavailable or busy (rc {rc_val}: {reason_code})."
            else:
                state["error_reason"] = f"Broker rejected connection with code {rc_val} ({reason_code})."
            logger.error(f"Connection rejected: {state['error_reason']}")
            connect_event.set()
            return

        state["connected"] = True
        logger.info("Connected successfully to EMQX broker.")
        logger.info(f"Subscribing to topic: {test_topic}...")
        c.subscribe(test_topic, qos=1)
        connect_event.set()

    def on_subscribe(c, userdata, mid, reason_codes, properties):
        failed = any(getattr(rc, "is_failure", False) or getattr(rc, "value", rc if isinstance(rc, int) else 0) >= 128 for rc in reason_codes)
        if failed:
            state["error_reason"] = f"Subscription denied by broker for topic '{test_topic}'. Reason codes: {reason_codes}"
            logger.error(state["error_reason"])
        else:
            logger.info(f"Subscription confirmed for topic: {test_topic}")
            subscribe_event.set()

    def on_message(c, userdata, msg):
        try:
            raw_data = msg.payload.decode("utf-8", errors="ignore")
            parsed = json.loads(raw_data)
            if parsed.get("nonce") == test_nonce:
                logger.info(f"Received self-published test message on {msg.topic}!")
                state["received_payload"] = parsed
                message_event.set()
        except Exception:
            # Ignore messages from other devices/simulators on the same topic
            pass

    def on_disconnect(c, userdata, disconnect_flags, reason_code, properties):
        rc_val = getattr(reason_code, 'value', reason_code)
        if rc_val != 0 and not state["connected"]:
            if not state["error_reason"]:
                state["error_reason"] = f"Broker disconnected unexpectedly during handshake (rc={rc_val}: {reason_code})."

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    # 6. Attempt connection with targeted error diagnostics
    logger.info(f"Connecting to {mqtt_host}:{mqtt_port}...")
    try:
        client.connect(mqtt_host, mqtt_port, keepalive=30)
    except socket.gaierror as dns_err:
        logger.error(
            f"FAIL [DNS / Host Resolution]: Host '{mqtt_host}' could not be resolved ({dns_err}).\n"
            "  Please verify MQTT_HOST in your .env file."
        )
        return 1
    except (ssl.SSLCertVerificationError, ssl.SSLEOFError) as ssl_cert_err:
        logger.error(
            f"FAIL [TLS Handshake]: SSL/TLS certificate verification failed ({ssl_cert_err}).\n"
            "  Ensure system clock is accurate, or check MQTT_CA_CERT."
        )
        return 1
    except ssl.SSLError as ssl_err:
        logger.error(
            f"FAIL [TLS Handshake]: SSL/TLS handshake failed ({ssl_err}).\n"
            f"  Ensure port {mqtt_port} is configured for TLS on EMQX and MQTT_USE_TLS is true."
        )
        return 1
    except ConnectionRefusedError as conn_refused:
        logger.error(
            f"FAIL [Connection Refused]: Could not establish connection to {mqtt_host}:{mqtt_port} ({conn_refused}).\n"
            f"  Check that the broker is running and port {mqtt_port} is accessible."
        )
        return 1
    except (socket.timeout, TimeoutError) as to_err:
        logger.error(
            f"FAIL [Connection Timeout]: Connection attempt to {mqtt_host}:{mqtt_port} timed out ({to_err}).\n"
            "  Check network connection and firewall / port 8883 accessibility."
        )
        return 1
    except Exception as ex:
        logger.error(f"FAIL [Connection Exception]: Unexpected error connecting to {mqtt_host}:{mqtt_port}: {ex}")
        return 1

    # Start network loop in background
    client.loop_start()

    try:
        # Wait for on_connect callback
        if not connect_event.wait(timeout=timeout_sec):
            logger.error(
                f"FAIL [Timeout]: Broker connection did not complete within {timeout_sec}s.\n"
                f"  Possible TLS negotiation hang or unresponsive host at {mqtt_host}:{mqtt_port}."
            )
            return 1

        if not state["connected"] or state["error_reason"]:
            logger.error(f"FAIL [Authentication / Broker]: {state['error_reason']}")
            return 1

        # Wait for on_subscribe confirmation
        if not subscribe_event.wait(timeout=timeout_sec):
            err = state["error_reason"] or f"Subscription to '{test_topic}' timed out after {timeout_sec}s."
            logger.error(f"FAIL [Subscription]: {err}")
            return 1

        # Publish test message
        logger.info(f"Publishing test message with nonce '{test_nonce}' to {test_topic}...")
        publish_info = client.publish(test_topic, json.dumps(test_payload), qos=1)
        publish_info.wait_for_publish(timeout=5.0)

        # Wait to receive our own message
        logger.info("Waiting for self-message echo...")
        if not message_event.wait(timeout=timeout_sec):
            logger.error(
                f"FAIL [Message Echo Timeout]: Test message was sent to '{test_topic}' but not received within {timeout_sec}s.\n"
                "  Check ACL rules or permissions for publish/subscribe on this broker account."
            )
            return 1

        # Success!
        logger.info("==================================================")
        logger.info("   RESULT: PASS")
        logger.info("==================================================")
        logger.info("Successfully verified MQTT connection, TLS, authentication, publish, and subscribe.")
        return 0

    finally:
        # Clean shutdown
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    exit_code = run_smoke_test()
    sys.exit(exit_code)
