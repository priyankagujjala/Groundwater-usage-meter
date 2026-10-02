/**
 * Groundwater Usage Meter - Global Configuration
 * Member 3 (Frontend Dashboard)
 */
const CONFIG = {
  // Backend API Base URL (Render/Railway in production, or local Flask)
  API_BASE_URL: 'https://groundwater-usage-meter.onrender.com',

  // Mock mode: set to true to demo/test without a running backend
  USE_MOCK: false,

  // Target Device ID as defined in project contract
  DEVICE_ID: 'device1',

  // Polling interval in milliseconds (default: 5000ms = 5 seconds)
  POLL_INTERVAL_MS: 5000,

  // Fallback defaults if not supplied by API
  DEFAULT_MONTHLY_LIMIT_L: 500,
  DEFAULT_RATE_PER_L: 0.10, // Rs 0.10 per litre excess

  // Direct MQTT WebSockets Bridge (Enables instantaneous <30ms hardware switching directly from browser)
  MQTT_WS_ENABLED: true,
  MQTT_WS_HOST: 'e0615ec6.ala.asia-southeast1.emqxsl.com',
  MQTT_WS_PORT: 8084,
  MQTT_WS_PATH: '/mqtt',
  MQTT_WS_USER: 'simulator_user',
  MQTT_WS_PASS: 'Sicproject',
};

// Export to window object for browser access
window.CONFIG = CONFIG;

