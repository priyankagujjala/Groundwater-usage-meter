"""
ESP32 Water-Meter Simulator
Groundwater Usage Meter with Pay-on-Excess

Emulates an ESP32 edge microcontroller equipped with a flow sensor and solenoid valve:
1. Publishes telemetry readings every 5 seconds (or configured interval) to gw/<device>/usage.
2. Subscribes to gw/<device>/cmd to handle remote relay 'ON' / 'OFF' commands.
3. Sets MQTT Last Will and Testament (LWT) on gw/<device>/status ('offline') and publishes 'online'.
4. Pauses water flow accumulation while relay is OFF while continuing heartbeat telemetry.
5. Supports --fast mode for demonstration and rapid quota breach testing.
"""

import argparse
import json
import logging
import os
import random
import signal
import ssl
import sys
import time
from pathlib import Path

import certifi
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# Configure structured logging with timestamps to stdout with immediate flushing
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [SIMULATOR] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("simulator")

# Load environment variables (.env from search paths)
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
        logger.info(f"Loaded environment variables from: {candidate}")
        break
else:
    load_dotenv()


class ESP32WaterMeterSimulator:
    def __init__(
        self,
        device_id: str = "device1",
        host: str = "localhost",
        port: int = 8883,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        ca_cert_path: str = "",
        interval_sec: float = 5.0,
        start_total: float = 0.0,
        base_flow_lpm: float = 2.4,
        fast_mode: bool = False
    ):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.ca_cert_path = ca_cert_path

        # Speed and flow parameters
        self.fast_mode = fast_mode
        self.interval_sec = 2.0 if fast_mode else max(0.5, interval_sec)
        self.flow_multiplier = 40.0 if fast_mode else 1.0
        self.nominal_flow_lpm = base_flow_lpm * self.flow_multiplier
        self.current_flow_lpm = self.nominal_flow_lpm
        self.total_litres = float(start_total)

        # Device operational state (starts ON as per contract)
        self.relay_state = "ON"
        self.is_connected = False
        self.is_running = False

        # MQTT Topics derived from device_id
        self.usage_topic = f"gw/{self.device_id}/usage"
        self.cmd_topic = f"gw/{self.device_id}/cmd"
        self.status_topic = f"gw/{self.device_id}/status"

        # Unique client_id (must differ from backend's client id)
        self.client_id = f"sim-{self.device_id}"

        self.client = self._create_mqtt_client()

    def _create_mqtt_client(self) -> mqtt.Client:
        # Use paho-mqtt v2 CallbackAPIVersion.VERSION2
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311
        )

        if self.username:
            client.username_pw_set(self.username, self.password)

        if self.use_tls:
            if self.ca_cert_path and os.path.exists(self.ca_cert_path):
                logger.info(f"Using custom CA certificate: {self.ca_cert_path}")
                client.tls_set(ca_certs=self.ca_cert_path, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            else:
                ca_bundle = certifi.where()
                client.tls_set(ca_certs=ca_bundle, tls_version=ssl.PROTOCOL_TLS_CLIENT)

        # Configure MQTT Last Will and Testament (LWT)
        # Broker publishes 'offline' on unclean disconnects
        client.will_set(self.status_topic, payload="offline", qos=1, retain=True)

        # Assign paho v2 callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # Automatic reconnect with exponential backoff (1s to 30s)
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc_val = getattr(reason_code, "value", reason_code)
        if rc_val == 0 or getattr(reason_code, "is_failure", False) is False:
            self.is_connected = True
            logger.info(f"Connected to EMQX Broker ({self.host}:{self.port}) as '{self.client_id}'")

            # Publish retained 'online' status on successful connect
            client.publish(self.status_topic, payload="online", qos=1, retain=True)
            logger.info(f"[STATUS] Published 'online' to {self.status_topic} (retain=True, QoS 1)")

            # Subscribe to command topic on every (re)connect
            client.subscribe(self.cmd_topic, qos=1)
            logger.info(f"[COMMAND] Subscribed to relay command topic: {self.cmd_topic} (QoS 1)")
        else:
            self.is_connected = False
            logger.error(f"MQTT connection rejected by broker: rc={rc_val} ({reason_code})")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self.is_connected = False
        rc_val = getattr(reason_code, "value", reason_code)
        if rc_val != 0:
            logger.warning(f"Connection lost (rc={rc_val}: {reason_code}). Automatic reconnect active...")
        else:
            logger.info("Cleanly disconnected from broker.")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8", errors="ignore").strip()

        if topic == self.cmd_topic:
            try:
                cmd_data = json.loads(payload_str)
                if not isinstance(cmd_data, dict):
                    logger.warning(f"Ignored non-object command payload on {topic}: {payload_str}")
                    return

                relay_val = cmd_data.get("relay")
                if not relay_val or not isinstance(relay_val, str):
                    logger.warning(f"Ignored invalid command without 'relay' key on {topic}: {payload_str}")
                    return

                new_state = relay_val.strip().upper()
                if new_state in ("ON", "OFF"):
                    old_state = self.relay_state
                    self.relay_state = new_state
                    if old_state != new_state:
                        if new_state == "OFF":
                            logger.info("Relay OFF - motor stopped")
                        else:
                            logger.info("Relay ON - motor resumed")
                    else:
                        logger.info(f"Relay state reaffirmed: {self.relay_state}")
                else:
                    logger.warning(f"Ignored unknown relay state '{relay_val}' (expected 'ON' or 'OFF'): {payload_str}")
            except json.JSONDecodeError:
                logger.warning(f"Ignored malformed JSON payload on {topic}: '{payload_str}'")
            except Exception as err:
                logger.error(f"Error handling message on {topic}: {err}", exc_info=True)

    def _compute_telemetry(self) -> tuple[float, float, float]:
        """
        Calculates flow_lpm, interval litres, and cumulative total based on relay state.
        When relay is ON: realistic random walk around nominal flow.
        When relay is OFF: flow is 0, litres is 0, total does not increase.
        """
        if self.relay_state == "ON":
            if self.fast_mode:
                # Fast mode: accelerated flow (~96 L/min) with +/- 5% jitter
                jitter = random.uniform(-0.05, 0.05)
                flow_lpm = max(50.0, self.nominal_flow_lpm * (1.0 + jitter))
            else:
                # Realistic random walk roughly 1.5 to 3.5 L/min around nominal 2.4 L/min
                step = random.uniform(-0.15, 0.15)
                self.current_flow_lpm = max(1.5, min(3.5, self.current_flow_lpm + step))
                flow_lpm = self.current_flow_lpm

            interval_litres = flow_lpm * (self.interval_sec / 60.0)
            self.total_litres += interval_litres
        else:
            flow_lpm = 0.0
            interval_litres = 0.0

        return round(flow_lpm, 2), round(interval_litres, 3), round(self.total_litres, 3)

    def start(self) -> None:
        logger.info("==================================================")
        logger.info("   GROUNDWATER METER - ESP32 DEVICE SIMULATOR")
        logger.info("==================================================")
        logger.info(f"Device ID      : {self.device_id}")
        logger.info(f"Client ID      : {self.client_id}")
        logger.info(f"Broker Host    : {self.host}:{self.port}")
        logger.info(f"TLS Enabled    : {self.use_tls}")
        logger.info(f"Interval       : {self.interval_sec}s")
        logger.info(f"Fast Mode      : {self.fast_mode} (x{self.flow_multiplier:.0f} flow multiplier)")
        logger.info(f"Initial Total  : {self.total_litres:.3f} L")
        logger.info(f"Initial Relay  : {self.relay_state}")
        logger.info(f"Usage Topic    : {self.usage_topic}")
        logger.info(f"Command Topic  : {self.cmd_topic}")
        logger.info(f"Status Topic   : {self.status_topic}")
        logger.info("==================================================")

        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except Exception as ex:
            logger.error(f"Initial connection to broker {self.host}:{self.port} failed: {ex}")
            logger.info("Background loop will continue retry attempts automatically.")
            try:
                self.client.loop_start()
            except Exception:
                pass

        self.is_running = True
        self._run_loop()

    def _run_loop(self) -> None:
        """
        Main simulation loop. Emits telemetry every interval.
        """
        while self.is_running:
            cycle_start = time.time()

            flow_lpm, litres, total = self._compute_telemetry()
            payload = {
                "device": self.device_id,
                "flow_lpm": flow_lpm,
                "litres": litres,
                "total": total
            }
            payload_str = json.dumps(payload)

            if self.is_connected:
                try:
                    self.client.publish(self.usage_topic, payload=payload_str, qos=1, retain=False)
                    logger.info(f"[PUBLISH] {self.usage_topic} -> {payload_str}")
                except Exception as pub_err:
                    logger.error(f"Failed to publish telemetry to {self.usage_topic}: {pub_err}")
            else:
                logger.warning(f"[OFFLINE] Broker disconnected - simulated: {payload_str}")

            elapsed = time.time() - cycle_start
            sleep_time = max(0.1, self.interval_sec - elapsed)
            time.sleep(sleep_time)

    def stop(self) -> None:
        """
        Performs clean shutdown:
        Publishes explicit 'offline' status before disconnecting.
        """
        if not self.is_running:
            return
        logger.info("Stopping simulator gracefully...")
        self.is_running = False

        if self.is_connected:
            try:
                logger.info(f"[STATUS] Publishing explicit 'offline' to {self.status_topic}...")
                pub = self.client.publish(self.status_topic, payload="offline", qos=1, retain=True)
                pub.wait_for_publish(timeout=2.0)
            except Exception as e:
                logger.warning(f"Error publishing clean offline status: {e}")

        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

        logger.info("Simulator stopped cleanly.")


def parse_args():
    parser = argparse.ArgumentParser(description="Groundwater Meter ESP32 Device Simulator")
    parser.add_argument(
        "--device",
        type=str,
        default=os.getenv("DEVICE_ID", "device1"),
        help="Device identifier (default: from DEVICE_ID or device1)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("SIM_INTERVAL_SEC", "5.0")),
        help="Telemetry publish interval in seconds (default: 5.0)"
    )
    parser.add_argument(
        "--start-total",
        type=float,
        default=float(os.getenv("SIM_INITIAL_TOTAL_L", "0.0")),
        help="Initial starting cumulative litres (default: 0.0)"
    )
    parser.add_argument(
        "--flow",
        type=float,
        default=float(os.getenv("SIM_BASE_FLOW_LPM", "2.4")),
        help="Base nominal flow rate in L/min when relay is ON (default: 2.4)"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Accelerate flow rate (x40) and publish every 2s to cross limit in 1-2 minutes"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    host = os.getenv("MQTT_HOST") or os.getenv("MQTT_BROKER", "localhost")
    port = int(os.getenv("MQTT_PORT", "8883"))
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "").strip()
    use_tls = os.getenv("MQTT_USE_TLS", "true").lower() in ("true", "1", "yes")
    ca_cert = os.getenv("MQTT_CA_CERT", "").strip()

    simulator = ESP32WaterMeterSimulator(
        device_id=args.device,
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        ca_cert_path=ca_cert,
        interval_sec=args.interval,
        start_total=args.start_total,
        base_flow_lpm=args.flow,
        fast_mode=args.fast
    )

    def handle_signal(sig, frame):
        logger.info(f"Received shutdown signal ({sig}). Shutting down...")
        simulator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    simulator.start()


if __name__ == "__main__":
    main()
