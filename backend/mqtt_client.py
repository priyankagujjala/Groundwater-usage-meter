"""
MQTT Telemetry Ingestion Module
Groundwater Usage Meter with Pay-on-Excess

Self-contained, framework-agnostic ingestion module designed for the Flask backend:
1. Subscribes to gw/+/usage and gw/+/status via EMQX Cloud Serverless.
2. Ingests usage telemetry records directly into PostgreSQL readings table.
3. Thread-safe in-memory cache for relay state, device status, and live flow rate.
4. Provides publish_relay() with QoS 1 and retain=True for remote motor/valve control.
5. Provides register_reading_handler() hook for limit checking and billing logic.
"""

import json
import logging
import os
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import certifi
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.pool import ThreadedConnectionPool

# Configure structured logging with stdout flushing
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MQTT_INGEST] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("mqtt_ingest")

# 1. Load environment variables (.env from search paths)
search_dirs = [
    Path.cwd(),
    Path.cwd().parent,
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parent.parent
]
for d in search_dirs:
    candidate = d / ".env"
    if candidate.is_file():
        load_dotenv(dotenv_path=candidate, override=False)
        break
else:
    load_dotenv()

# ==============================================================================
# Thread-Safe In-Memory State & Caches
# ==============================================================================
_state_lock = threading.Lock()
_relay_states: Dict[str, str] = {}           # device -> 'ON' | 'OFF' (default 'ON')
_device_statuses: Dict[str, str] = {}       # device -> 'online' | 'offline' | 'unknown'
_last_flows: Dict[str, Optional[float]] = {} # device -> flow_lpm
_known_devices_cache: set = set()          # set of valid device_id strings (e.g. 'device1')
_reading_handlers: List[Callable[[str, float, float, float], None]] = []

# Module singletons
_mqtt_client: Optional[mqtt.Client] = None
_db_pool: Optional[ThreadedConnectionPool] = None
_is_started = False
_lifecycle_lock = threading.RLock()


# ==============================================================================
# Database Connection Pool & Operations
# ==============================================================================
def _get_db_pool() -> Optional[ThreadedConnectionPool]:
    """
    Initializes and returns the singleton ThreadedConnectionPool.
    Thread-safe initialization using _lifecycle_lock.
    """
    global _db_pool
    if _db_pool is not None:
        return _db_pool

    with _lifecycle_lock:
        if _db_pool is not None:
            return _db_pool

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        logger.error("DATABASE_URL is not configured in environment. DB ingestion disabled.")
        return None

    try:
        connect_kwargs = {}
        if "sslmode=" not in database_url:
            connect_kwargs["sslmode"] = "require"

        _db_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=database_url,
            **connect_kwargs
        )
        logger.info("Database ThreadedConnectionPool initialized (1-10 connections, SSL active).")
        return _db_pool
    except Exception as err:
        logger.error(f"Failed to initialize Database pool: {err}", exc_info=True)
        return None


def _execute_db(operation: Callable[[psycopg2.extensions.connection], any]):
    """
    Executes a database callable with connection handling and 1 automatic retry on stale connections.
    """
    pool = _get_db_pool()
    if not pool:
        return None

    for attempt in range(2):
        conn = None
        try:
            conn = pool.getconn()
            result = operation(conn)
            conn.commit()
            pool.putconn(conn)
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as op_err:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                pool.putconn(conn, close=True)
                conn = None
            if attempt == 0:
                logger.warning(f"Transient DB connection error ({op_err}). Retrying once...")
                time.sleep(0.2)
                continue
            else:
                logger.error(f"DB operation failed after retry: {op_err}")
                return None
        except Exception as ex:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                pool.putconn(conn)
            logger.error(f"Database error executing query: {ex}", exc_info=True)
            return None


