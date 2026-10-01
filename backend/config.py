import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))
load_dotenv()

class Config:
    """Backend Application Configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY") or "gw-meter-secret-key-dev"

    # Database: Use DATABASE_URL from environment (Neon/Supabase Postgres),
    # or fall back to local SQLite when DATABASE_URL is not set.
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        # Handle Postgres URI schemes compatibility
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = db_url
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///groundwater.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # MQTT Settings
    MQTT_HOST = os.getenv("MQTT_HOST") or "broker.emqx.io"
    MQTT_PORT = int(os.getenv("MQTT_PORT") or 1883)
    MQTT_USERNAME = os.getenv("MQTT_USERNAME") or ""
    MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or ""

    # CORS Settings
    raw_origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://localhost:8080,http://localhost:3000,http://127.0.0.1:8000,http://127.0.0.1:8080,https://*.netlify.app"
    )
    ALLOWED_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    # Razorpay (Reads from environment / backend/.env, with safe fallback)
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID") or "rzp_test_placeholder"
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET") or "rzp_secret_placeholder"


class TestConfig(Config):
    """Testing Configuration with in-memory database."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

