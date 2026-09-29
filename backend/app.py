import sys
import os
import atexit
import logging
from flask import Flask
from flask_cors import CORS
try:
    from .config import Config
    from .models import db, Device
    from .routes import api_bp
    from .billing import process_reading
except ImportError:
    from config import Config
    from models import db, Device
    from routes import api_bp
    from billing import process_reading

try:
    from .mqtt_client import start_mqtt, stop_mqtt, register_reading_handler
except (ImportError, ModuleNotFoundError):
    try:
        from mqtt_client import start_mqtt, stop_mqtt, register_reading_handler
    except (ImportError, ModuleNotFoundError):
        start_mqtt = None
        stop_mqtt = None
        register_reading_handler = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app")

_mqtt_initialized = False

def _setup_mqtt(app: Flask):
    """Register MQTT telemetry reading handler and start background MQTT ingestion loop."""
    global _mqtt_initialized
    if start_mqtt is None or register_reading_handler is None:
        logger.debug("mqtt_client module not available. Skipping MQTT setup.")
        return

    def mqtt_reading_handler(device_name: str, litres: float, total_l: float, flow_lpm: float):
        """Callback executed when telemetry is ingested into database by mqtt_client."""
        try:
            with app.app_context():
                process_reading(
                    device_id=device_name,
                    litres=litres,
                    total_l=total_l,
                    flow_lpm=flow_lpm
                )
        except Exception as err:
            logger.error(f"Error processing MQTT reading handler: {err}", exc_info=True)

    register_reading_handler(mqtt_reading_handler)

    is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules or app.config.get("TESTING", False)
    if is_testing:
        logger.info("Running under pytest context. Skipping background MQTT client start.")
        try:
            from .mqtt_client import _relay_states
            _relay_states.clear()
        except Exception:
            try:
                from mqtt_client import _relay_states
                _relay_states.clear()
            except Exception:
                pass
        return

    if not _mqtt_initialized:
        logger.info("Initializing MQTT background client and subscriber thread...")
        start_mqtt()
        if stop_mqtt is not None:
            atexit.register(stop_mqtt)
        _mqtt_initialized = True


def create_app(config_class=Config):
    """Application factory for the Groundwater Meter API."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize Database
    db.init_app(app)

    # Configure CORS for Allowed Origins (including Netlify and local dashboard)
    allowed_origins = app.config.get("ALLOWED_ORIGINS", ["*"])
    CORS(app, resources={r"/api/*": {"origins": allowed_origins}, r"/health": {"origins": "*"}})

    # Register API Blueprint
    app.register_blueprint(api_bp)

    # Database Tables Creation & Initial Seeding
    with app.app_context():
        db.create_all()
        _seed_initial_data()

    # Initialize MQTT Ingestion & Reading Handler
    _setup_mqtt(app)

    return app


def _seed_initial_data():
    """Seed default device1 if not present in database."""
    device1 = db.session.get(Device, "device1")
    if not device1:
        logger.info("Seeding default device 'device1' with limit 500L and rate Rs 0.10/L.")
        device1 = Device(
            id="device1",
            name="Groundwater Pump 1",
            monthly_limit_l=500.0,
            rate_per_l=0.10,
        )
        db.session.add(device1)
        db.session.commit()


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

