import os
import logging
from flask import Flask
from flask_cors import CORS
try:
    from .config import Config
    from .models import db, Device
    from .routes import api_bp
except ImportError:
    from config import Config
    from models import db, Device
    from routes import api_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app")

_global_app = None

def create_app(config_class=Config):
    """Application factory for the Groundwater Meter API."""
    global _global_app
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize Database
    db.init_app(app)

    # Configure CORS for Allowed Origins (including GitHub Pages, Netlify, and local dashboard)
    CORS(app, resources={r"/api/*": {"origins": "*"}, r"/health": {"origins": "*"}})

    # Register API Blueprint
    app.register_blueprint(api_bp)

    # Database Tables Creation & Initial Seeding
    with app.app_context():
        db.create_all()
        _seed_initial_data()

    # Start MQTT background ingestion loop once at startup (not twice under debug reloader)
    if not app.config.get("TESTING", False):
        if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            try:
                try:
                    from .mqtt_client import start_mqtt
                except ImportError:
                    from mqtt_client import start_mqtt
                start_mqtt()
            except Exception as mqtt_err:
                logger.warning(f"MQTT startup deferred: {mqtt_err}")

    _global_app = app
    return app


def _seed_initial_data():
    """Seed default device1 if not present in database."""
    device1 = Device.query.filter_by(name="device1").first()
    if not device1:
        logger.info("Seeding default device 'device1' with limit 500L and rate Rs 0.10/L.")
        device1 = Device(
            name="device1",
            monthly_limit_l=500.0,
            rate_per_l=0.10,
        )
        db.session.add(device1)
        db.session.commit()


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
