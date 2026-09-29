# Groundwater Usage Meter with Pay-on-Excess: 1-Day Build Plan (Software Only)

**Team:** 3 members | **Goal:** Frontend and backend complete today | **Razorpay is the LAST task**

**Stack:** MQTT, EMQX, Flask, PostgreSQL, Razorpay (test mode), HTML/CSS Dashboard, Netlify

**Hardware (ESP32, flow sensor, motor, relay) is skipped for now.** A Python **device simulator** stands in for the ESP32. It publishes fake flow data over MQTT and reacts to relay commands, so the whole pipeline can be built and tested today. Later, the real ESP32 only needs to publish the same MQTT messages, and nothing else changes.

---

## 1. Architecture

```
Device Simulator (Python) --MQTT--> EMQX broker --> Flask backend --> PostgreSQL
        ^   (relay cmd)                 ^                 |
        +-------------------------------+                 |-- REST API --> Dashboard (Netlify)
                                                          +-- Razorpay (test) order/verify
```

**Deployment note:** Netlify hosts static sites and serverless functions, so it cannot keep an MQTT subscriber running. Use it only for the **dashboard (static HTML/CSS/JS)**. The Flask backend with the MQTT subscriber runs on Render/Railway.

| Component | Where it runs |
|---|---|
| Dashboard | Netlify |
| Flask backend (with MQTT subscriber) | Render or Railway (free tier) |
| MQTT broker | EMQX Cloud Serverless (free) |
| PostgreSQL | Neon or Supabase (free) |
| Device simulator | Any team member's laptop |

---

## 2. Core Logic

- The simulator publishes litres used and flow rate every 5 seconds, as the ESP32 would.
- Each device/user has a **monthly limit** (e.g. 500 L). Usage above it is billed at a rate (e.g. Rs 0.10/L).
- Flask keeps the running total. When the total crosses the limit, it creates a **bill** and sends `relay OFF`.
- The user pays on the dashboard via Razorpay test mode. After payment, Flask sends `relay ON`.
- The simulator stops (or slows) its "flow" while the relay is OFF, and resumes when it is ON, which mimics the motor.

---

## 3. Contract (agree on this first, everyone codes against it)

### MQTT topics and payloads

| Topic | Direction | Payload |
|---|---|---|
| `gw/device1/usage` | simulator -> cloud | `{"device":"device1","flow_lpm":2.4,"litres":0.2,"total":132.5}` |
| `gw/device1/cmd` | cloud -> simulator | `{"relay":"ON"}` or `{"relay":"OFF"}` |
| `gw/device1/status` | simulator -> cloud | online/offline (MQTT Last Will) |

### Database tables

- `devices(id, name, monthly_limit_l, rate_per_l)`
- `readings(id, device_id, litres, total_l, ts)`
- `bills(id, device_id, excess_l, amount, status, razorpay_order_id, payment_id, ts)`

### Flask API

| Endpoint | Purpose |
|---|---|
| `GET /api/usage/<device>` | Current total, limit, flow rate, relay state |
| `GET /api/readings/<device>` | History for the chart |
| `POST /api/relay/<device>` | Manual ON/OFF, publishes to MQTT |
| `GET /api/bills/<device>` | List of bills |
| `POST /api/pay/create-order` | Razorpay create order (last task) |
| `POST /api/pay/verify` | Razorpay signature verify (last task) |

---

## 4. Work Split (equal load)

### Member 1: MQTT, Simulator and Database

1. Create the EMQX Cloud Serverless instance, add username/password auth, and verify with MQTTX. Share credentials privately, never in Git.
2. Set up Neon/Supabase Postgres and create the three tables (SQL script in `/backend/schema.sql`).
3. Write the **device simulator** (`/simulator/simulator.py`, paho-mqtt):
   - Publishes usage to `gw/device1/usage` every 5 s with a running total
   - Subscribes to `gw/device1/cmd` and pauses/resumes flow on relay OFF/ON
   - Sets a Last Will for offline status
   - Has a flag to speed up usage (e.g. `--fast`) so the limit is crossed quickly in a demo
4. Write the **MQTT ingestion module** for the backend (`/backend/mqtt_client.py`): a subscriber thread that saves readings to the DB, and a `publish_relay(device, state)` helper for Member 2 to call.
5. Later: help test the full flow and Razorpay.

