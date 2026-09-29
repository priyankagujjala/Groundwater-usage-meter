# Groundwater Usage Meter with Pay-on-Excess

An end-to-end IoT and smart metering solution designed to monitor groundwater extraction, enforce monthly quota thresholds, trigger automatic valve shutoff upon limit breaches, and provide real-time consumption telemetry and billing.

---

## 1. Architecture Overview

```
Device Simulator (Python) --MQTT--> EMQX Broker --> Flask Backend --> PostgreSQL
        ^   (Relay Cmd)                 ^                 |
        +-------------------------------+                 |-- REST API --> Dashboard (Netlify)
                                                          +-- Razorpay (Test Mode)
```

- **Device Simulator / ESP32**: Emulates flow sensor telemetry (`flow_lpm`, `litres`, `total`) and listens for relay valve commands (`ON` / `OFF`) over MQTT.
- **EMQX Cloud Serverless**: Scalable MQTT broker routing telemetry and control payloads.
- **Flask Backend**: Ingests MQTT readings into PostgreSQL, maintains quota ledger, enforces cutoffs, and exposes REST endpoints.
- **PostgreSQL Database**: Persists devices, time-series flow readings, and billing invoices.
- **Dashboard (Frontend)**: Real-time static web app (HTML5 / Vanilla CSS / Modern JS) hosted on Netlify, polling live flow, quota gauge, valve state, historical charts, and excess bills.

---

## 2. Repository Structure

```
.
├── .gitignore              # Ignored files (Python, Node, Netlify, OS files)
├── README.md               # Project documentation and quickstart guide
├── project-plan.md         # Source of truth architecture & API contracts
├── simulator/              # (Member 1) Python MQTT device simulator
│   ├── simulator.py
│   └── requirements.txt
├── backend/                # (Member 1 & 2) Flask REST API & MQTT subscriber
│   ├── app.py
│   ├── mqtt_client.py
│   ├── schema.sql
│   └── requirements.txt
└── dashboard/              # (Member 3) Static Frontend Dashboard
    ├── index.html          # Semantic dashboard structure
    ├── style.css           # Modern design system (light/dark theme, responsive)
    ├── config.js           # Endpoint config & Mock mode toggle
    └── app.js              # State management, API layer, charts & mock engine
```

---

## 3. Getting Started & How to Run

### A. Dashboard (Frontend - `/dashboard`)

The dashboard is built with zero dependencies and no build step required.

1. **Serve locally using any static web server:**
   ```bash
   # Option 1: Python 3 built-in server
   cd dashboard
   python3 -m http.server 8080

   # Option 2: Node npx serve
   npx serve dashboard -l 8080
   ```
2. **Open your browser:** `http://localhost:8080`
3. **Configuration:**
   - Edit `dashboard/config.js` to toggle `USE_MOCK = true` (for standalone UI testing) or `USE_MOCK = false` to connect to the active Flask backend at `API_BASE_URL`.

---

### B. Device Simulator (`/simulator`)

Emulates an ESP32 water meter publishing every 5 seconds.

1. **Setup virtual environment:**
   ```bash
   cd simulator
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Run simulator:**
   ```bash
   python3 simulator.py
   # Or run in fast mode to rapidly trigger quota limits:
   python3 simulator.py --fast
   ```

---

### C. Flask Backend (`/backend`)

REST API and MQTT subscriber handling database persistence and billing logic.

1. **Setup virtual environment:**
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Configure environment:**
   Create a `.env` file based on `.env.example` with your EMQX and PostgreSQL credentials.
3. **Run backend:**
   ```bash
   flask run --port=5000
   ```

---

## 4. API & MQTT Contracts

### MQTT Topics

| Topic | Direction | Payload Example |
|---|---|---|
| `gw/device1/usage` | Simulator -> Cloud | `{"device":"device1","flow_lpm":2.4,"litres":0.2,"total":132.5}` |
| `gw/device1/cmd` | Cloud -> Simulator | `{"relay":"ON"}` or `{"relay":"OFF"}` |
| `gw/device1/status` | Simulator -> Cloud | `online` / `offline` (MQTT LWT) |

### REST Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/usage/<device>` | Get current total usage, monthly limit, live flow rate, and relay state |
| `GET` | `/api/readings/<device>` | Get time-series readings for history charts |
| `POST` | `/api/relay/<device>` | Manual relay toggle (`{"relay":"ON"\|"OFF"}`) |
| `GET` | `/api/bills/<device>` | List excess consumption billing records |
| `POST` | `/api/pay/create-order` | *(Final Phase)* Razorpay order generation |
| `POST` | `/api/pay/verify` | *(Final Phase)* Razorpay signature verification |

---

## 5. Deploy

### Dashboard (Netlify)

The dashboard is configured for zero-build static hosting via `netlify.toml`:

1. **Option A: Netlify Git Integration (Recommended)**
   - Log into [Netlify](https://app.netlify.com/).
   - Click **"Add new site"** > **"Import an existing project"** > select **GitHub**.
   - Choose `priyankagujjala/Groundwater-usage-meter`.
   - Set **Branch to deploy**: `main` (or active feature branch for testing).
   - Set **Publish directory**: `dashboard` (automatically detected from `netlify.toml`).
   - Leave **Build command** empty.
   - Click **Deploy Site**.

2. **Option B: Netlify CLI**
   ```bash
   npm i -g netlify-cli
   netlify login
   netlify deploy --prod --dir=dashboard
   ```

3. **Connecting Backend:**
   Once the Flask backend is deployed on Render/Railway, update `API_BASE_URL` in `dashboard/config.js` to your backend's HTTPS endpoint and set `USE_MOCK = false`.

### Backend & Database

- **Backend**: Deployed to Render or Railway with Python runtime and environment variables.
- **PostgreSQL**: Hosted on Neon or Supabase.
- **MQTT Broker**: Hosted on EMQX Serverless Cloud.