def _device_exists(device_id: str) -> bool:
    """
    Checks if device_id exists in the devices table. Caches valid device IDs in memory.
    """
    with _state_lock:
        if device_id in _known_devices_cache:
            return True

    def _query(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM devices WHERE id = %s;", (device_id,))
            return cur.fetchone() is not None

    exists = _execute_db(_query)
    if exists:
        with _state_lock:
            _known_devices_cache.add(device_id)
        return True
    return False


def _insert_reading_to_db(device_id: str, litres: float, total_l: float) -> bool:
    """
    Inserts an ingested telemetry record into PostgreSQL readings table.
    Uses VARCHAR(64) string device_id matching devices.id.
    """
    def _query(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO readings (device_id, litres, total_l, ts)
                VALUES (%s, %s, %s, now());
                """,
                (device_id, litres, total_l)
            )
            return True

    res = _execute_db(_query)
    return bool(res)


# ==============================================================================
# MQTT Callbacks & Ingestion Logic
# ==============================================================================
def _on_connect(client, userdata, flags, reason_code, properties=None):
    rc_val = getattr(reason_code, "value", reason_code)
    if rc_val == 0 or getattr(reason_code, "is_failure", False) is False:
        logger.info("Connected successfully to EMQX Broker.")

        # Subscribe with QoS 1 on every connect & reconnect
        usage_topic = "gw/+/usage"
        status_topic = "gw/+/status"
        client.subscribe(usage_topic, qos=1)
        client.subscribe(status_topic, qos=1)
        logger.info(f"Subscribed (QoS 1) to telemetry topics: '{usage_topic}' and '{status_topic}'")
    else:
        logger.error(f"Connection rejected by broker: rc={rc_val} ({reason_code})")


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    rc_val = getattr(reason_code, "value", reason_code)
    if rc_val != 0:
        logger.warning(f"Connection lost (rc={rc_val}: {reason_code}). Automatic reconnect active...")
    else:
        logger.info("Cleanly disconnected from MQTT broker.")


def _on_message(client, userdata, msg):
    topic = msg.topic
    payload_str = msg.payload.decode("utf-8", errors="ignore").strip()

    # 1. Device Status Topic: gw/<device>/status ('online' / 'offline')
    if topic.endswith("/status"):
        parts = topic.split("/")
        device_name = parts[1] if len(parts) >= 2 else "unknown"
        status_val = payload_str.lower()
        with _state_lock:
            _device_statuses[device_name] = status_val
        logger.info(f"[STATUS EVENT] Device '{device_name}' is {status_val}")
        return

    # 2. Device Usage Topic: gw/<device>/usage
    if topic.endswith("/usage"):
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.warning(f"Dropped non-JSON payload on {topic}: '{payload_str}'")
            return

        if not isinstance(data, dict):
            logger.warning(f"Dropped non-object payload on {topic}: {payload_str}")
            return

        # Defensive validation of required keys and numeric types
        required_keys = ("device", "flow_lpm", "litres", "total")
        if not all(k in data for k in required_keys):
            logger.warning(f"Dropped invalid payload on {topic} (missing keys {required_keys}): {payload_str}")
            return

        try:
            device_name = str(data["device"]).strip()
            flow_lpm = float(data["flow_lpm"])
            litres = float(data["litres"])
            total_l = float(data["total"])
        except (ValueError, TypeError) as val_err:
            logger.warning(f"Dropped payload on {topic} due to numeric conversion error ({val_err}): {payload_str}")
            return

        if not device_name:
            logger.warning(f"Dropped payload with empty device identifier on {topic}")
            return

        # Update in-memory live flow rate
        with _state_lock:
            _last_flows[device_name] = flow_lpm

        # Verify device exists in database devices table
        if not _device_exists(device_name):
            logger.warning(f"Device '{device_name}' not found in database 'devices' table. Dropping reading.")
            return

        # Persist reading to PostgreSQL using string device_name (VARCHAR(64))
        saved = _insert_reading_to_db(device_name, litres, total_l)
        if saved:
            logger.info(
                f"[INGESTED] Device: {device_name} | "
                f"Flow: {flow_lpm:.2f} L/min | Interval: {litres:.3f} L | Total: {total_l:.3f} L"
            )

            # Trigger limit checking and billing logic (if billing module present)
            try:
                try:
                    from .billing import process_reading
                except (ImportError, ModuleNotFoundError):
                    try:
                        from billing import process_reading
                    except (ImportError, ModuleNotFoundError):
                        process_reading = None
                if process_reading:
                    process_reading(device_name, litres, total_l, flow_lpm)
            except Exception as billing_err:
                logger.error(f"Error executing billing process_reading: {billing_err}", exc_info=True)

            # Invoke registered handlers (e.g. Flask billing/limit triggers)
            with _state_lock:
                handlers = list(_reading_handlers)

            for handler in handlers:
                try:
                    handler(device_name, litres, total_l, flow_lpm)
                except Exception as handler_err:
                    logger.error(f"Error in reading handler: {handler_err}", exc_info=True)


# ==============================================================================
# Public API Implementation
# ==============================================================================
def start_mqtt() -> None:
    """
    Initializes and starts the background MQTT client and database connection pool.
    Idempotent: safe to call multiple times without creating duplicate clients.
    """
    global _mqtt_client, _is_started

    with _lifecycle_lock:
        if _is_started:
            logger.debug("start_mqtt() called but ingestion is already active. No-op.")
            return

        # Initialize DB pool first
        _get_db_pool()

        host = os.getenv("MQTT_HOST") or os.getenv("MQTT_BROKER", "localhost")
        port = int(os.getenv("MQTT_PORT", "8883"))
        username = os.getenv("MQTT_BACKEND_USERNAME") or os.getenv("MQTT_USERNAME", "").strip()
        password = os.getenv("MQTT_BACKEND_PASSWORD") or os.getenv("MQTT_PASSWORD", "").strip()
        use_tls = os.getenv("MQTT_USE_TLS", "true").lower() in ("true", "1", "yes")
        ca_cert = os.getenv("MQTT_CA_CERT", "").strip()

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="backend-ingest",
            protocol=mqtt.MQTTv311
        )

        if username:
            client.username_pw_set(username, password)

        if use_tls:
            if ca_cert and os.path.exists(ca_cert):
                client.tls_set(ca_certs=ca_cert, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            else:
                client.tls_set(ca_certs=certifi.where(), tls_version=ssl.PROTOCOL_TLS_CLIENT)

        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_message = _on_message

        # Automatic reconnect with exponential backoff (1s to 30s)
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        logger.info(f"Connecting to MQTT broker at {host}:{port} as 'backend-ingest'...")
        try:
            client.connect(host, port, keepalive=60)
            client.loop_start()
            _mqtt_client = client
            _is_started = True
            logger.info("MQTT background ingestion loop started successfully.")
        except Exception as ex:
            logger.error(f"Failed to connect to MQTT broker {host}:{port}: {ex}", exc_info=True)
            try:
                client.loop_start()
                _mqtt_client = client
                _is_started = True
            except Exception:
                pass


def stop_mqtt() -> None:
    """
    Cleanly terminates the MQTT client background thread and closes the database connection pool.
    """
    global _mqtt_client, _db_pool, _is_started

    with _lifecycle_lock:
        if not _is_started:
            return

        logger.info("Stopping MQTT background listener...")
        if _mqtt_client is not None:
            try:
                _mqtt_client.loop_stop()
                _mqtt_client.disconnect()
            except Exception as ex:
                logger.warning(f"Error disconnecting MQTT client: {ex}")
            finally:
                _mqtt_client = None

        if _db_pool is not None:
            try:
                _db_pool.closeall()
                logger.info("Database connection pool closed.")
            except Exception as ex:
                logger.warning(f"Error closing DB pool: {ex}")
            finally:
                _db_pool = None

        _is_started = False
        logger.info("MQTT ingestion shutdown complete.")


def publish_relay(device: str, state: str) -> bool:
    """
    Publishes a relay control command to gw/<device>/cmd with QoS 1 and retain=True.
    Updates the in-memory relay state.

    Args:
        device (str): Device identifier (e.g. 'device1')
        state (str): Desired state ('ON' or 'OFF')

    Returns:
        bool: True if publish was accepted by the client loop, False otherwise. Never raises.
    """
    try:
        norm_state = str(state).strip().upper()
        if norm_state not in ("ON", "OFF"):
            logger.error(f"Invalid relay state '{state}'. Must be 'ON' or 'OFF'.")
            return False

        with _state_lock:
            _relay_states[device] = norm_state

        client = _mqtt_client
        if client is None:
            logger.warning(f"Cannot publish relay command: MQTT client is not running.")
            return False

        topic = f"gw/{device}/cmd"
        payload = json.dumps({"relay": norm_state})

        # retain=True ensures newly connecting / recovering simulators immediately adopt the commanded state
        msg_info = client.publish(topic, payload=payload, qos=1, retain=True)
        if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"[RELAY CMD] Successfully published {payload} to {topic} (retain=True)")
            return True
        else:
            logger.error(f"Publishing relay command to {topic} returned rc={msg_info.rc}")
            return False
    except Exception as ex:
        logger.error(f"Exception publishing relay command to device '{device}': {ex}", exc_info=True)
        return False


def get_relay_state(device: str) -> str:
    """
    Returns the last commanded relay state ('ON' or 'OFF') for the given device.
    Defaults to 'ON' if not yet commanded.
    """
    with _state_lock:
        return _relay_states.get(device, "ON")


def get_device_status(device: str) -> str:
    """
    Returns the online/offline status ('online', 'offline', or 'unknown') for the given device.
    """
    with _state_lock:
        return _device_statuses.get(device, "unknown")


def get_last_flow_lpm(device: str) -> Optional[float]:
    """
    Returns the most recent flow rate (in L/min) ingested from telemetry, or None if unobserved.
    """
    with _state_lock:
        return _last_flows.get(device, None)


def register_reading_handler(fn: Callable[[str, float, float, float], None]) -> None:
    """
    Registers a callback hook invoked immediately AFTER a telemetry reading is saved to PostgreSQL.

    Signature: fn(device_name: str, litres: float, total_l: float, flow_lpm: float) -> None
    """
    with _state_lock:
        if fn not in _reading_handlers:
            _reading_handlers.append(fn)
            logger.info(f"Registered reading handler: {fn.__name__ if hasattr(fn, '__name__') else fn}")


# ==============================================================================
# Standalone CLI Test Runner (__main__)
# ==============================================================================
if __name__ == "__main__":
    logger.info("==================================================")
    logger.info("   MQTT INGESTION MODULE - STANDALONE RUNNER")
    logger.info("==================================================")
    logger.info("Starting MQTT listener and printing incoming readings... Press Ctrl+C to exit.")

    # Register demonstration handler to print live readings
    def _print_reading(device: str, litres: float, total: float, flow: float):
        print(
            f" [LIVE HOOK] Device: {device} | Flow: {flow:.2f} L/min | "
            f"Interval: {litres:.3f} L | Cumulative: {total:.3f} L",
            flush=True
        )

    register_reading_handler(_print_reading)
    start_mqtt()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received. Shutting down standalone runner...")
        stop_mqtt()
        sys.exit(0)