### Member 2: Flask API and Billing Logic

1. Set up the Flask app with SQLAlchemy, config via `.env`, and CORS for the dashboard.
2. Build the REST endpoints listed above.
3. Write the **limit and billing logic**: on each new reading, compare the total to the limit, create a bill once, and call Member 1's `publish_relay(device, "OFF")`.
4. Deploy to Render/Railway with environment variables, and give Member 3 the public URL early.
5. **LAST TASK:** Razorpay server side: create-order, signature verify, mark bill paid, then relay ON.

### Member 3: Dashboard, Deployment and Git

1. Set up the GitHub repo, `.gitignore`, and branches, and manage merges.
2. Build the dashboard (HTML/CSS/JS) with mock data first, then switch to the real API:
   - Usage gauge (used vs limit) and live flow rate
   - History chart (Chart.js)
   - Relay status and manual toggle button
   - Bills table with status
3. Auto-refresh every few seconds (polling `/api/usage`).
4. Deploy to Netlify and set the backend's **https** URL as the API base URL in the dashboard:
   ```bash
   npm i -g netlify-cli
   netlify login
   netlify deploy --prod --dir=dashboard
   ```
   Or connect the GitHub repo in the Netlify UI (publish directory `dashboard`) so every push to `main` redeploys automatically.
5. **LAST TASK:** Razorpay Checkout on the frontend (`checkout.js`, "Pay now" button, verify call).

---

## 5. Timeline (adjust to your hours)

| Phase | Time | What |
|---|---|---|
| 0 | 0:00-0:30 | Agree on topics, payload, API contract, repo structure |
| 1 | 0:30-2:30 | Parallel work: EMQX + DB + simulator, Flask API skeleton, dashboard with mock data |
| 2 | 2:30-4:30 | Integrate: simulator -> EMQX -> Flask -> DB, then dashboard reads the real API |
| 3 | 4:30-6:00 | Limit and billing logic, relay control from dashboard, deploy backend and frontend |
| 4 | 6:00-7:30 | **Razorpay test mode**, end-to-end test, fixes, demo run |

---

## 6. Repo Structure

```
/simulator   (Member 1)
/backend     (Member 1 + Member 2: mqtt_client.py, schema.sql, then the Flask app)
/dashboard   (Member 3)
README.md
.gitignore
```

Member 1 owns `mqtt_client.py` and `schema.sql`. Member 2 owns the rest of `/backend`. Keep to your own files to avoid merge conflicts.

---

## 7. Git via CLI

One person creates the repo, then everyone does:

```bash
git clone <repo-url> && cd <repo>
git checkout -b feature/<simulator|backend|dashboard>

# work, then:
git add .
git commit -m "feat: short message"
git push -u origin feature/<name>
```

Merge via pull request, or for speed:

```bash
git checkout main && git pull
git merge feature/<name>
git push
```

**Rules:**
- Pull often: `git pull --rebase`
- Secrets go in `.env` (git-ignored), never in the repo. Commit a `.env.example` instead.

---

## 8. Tips for Working with Antigravity

- Give it the contract first (topics, payload JSON, API endpoints, table schema) and ask it to generate code against that.
- Ask for one small piece at a time, e.g. "Flask route that receives MQTT usage and updates total", and test before moving on.
- Ask it for `requirements.txt` and `.env.example`.
- Razorpay test: use test keys (`rzp_test_...`) and the test card details from the Razorpay docs.

---

## 9. Final Checklist

- [ ] Simulator publishes usage to EMQX
- [ ] Flask receives and stores readings
- [ ] Dashboard shows live usage, limit and history
- [ ] Crossing the limit creates a bill and sends relay OFF (simulator pauses)
- [ ] Manual relay toggle works from the dashboard
- [ ] Backend deployed (Render/Railway), dashboard deployed (Netlify)
- [ ] Razorpay test payment succeeds and relay turns back ON (simulator resumes)
- [ ] All code pushed to `main`

---

## 10. Adding Hardware Later

When the ESP32 is ready, it only needs to:
1. Connect to Wi-Fi and EMQX with TLS.
2. Publish the same `usage` payload to `gw/device1/usage`.
3. Subscribe to `gw/device1/cmd` and drive the relay.

The backend and dashboard need no changes.
