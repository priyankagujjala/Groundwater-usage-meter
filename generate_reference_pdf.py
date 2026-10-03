import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw total page count and footer."""
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_decorations(self, page_count):
        if self._pageNumber == 1:
            return  # Suppress headers/footers on cover page

        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Running Header
        self.drawString(54, 750, "AquaPulse — Groundwater Usage Meter & Remote Quota Cutoff")
        self.drawRightString(558, 750, "Technical Reference Manual")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(54, 742, 558, 742)

        # Running Footer
        self.line(54, 45, 558, 45)
        self.drawString(54, 32, "CONFIDENTIAL & PROPRIETARY — ACADEMIC & ENGINEERING REFERENCE")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 32, page_str)
        self.restoreState()


def build_pdf(filename="AquaPulse_Groundwater_Usage_Meter_Technical_Reference.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Custom Color Palette
    PRIMARY = colors.HexColor("#0f172a")      # Slate 900
    SECONDARY = colors.HexColor("#0284c7")    # Sky 600
    ACCENT_CYAN = colors.HexColor("#06b6d4")  # Cyan 500
    SUCCESS_GREEN = colors.HexColor("#10b981")# Emerald 500
    DANGER_RED = colors.HexColor("#ef4444")   # Red 500
    BG_LIGHT = colors.HexColor("#f8fafc")     # Slate 50
    TEXT_MAIN = colors.HexColor("#334155")    # Slate 700
    BORDER_COLOR = colors.HexColor("#cbd5e1") # Slate 300

    # Custom Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=PRIMARY,
        spaceAfter=8
    )

    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=SECONDARY,
        spaceAfter=20
    )

    h1_style = ParagraphStyle(
        'Header1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=19,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Header2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=SECONDARY,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=TEXT_MAIN,
        spaceAfter=6
    )

    body_bold = ParagraphStyle(
        'BodyDarkBold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )

    bullet_style = ParagraphStyle(
        'BulletDark',
        parent=body_style,
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=3
    )

    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#0f172a")
    )

    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_MAIN
    )

    table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11.5,
        textColor=colors.white
    )

    story = []

    # =========================================================================
    # COVER / HEADER BANNER
    # =========================================================================
    story.append(Paragraph("AquaPulse: Smart Groundwater Metering & Quota Enforcement System", title_style))
    story.append(Paragraph("Comprehensive Technical Reference Manual | Hardware, IoT, Backend, Cloud & Frontend Architecture", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=SECONDARY, spaceAfter=14))

    # Meta Info Box
    meta_data = [
        [Paragraph("<b>Project Version:</b> 4.3 (Production Ready)", table_cell), Paragraph("<b>Target Hardware:</b> ESP32 NodeMCU + YF-S201 + 5V Relay", table_cell)],
        [Paragraph("<b>Cloud Stack:</b> EMQX Cloud, Render (Flask), Supabase/PostgreSQL", table_cell), Paragraph("<b>Payment Gateway:</b> Razorpay API (Mock/Live Ready)", table_cell)],
        [Paragraph("<b>Frontend:</b> SPA on Netlify (Paho MQTT WS + REST)", table_cell), Paragraph("<b>Security:</b> TLS 1.2/1.3, HMAC-SHA256, Environment Isolation", table_cell)]
    ]
    meta_table = Table(meta_data, colWidths=[240, 260])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BG_LIGHT),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    # =========================================================================
    # SECTION 1: EXECUTIVE SUMMARY & CORE CONCEPT
    # =========================================================================
    story.append(Paragraph("1. Executive Summary & Dual-Limit Concept", h1_style))
    story.append(Paragraph(
        "AquaPulse is an end-to-end IoT and cloud-integrated groundwater metering platform engineered to combat indiscriminate aquifer depletion through dynamic quota enforcement and fair usage tariff generation. The system establishes a progressive dual-threshold policy:",
        body_style
    ))
    story.append(Paragraph("• <b>Option 1: Free Litres Allowance (Green Zone):</b> Community or domestic baseline allocation (e.g., 0 to 40 L or 500 L). Within this tier, water is supplied at zero charge and the dashboard gauge displays a safe <b>Green</b> status.", bullet_style))
    story.append(Paragraph("• <b>Option 2: Monthly Water Usage Limit (Cutoff Threshold):</b> The absolute maximum permitted monthly extraction (e.g., 100 L or 1000 L). Once total pumped litres reach this threshold, the cloud backend automatically transmits a cutoff signal and the ESP32 cuts off the motor valve immediately.", bullet_style))
    story.append(Paragraph("• <b>Excess Usage Invoicing:</b> For every litre extracted between Option 1 and Option 2, the system automatically bills the consumer at a configurable tariff (e.g. ₹0.10 / Litre) and turns the gauge <b>Red</b>.", bullet_style))
    story.append(Paragraph("• <b>Automated Cycle Restoration:</b> When unpaid invoices are settled through the integrated Razorpay checkout or when the administrator issues a month reset, the cloud dispatches an activation command, re-opening the valve and restoring green status.", bullet_style))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 2: COMPLETE HARDWARE ARCHITECTURE & PINOUT
    # =========================================================================
    story.append(Paragraph("2. Complete Hardware Architecture & Wiring Specifications", h1_style))
    story.append(Paragraph(
        "The edge metering device is driven by an ESP32 microcontroller operating on 3.3V logic, interfacing directly with a 5V YF-S201 turbine flow sensor, a 5V relay module, a 5V/12V water pump, and a unified external 5V 2A power source.",
        body_style
    ))

    pin_table_data = [
        [Paragraph("Component", table_header), Paragraph("Pin / Terminal", table_header), Paragraph("ESP32 / System Connection", table_header), Paragraph("Signal / Voltage Level", table_header)],
        [Paragraph("<b>YF-S201 Flow Sensor</b>", table_cell), Paragraph("Red Wire (VCC)", table_cell), Paragraph("Breadboard 5V Rail (External Adapter +)", table_cell), Paragraph("5.0V DC Power", table_cell)],
        [Paragraph("", table_cell), Paragraph("Black Wire (GND)", table_cell), Paragraph("Common Breadboard Ground Rail", table_cell), Paragraph("0V Ground (Common)", table_cell)],
        [Paragraph("", table_cell), Paragraph("Yellow Wire (Signal)", table_cell), Paragraph("ESP32 <b>GPIO 18</b> (Interrupt Pin)", table_cell), Paragraph("Pulses (~450 pulses/Litre)", table_cell)],
        [Paragraph("<b>Relay Module (5V/3.3V)</b>", table_cell), Paragraph("VCC", table_cell), Paragraph("Breadboard 5V Rail", table_cell), Paragraph("5.0V DC Power", table_cell)],
        [Paragraph("", table_cell), Paragraph("GND", table_cell), Paragraph("Common Breadboard Ground Rail", table_cell), Paragraph("0V Ground (Common)", table_cell)],
        [Paragraph("", table_cell), Paragraph("IN (Control Signal)", table_cell), Paragraph("ESP32 <b>GPIO 23</b>", table_cell), Paragraph("Active-LOW (0V=ON, 3.3V=OFF)", table_cell)],
        [Paragraph("<b>Relay Output Terminals</b>", table_cell), Paragraph("COM (Common)", table_cell), Paragraph("5V / 12V Pump Power Supply (+)", table_cell), Paragraph("Switched High-Side Line", table_cell)],
        [Paragraph("", table_cell), Paragraph("NO (Normally Open)", table_cell), Paragraph("Pump Red Wire (+)", table_cell), Paragraph("Powered when Relay Energized", table_cell)],
        [Paragraph("<b>Pump / Motor</b>", table_cell), Paragraph("Black Wire (-)", table_cell), Paragraph("Pump Power Supply Ground (-)", table_cell), Paragraph("0V Power Return", table_cell)],
        [Paragraph("<b>ESP32 DevKit V1</b>", table_cell), Paragraph("VIN / 5V Pin", table_cell), Paragraph("Breadboard 5V Rail (from 5V Adapter)", table_cell), Paragraph("5.0V Input Power", table_cell)],
        [Paragraph("", table_cell), Paragraph("GND Pin", table_cell), Paragraph("Common Breadboard Ground Rail", table_cell), Paragraph("0V Common Ground Reference", table_cell)],
        [Paragraph("", table_cell), Paragraph("3.3V Pin", table_cell), Paragraph("ESP32 Internal Output (Unused/Sensor Safe)", table_cell), Paragraph("3.3V Regulated Output", table_cell)]
    ]

    pin_table = Table(pin_table_data, colWidths=[120, 100, 160, 120])
    pin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), SECONDARY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(pin_table)
    story.append(Spacer(1, 10))

    # Critical Circuit Note
    story.append(Paragraph("<b>CRITICAL HARDWARE RULE — COMMON GROUND:</b> All ground wires (5V Adapter (-), ESP32 GND, Flow Sensor Black GND, Relay GND, and Motor Power Return) MUST be tied together on a single common ground rail on the breadboard to establish a shared reference potential.", body_bold))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 3: COMMUNICATION & IoT PROTOCOLS
    # =========================================================================
    story.append(Paragraph("3. Communication Architecture & IoT Protocols", h1_style))
    story.append(Paragraph(
        "Communication is orchestrated via an enterprise-grade <b>EMQX Cloud Serverless MQTT Broker</b> utilizing two simultaneous transport layers:",
        body_style
    ))
    story.append(Paragraph("• <b>MQTTS over TLS (Port 8883):</b> Used by the physical ESP32 edge microcontroller with hardware encryption and automatic Last Will and Testament (LWT) for instant online/offline presence tracking.", bullet_style))
    story.append(Paragraph("• <b>MQTT over Secure WebSockets (WSS Port 8084):</b> Directly utilized by the client browser via the Paho MQTT JavaScript client. This achieves <b>&lt;30 millisecond physical relay actuation</b> directly from the user dashboard without waiting for backend HTTP polling.", bullet_style))

    comm_data = [
        [Paragraph("Topic Pattern", table_header), Paragraph("QoS", table_header), Paragraph("Direction", table_header), Paragraph("Payload Format & Purpose", table_header)],
        [Paragraph("<code>gw/{device}/usage</code>", code_style), Paragraph("0 / 1", table_cell), Paragraph("ESP32 &rarr; Cloud &rarr; Web", table_cell), Paragraph("<code>{\"device\":\"device1\",\"flow_lpm\":2.4,\"litres\":0.2,\"total\":42.5}</code><br/>Periodic telemetry published every 5 seconds.", table_cell)],
        [Paragraph("<code>gw/{device}/cmd</code>", code_style), Paragraph("1 (Ack)", table_cell), Paragraph("Cloud / Web &rarr; ESP32", table_cell), Paragraph("<code>{\"relay\":\"ON\"}</code> or <code>{\"relay\":\"OFF\",\"reset\":true}</code><br/>Valve control and counter reset dispatch.", table_cell)],
        [Paragraph("<code>gw/{device}/status</code>", code_style), Paragraph("1 (Retain)", table_cell), Paragraph("ESP32 &rarr; Cloud", table_cell), Paragraph("<code>online</code> / <code>offline</code> (LWT)<br/>Instant hardware heartbeat and disconnect detection.", table_cell)]
    ]
    comm_table = Table(comm_data, colWidths=[120, 45, 115, 220])
    comm_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(comm_table)
    story.append(Spacer(1, 12))

    # =========================================================================
    # SECTION 4: BACKEND & DATABASE SPECIFICATION
    # =========================================================================
    story.append(Paragraph("4. Backend Architecture, Database Schema & Ingestion Engine", h1_style))
    story.append(Paragraph(
        "The backend is built with <b>Python 3.12 and Flask</b>, deployed on Render with an isolated <code>ThreadedConnectionPool</code> to PostgreSQL (Supabase/Neon). It features automatic startup schema migrations (<code>_run_migrations()</code>) to guarantee seamless column additions across environments.",
        body_style
    ))

    db_data = [
        [Paragraph("Database Table", table_header), Paragraph("Primary & Foreign Keys", table_header), Paragraph("Key Attributes", table_header), Paragraph("Role in System", table_header)],
        [Paragraph("<b>devices</b>", table_cell), Paragraph("<code>id (PK)</code>", table_cell), Paragraph("<code>name (UNIQUE)</code><br/><code>free_limit_l (Float)</code><br/><code>monthly_limit_l (Float)</code><br/><code>rate_per_l (Float)</code>", table_cell), Paragraph("Stores provisioned hardware credentials, quota limits, and per-litre tariff pricing.", table_cell)],
        [Paragraph("<b>readings</b>", table_cell), Paragraph("<code>id (PK)</code><br/><code>device_id (FK)</code>", table_cell), Paragraph("<code>litres (Float)</code><br/><code>total_l (Float)</code><br/><code>ts (TIMESTAMPTZ)</code>", table_cell), Paragraph("Append-only time-series ledger of all water extraction samples.", table_cell)],
        [Paragraph("<b>bills</b>", table_cell), Paragraph("<code>id (PK)</code><br/><code>device_id (FK)</code>", table_cell), Paragraph("<code>excess_l (Float)</code><br/><code>amount (Float)</code><br/><code>status ('unpaid'|'paid')</code><br/><code>razorpay_order_id</code>", table_cell), Paragraph("Financial records generated automatically when usage exceeds the free limit.", table_cell)]
    ]
    db_table = Table(db_data, colWidths=[90, 90, 160, 160])
    db_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), SECONDARY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(db_table)
    story.append(Spacer(1, 12))

    # =========================================================================
    # SECTION 5: FRONTEND DASHBOARD & UI ENGINE
    # =========================================================================
    story.append(Paragraph("5. Frontend Dashboard Architecture & User Experience", h1_style))
    story.append(Paragraph(
        "The frontend is an ultra-modern Vanilla JavaScript Single Page Application (SPA) hosted on Netlify, featuring an advanced Glassmorphism design system, real-time Chart.js telemetry charts, and interactive policy configuration tools:",
        body_style
    ))
    story.append(Paragraph("• <b>Live Circular Gauge:</b> Custom SVG progress ring showing cumulative Litres Used with dual sub-labels (<code>Free: 40 L • Cutoff: 100 L</code>). Dynamically shifts from <b>Safe Emerald Green</b> during free extraction to <b>Vibrant Red</b> upon crossing the free allowance threshold.", bullet_style))
    story.append(Paragraph("• <b>Telemetry Monotonic Lock:</b> Eliminates UI jumping and sensor flicker by guaranteeing <code>totalUsageLitres</code> strictly advances and prevents polling latency from overriding live flow rates.", bullet_style))
    story.append(Paragraph("• <b>Role-Based Access Control (RBAC):</b> Integrated role switcher between <b>Admin</b> (full access to Edit Limits, Reset Monthly Litres, and Manual Valve Override) and <b>User</b> (view-only telemetry, personal bills list, and 1-click Razorpay payment).", bullet_style))
    story.append(Paragraph("• <b>Integrated Razorpay Modal:</b> Native checkout supporting UPI, Credit/Debit Cards, and Netbanking. On successful payment verification, the system clears outstanding dues and sends an instantaneous MQTT valve-open signal.", bullet_style))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 6: REST API SPECIFICATION
    # =========================================================================
    story.append(Paragraph("6. REST API Endpoint Reference", h1_style))
    
    api_data = [
        [Paragraph("Endpoint", table_header), Paragraph("Method", table_header), Paragraph("Request Body / Params", table_header), Paragraph("Response / Description", table_header)],
        [Paragraph("<code>/health</code>", code_style), Paragraph("GET", table_cell), Paragraph("None", table_cell), Paragraph("<code>{\"ok\": true}</code> — Liveness check for cloud monitoring.", table_cell)],
        [Paragraph("<code>/api/usage/&lt;dev&gt;</code>", code_style), Paragraph("GET", table_cell), Paragraph("None", table_cell), Paragraph("Returns cumulative total, free limit, monthly cutoff, flow rate, relay state, and online status.", table_cell)],
        [Paragraph("<code>/api/device/&lt;dev&gt;/quota</code>", code_style), Paragraph("POST", table_cell), Paragraph("<code>{\"free_limit_l\": 40, \"monthly_limit_l\": 100, \"rate_per_l\": 0.1}</code>", table_cell), Paragraph("Updates dual quota thresholds and tariff pricing in database.", table_cell)],
        [Paragraph("<code>/api/telemetry</code>", code_style), Paragraph("POST", table_cell), Paragraph("<code>{\"device\": \"device1\", \"flow_lpm\": 2.4, \"litres\": 0.2, \"total\": 45.0}</code>", table_cell), Paragraph("Direct HTTP telemetry ingestion for backup bill generation.", table_cell)],
        [Paragraph("<code>/api/relay/&lt;dev&gt;</code>", code_style), Paragraph("POST", table_cell), Paragraph("<code>{\"relay\": \"ON\" | \"OFF\"}</code>", table_cell), Paragraph("Dispatches QoS 1 relay toggle command via MQTT.", table_cell)],
        [Paragraph("<code>/api/bills/&lt;dev&gt;</code>", code_style), Paragraph("GET", table_cell), Paragraph("None", table_cell), Paragraph("Fetches all billing records, amounts, and payment statuses.", table_cell)],
        [Paragraph("<code>/api/payment/create-order</code>", code_style), Paragraph("POST", table_cell), Paragraph("<code>{\"bill_id\": 1}</code>", table_cell), Paragraph("Generates authenticated Razorpay Order ID for checkout.", table_cell)],
        [Paragraph("<code>/api/payment/verify</code>", code_style), Paragraph("POST", table_cell), Paragraph("<code>{\"bill_id\", \"razorpay_payment_id\", \"razorpay_signature\"}</code>", table_cell), Paragraph("Verifies HMAC-SHA256 signature and unlocks motor valve.", table_cell)],
        [Paragraph("<code>/api/device/&lt;dev&gt;/reset-litres</code>", code_style), Paragraph("POST", table_cell), Paragraph("None (Admin Only)", table_cell), Paragraph("Resets monthly usage to 0.0L and opens motor valve.", table_cell)]
    ]

    api_table = Table(api_data, colWidths=[125, 45, 160, 170])
    api_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('PADDING', (0, 0), (-1, -1), 3.5),
    ]))
    story.append(api_table)
    story.append(Spacer(1, 14))

    # =========================================================================
    # SECTION 7: TESTING, CALIBRATION & SAFETY
    # =========================================================================
    story.append(Paragraph("7. Sensor Calibration, Validation & Safety Precautions", h1_style))
    story.append(Paragraph("• <b>YF-S201 Calibration Formula:</b> <code>Flow Rate (L/min) = (Pulses / Elapsed_Seconds) / 7.5</code>. Each pulse represents approximately <code>2.22 millilitres</code> (450 pulses per 1.0 Litre).", body_style))
    story.append(Paragraph("• <b>Hardware Debounce:</b> An Interrupt Service Routine (ISR) lockout threshold of <code>2000 microseconds (2 ms)</code> is enforced in firmware to prevent false pulse triggering from pump electrical noise.", body_style))
    story.append(Paragraph("• <b>Automated Test Suite:</b> The system includes 36 automated Python unit and integration tests covering billing calculations, dual quota enforcement, MQTT publish acknowledgement timeouts, and payment verification.", body_style))
    story.append(Paragraph("• <b>Power Isolation:</b> In production environments, place a 1N4007 flyback diode across inductive motor terminals and use optocoupled relay modules to prevent back-EMF voltage spikes from resetting the ESP32.", body_style))

    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully generated: {filename}")

if __name__ == "__main__":
    output_path = "/Users/priyankagujjala/Desktop/Groundwater-usage-meter/AquaPulse_Groundwater_Usage_Meter_Technical_Reference.pdf"
    build_pdf(output_path)
