import pytest
from backend.app import create_app
from backend.config import TestConfig
from backend.models import db, Device
from backend.billing import device_states

@pytest.fixture
def app():
    """Create test application context with isolated SQLite in-memory DB."""
    # Reset in-memory state
    device_states.clear()

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        # Seed test device1
        if not db.session.get(Device, "device1"):
            db.session.add(Device(id="device1", name="Test Pump 1", monthly_limit_l=500.0, rate_per_l=0.10))
            db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Test client for HTTP API requests."""
    return app.test_client()
