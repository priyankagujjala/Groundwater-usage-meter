/**
 * Groundwater Usage Meter - Global Configuration
 * Member 3 (Frontend Dashboard)
 */
const CONFIG = {
  // Backend API Base URL (Render/Railway in production, or local Flask)
  API_BASE_URL: 'http://localhost:5000',

  // Mock mode: set to false to connect to the live backend API
  USE_MOCK: true,

  // Target Device ID as defined in project contract
  DEVICE_ID: 'device1',

  // Polling interval in milliseconds (default: 5000ms = 5 seconds)
  POLL_INTERVAL_MS: 5000,

  // Fallback defaults if not supplied by API
  DEFAULT_MONTHLY_LIMIT_L: 500,
  DEFAULT_RATE_PER_L: 0.10, // Rs 0.10 per litre excess
};

// Export to window object for browser access
window.CONFIG = CONFIG;
