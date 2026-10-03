from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def utc_now():
    return datetime.now(timezone.utc)

class Device(db.Model):
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    free_limit_l = db.Column(db.Float, nullable=False, default=500.0)
    monthly_limit_l = db.Column(db.Float, nullable=False, default=1000.0)
    rate_per_l = db.Column(db.Float, nullable=False, default=0.10)

    # Relationships
    readings = db.relationship("Reading", backref="device", lazy=True, cascade="all, delete-orphan")
    bills = db.relationship("Bill", backref="device", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "free_limit_l": self.free_limit_l,
            "monthly_limit_l": self.monthly_limit_l,
            "rate_per_l": self.rate_per_l,
        }


class Reading(db.Model):
    __tablename__ = "readings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    litres = db.Column(db.Float, nullable=False, default=0.0)
    total_l = db.Column(db.Float, nullable=False, default=0.0)
    ts = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "device": self.device.name if self.device else None,
            "litres": round(self.litres, 2),
            "total": round(self.total_l, 2),
            "ts": self.ts.isoformat() if self.ts else None,
        }


class Bill(db.Model):
    __tablename__ = "bills"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    excess_l = db.Column(db.Float, nullable=False, default=0.0)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(32), nullable=False, default="unpaid") # "unpaid" | "paid" | "pending"
    razorpay_order_id = db.Column(db.String(128), nullable=True)
    payment_id = db.Column(db.String(128), nullable=True)
    ts = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "device": self.device.name if self.device else None,
            "excess_l": round(self.excess_l, 2),
            "amount": round(self.amount, 2),
            "status": self.status,
            "razorpay_order_id": self.razorpay_order_id,
            "payment_id": self.payment_id,
            "ts": self.ts.isoformat() if self.ts else None,
        }
