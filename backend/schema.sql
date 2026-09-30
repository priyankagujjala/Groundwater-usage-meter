-- ==============================================================================
-- Groundwater Usage Meter with Pay-on-Excess - Database Schema
-- PostgreSQL DDL for Neon / Supabase / Self-Hosted PostgreSQL
-- ==============================================================================

-- 1. Devices Table
CREATE TABLE IF NOT EXISTS devices (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    monthly_limit_l NUMERIC(12,2) NOT NULL DEFAULT 500,
    rate_per_l NUMERIC(8,4) NOT NULL DEFAULT 0.10
);

-- 2. Readings Table
CREATE TABLE IF NOT EXISTS readings (
    id BIGSERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id),
    litres NUMERIC(12,3) NOT NULL,
    total_l NUMERIC(14,3) NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Bills Table
CREATE TABLE IF NOT EXISTS bills (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id),
    excess_l NUMERIC(12,3) NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid', 'paid', 'pending')),
    razorpay_order_id TEXT,
    payment_id TEXT,
    ts TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Indexes
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings (device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_bills_device_ts ON bills (device_id, ts DESC);

-- 5. Seed Device
INSERT INTO devices (name, monthly_limit_l, rate_per_l)
VALUES ('device1', 500, 0.10)
ON CONFLICT (name) DO NOTHING;
