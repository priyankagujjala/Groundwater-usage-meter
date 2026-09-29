"""
Lightweight Ingestion Test Suite
Groundwater Usage Meter with Pay-on-Excess

Automates verification of backend/mqtt_client.py:
- Check 1: Ingestion pipeline (MQTT -> validation -> DB persistence -> handler hook -> in-memory state).
- Check 4: Bad input resiliency (malformed JSON, missing required fields, invalid numeric types).
- Extra: Idempotent lifecycle (start_mqtt / stop_mqtt).

Requires no external test runner (pure Python 3, uses standard library + project dependencies).
Exit code 0 on PASS, 1 on FAIL.
"""

import json
import logging
import os
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path

import certifi
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import psycopg2

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [TEST] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("test_ingest")

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Load .env
for p in [Path.cwd(), Path.cwd().parent, backend_dir, backend_dir.parent]:
    env_file = p / ".env"
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file, override=False)
        break
else:
    load_dotenv()

import mqtt_client


def create_test_publisher() -> mqtt.Client:
    """
    Creates an independent MQTT publisher client to inject test messages.
    """
    host = os.getenv("MQTT_HOST") or os.getenv("MQTT_BROKER", "localhost")
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "").strip()
    use_tls = os.getenv("MQTT_USE_TLS", "true").lower() in ("true", "1", "yes")
    ca_cert = os.getenv("MQTT_CA_CERT", "").strip()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"test-pub-{uuid.uuid4().hex[:6]}"
    )
    if username:
        client.username_pw_set(username, password)
    if use_tls:
        if ca_cert and os.path.exists(ca_cert):
            client.tls_set(ca_certs=ca_cert, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        else:
            client.tls_set(ca_certs=certifi.where(), tls_version=ssl.PROTOCOL_TLS_CLIENT)

    client.connect(host, port, keepalive=30)
    client.loop_start()
    return client


def run_checks() -> int:
    logger.info("==================================================")
    logger.info("   INGESTION PIPELINE AUTOMATED VERIFICATION")
    logger.info("==================================================")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set in .env. Cannot run DB verification checks.")
        return 1

    # --- 1. Start MQTT ingestion module ---
    logger.info("Starting MQTT ingestion module...")
    mqtt_client.start_mqtt()
    # Test idempotency (calling twice should not raise or duplicate)
    mqtt_client.start_mqtt()
    time.sleep(2.0)  # Allow connection to settle

    test_pub = create_test_publisher()
    time.sleep(1.0)

    all_passed = True

    # ==============================================================================
    # CHECK 1: Valid Telemetry Ingestion -> DB + Handler + Memory State
    # ==============================================================================
    logger.info("--------------------------------------------------")
    logger.info("Running Check 1: Valid Telemetry Ingestion")
    logger.info("--------------------------------------------------")

    test_device = "device1"
    unique_total = round(100.0 + (time.time() % 1000), 3)
    test_flow = 2.45
    test_litres = 0.204

    handler_called = threading.Event()
    received_data = {}

    def _test_handler(dev, lit, tot, flow):
        if tot == unique_total:
            received_data["device"] = dev
            received_data["litres"] = lit
            received_data["total"] = tot
            received_data["flow"] = flow
            handler_called.set()

    mqtt_client.register_reading_handler(_test_handler)

    # Publish valid reading
    valid_payload = {
        "device": test_device,
        "flow_lpm": test_flow,
        "litres": test_litres,
        "total": unique_total
    }
    logger.info(f"Publishing valid telemetry to gw/{test_device}/usage: {valid_payload}")
    test_pub.publish(f"gw/{test_device}/usage", json.dumps(valid_payload), qos=1)

    # Wait for handler callback
    handler_ok = handler_called.wait(timeout=6.0)
    if not handler_ok:
        logger.error("FAIL: reading_handler callback was not invoked within 6 seconds.")
        all_passed = False
    else:
        logger.info(f"Handler callback triggered successfully with total={unique_total} L")

    # Verify Database row
    db_ok = False
    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, d.name, r.litres, r.total_l 
                FROM readings r
                JOIN devices d ON r.device_id = d.id
                WHERE d.name = %s AND r.total_l = %s
                ORDER BY r.id DESC LIMIT 1;
                """,
                (test_device, unique_total)
            )
            row = cur.fetchone()
            if row:
                logger.info(f"Database row confirmed: id={row[0]}, device={row[1]}, litres={row[2]}, total_l={row[3]}")
                db_ok = True
            else:
                logger.error(f"FAIL: Row with total_l={unique_total} not found in PostgreSQL readings table.")
        conn.close()
    except Exception as db_err:
        logger.error(f"Database verification query failed: {db_err}")

    if not db_ok:
        all_passed = False

    # Verify in-memory flow cache
    cached_flow = mqtt_client.get_last_flow_lpm(test_device)
    if cached_flow == test_flow:
        logger.info(f"In-memory cached flow rate verified: {cached_flow} L/min")
    else:
        logger.error(f"FAIL: get_last_flow_lpm returned {cached_flow}, expected {test_flow}")
        all_passed = False

    if handler_ok and db_ok and (cached_flow == test_flow):
        print("\n [PASS] Check 1: Ingestion pipeline end-to-end verified (MQTT -> DB -> Hook -> In-Memory).\n", flush=True)
    else:
        print("\n [FAIL] Check 1: Ingestion pipeline failed.\n", flush=True)

    # ==============================================================================
    # CHECK 4: Bad Input Resiliency (Malformed JSON, Missing Fields, Bad Types)
    # ==============================================================================
    logger.info("--------------------------------------------------")
    logger.info("Running Check 4: Bad Input Resiliency")
    logger.info("--------------------------------------------------")

    bad_input_passed = True

    # Count rows before bad payloads
    count_before = 0
    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM readings;")
            count_before = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        logger.error(f"Failed to query readings count: {e}")

    # 4a. Malformed JSON payload
    logger.info("Testing 4a: Publishing malformed JSON...")
    test_pub.publish(f"gw/{test_device}/usage", "{invalid-json-content!@#$%", qos=1)
    time.sleep(1.0)

    # 4b. Missing 'total' key
    logger.info("Testing 4b: Publishing payload missing 'total' field...")
    missing_total_payload = {"device": test_device, "flow_lpm": 2.4, "litres": 0.2}
    test_pub.publish(f"gw/{test_device}/usage", json.dumps(missing_total_payload), qos=1)
    time.sleep(1.0)

    # 4c. Non-numeric flow_lpm
    logger.info("Testing 4c: Publishing payload with invalid non-numeric flow...")
    bad_type_payload = {"device": test_device, "flow_lpm": "NOT_A_FLOAT", "litres": 0.2, "total": 999.0}
    test_pub.publish(f"gw/{test_device}/usage", json.dumps(bad_type_payload), qos=1)
    time.sleep(1.0)

    # Verify that none of the bad payloads were inserted into DB
    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM readings;")
            count_after = cur.fetchone()[0]
            if count_after == count_before:
                logger.info(f"Row count unchanged ({count_before} rows) - all bad payloads were dropped cleanly.")
            else:
                logger.error(f"FAIL: Row count increased from {count_before} to {count_after} (bad payload inserted!).")
                bad_input_passed = False
        conn.close()
    except Exception as e:
        logger.error(f"Failed to verify row count: {e}")
        bad_input_passed = False

    # Confirm backend client is still connected and alive after bad inputs
    try:
        status_payload = "online"
        test_pub.publish(f"gw/{test_device}/status", status_payload, qos=1)
        time.sleep(1.0)
        curr_status = mqtt_client.get_device_status(test_device)
        if curr_status == "online":
            logger.info("Ingestion listener remains alive and responsive after invalid input stream.")
        else:
            logger.error(f"FAIL: Ingestion listener status unexpected ({curr_status}).")
            bad_input_passed = False
    except Exception as e:
        logger.error(f"Failed status check: {e}")
        bad_input_passed = False

    if bad_input_passed:
        print("\n [PASS] Check 4: Bad inputs dropped safely without crashing listener or corrupting DB.\n", flush=True)
    else:
        print("\n [FAIL] Check 4: Bad input resiliency failed.\n", flush=True)
        all_passed = False

    # Clean shutdown
    test_pub.loop_stop()
    test_pub.disconnect()
    mqtt_client.stop_mqtt()

    logger.info("==================================================")
    if all_passed:
        logger.info("   FINAL RESULT: ALL INGESTION CHECKS PASSED!")
        logger.info("==================================================")
        return 0
    else:
        logger.error("   FINAL RESULT: SOME INGESTION CHECKS FAILED.")
        logger.info("==================================================")
        return 1


if __name__ == "__main__":
    exit_code = run_checks()
    sys.exit(exit_code)
