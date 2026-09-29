#!/usr/bin/env python3
"""
Developer Telemetry Feeder Script
--------------------------------
Simulates incoming flow sensor readings with rising totals to demonstrate:
1. Normal readings below quota (500 L)
2. Crossing the monthly limit threshold
3. Automatic bill generation (excess litres * rate)
4. Automatic relay valve cutoff ('OFF')

Usage:
    python dev_feed.py
    # or with custom interval/speed:
    python dev_feed.py --fast
"""

import sys
import time
import argparse
from app import create_app
from billing import process_reading, get_device_state
from models import Device, Bill, Reading

def run_dev_feed(device_id="device1", fast=False):
    app = create_app()

    delay = 0.5 if fast else 1.5

    print(f"\n🌊 Starting Dev Feeder for '{device_id}' (fast_mode={fast})...")
    print("=" * 65)

    with app.app_context():
        device = Device.query.filter_by(name=device_id).first()
        limit = device.monthly_limit_l if device else 500.0
        rate = device.rate_per_l if device else 0.10
        print(f"Device: {device_id} | Quota Limit: {limit} L | Tariff: Rs {rate:.2f}/L\n")

        # Telemetry progression steps: starting near limit and crossing it
        steps = [
            {"litres": 5.0, "total": 485.0, "flow": 2.4},
            {"litres": 5.0, "total": 490.0, "flow": 2.5},
            {"litres": 5.0, "total": 495.0, "flow": 2.4},
            {"litres": 4.0, "total": 499.0, "flow": 2.3},
            # LIMIT BREACH OCCURS HERE (500L exceeded -> 508.5L)
            {"litres": 9.5, "total": 508.5, "flow": 2.4},
            # Subsequent reading while bill unpaid
            {"litres": 0.0, "total": 508.5, "flow": 0.0},
        ]

        for idx, step in enumerate(steps, 1):
            res = process_reading(
                device_id=device_id,
                litres=step["litres"],
                total_l=step["total"],
                flow_lpm=step["flow"]
            )

            status_symbol = "✅" if res["action"] == "RECORDED" else "🚨"
            print(f"[{idx}/{len(steps)}] {status_symbol} Ingest: +{step['litres']:.1f}L | Total: {res['total_l']:.1f}/{res['limit_l']:.0f}L | Flow: {res['flow_lpm']:.1f} L/min | Relay: {res['relay']}")

            if res["bill"]:
                bill = res["bill"]
                print(f"   🧾 >> NEW BILL GENERATED: Bill #{bill['id']} for Rs {bill['amount']:.2f} ({bill['excess_l']:.1f}L excess) | Status: {bill['status']}")
                print(f"   ⚡ >> RELAY VALVE AUTOMATICALLY COMMANDED 'OFF'")

            time.sleep(delay)

        print("=" * 65)
        print("✅ Dev feed simulation completed successfully.\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Feed mock telemetry into billing engine.")
    parser.add_argument("--device", default="device1", help="Device ID to feed (default: device1)")
    parser.add_argument("--fast", action="store_true", help="Run with minimal delay")
    args = parser.parse_args()

    run_dev_feed(device_id=args.device, fast=args.fast)
