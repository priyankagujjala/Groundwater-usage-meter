/**
 * Groundwater Usage Meter - Dashboard Application Logic
 * Author: Member 3 (Frontend & Netlify Deployment)
 * 
 * Architecture:
 * 1. State Management & Configuration
 * 2. Mock Simulation Layer (for standalone demo / testing)
 * 3. Isolated REST API Client (strictly against project API contract)
 * 4. Chart.js Visualization Engine
 * 5. UI Renderers (Gauge, Flow Rate, Relay State, Invoices, Alerts)
 * 6. Event Listeners & Polling Engine
 */

(function () {
  'use strict';

  /* ==========================================================================
     1. State Management & Configuration
     ========================================================================== */
  const config = window.CONFIG || {
    API_BASE_URL: 'http://localhost:5000',
    USE_MOCK: true,
    DEVICE_ID: 'device1',
    POLL_INTERVAL_MS: 5000,
    DEFAULT_MONTHLY_LIMIT_L: 500,
    DEFAULT_RATE_PER_L: 0.10,
  };

  const state = {
    deviceId: config.DEVICE_ID,
    totalUsageLitres: 460.0, // starts near threshold for realistic demo progression
    monthlyLimitLitres: config.DEFAULT_MONTHLY_LIMIT_L,
    flowRateLpm: 2.4,
    relayState: 'ON', // 'ON' | 'OFF'
    deviceStatus: 'online', // 'online' | 'offline'
    ratePerLitre: config.DEFAULT_RATE_PER_L,
    readingsHistory: [],
    bills: [],
    pollTimer: null,
    isPolling: false,
    chartInstance: null,
    theme: localStorage.getItem('aquapulse_theme') || 'dark',
    mockNetworkFail: false,
  };

  /* ==========================================================================
     2. Mock Simulation Layer (Realistic Data Generator)
     ========================================================================== */
  const MockEngine = {
    init() {
      // Seed initial 10 readings for chart
      const now = Date.now();
      state.readingsHistory = [];
      let runningTotal = state.totalUsageLitres - 15.0;

      for (let i = 9; i >= 0; i--) {
        const time = new Date(now - i * 60000);
        const flow = state.relayState === 'ON' ? +(2.0 + Math.random() * 0.8).toFixed(1) : 0.0;
        runningTotal += +(flow * 0.08).toFixed(1);
        state.readingsHistory.push({
          id: 10 - i,
          device_id: state.deviceId,
          flow_lpm: flow,
          litres: +(flow * 0.08).toFixed(2),
          total_l: +runningTotal.toFixed(1),
          ts: time.toISOString(),
        });
      }
    },

    stepSimulation() {
      if (state.mockNetworkFail) {
        throw new Error('Simulated network connection failure');
      }

      // If relay is ON and device is online, increment flow and total
      if (state.relayState === 'ON' && state.deviceStatus === 'online') {
        const currentFlow = +(2.2 + (Math.random() * 0.6 - 0.3)).toFixed(1);
        state.flowRateLpm = currentFlow;

        // ~0.2 L accumulated in 5 seconds
        const deltaL = +(currentFlow * (config.POLL_INTERVAL_MS / 60000)).toFixed(2);
        state.totalUsageLitres = +(state.totalUsageLitres + deltaL).toFixed(2);

        // Record reading
        state.readingsHistory.push({
          id: state.readingsHistory.length + 1,
          device_id: state.deviceId,
          flow_lpm: currentFlow,
          litres: deltaL,
          total_l: state.totalUsageLitres,
          ts: new Date().toISOString(),
        });

        // Keep last 30 readings
        if (state.readingsHistory.length > 30) {
          state.readingsHistory.shift();
        }

        // Check limit threshold breach (> 500 L)
        if (state.totalUsageLitres >= state.monthlyLimitLitres) {
          const excess = +(state.totalUsageLitres - state.monthlyLimitLitres).toFixed(2);
          const amount = +(excess * state.ratePerLitre).toFixed(2);

          // Check if unpaid bill already exists
          const existingUnpaid = state.bills.find(b => b.status.toLowerCase() === 'unpaid');
          if (!existingUnpaid) {
            state.bills.unshift({
              id: `INV-${Date.now().toString().slice(-6)}`,
              device_id: state.deviceId,
              excess_l: excess > 0 ? excess : 0.5,
              amount: amount > 0 ? amount : 0.05,
              status: 'unpaid',
              ts: new Date().toISOString(),
            });
          }

          // Automatic valve cutoff
          state.relayState = 'OFF';
          state.flowRateLpm = 0.0;
        }
      } else {
        // Relay is OFF
        state.flowRateLpm = 0.0;
      }
    },

    getUsage() {
      this.stepSimulation();
      return {
        device: state.deviceId,
        total: state.totalUsageLitres,
        total_l: state.totalUsageLitres,
        limit: state.monthlyLimitLitres,
        monthly_limit_l: state.monthlyLimitLitres,
        flow_lpm: state.flowRateLpm,
        flow_rate: state.flowRateLpm,
        relay: state.relayState,
        relay_state: state.relayState,
        status: state.deviceStatus,
        rate_per_l: state.ratePerLitre,
      };
    },

    getReadings() {
      return [...state.readingsHistory];
    },

    getBills() {
      return [...state.bills];
    },

    setRelay(desiredState) {
      if (state.mockNetworkFail) {
        throw new Error('Simulated network connection failure');
      }
      state.relayState = desiredState;
      if (desiredState === 'OFF') {
        state.flowRateLpm = 0.0;
      } else {
        state.flowRateLpm = 2.4;
      }
      return { success: true, relay: state.relayState };
    }
  };

  /* ==========================================================================
     3. Isolated REST API Client
     ========================================================================== */
  const ApiClient = {
    /**
     * Standardized fetch wrapper with timeout
     */
    async request(endpoint, options = {}) {
      const url = `${config.API_BASE_URL.replace(/\/$/, '')}${endpoint}`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 4000);

      try {
        const response = await fetch(url, {
          ...options,
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            ...(options.headers || {}),
          },
        });
        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`HTTP Error ${response.status}: ${response.statusText}`);
        }
        return await response.json();
      } catch (err) {
        clearTimeout(timeoutId);
        throw err;
      }
    },

    /**
     * GET /api/usage/<device>
     * Returns: current total, limit, flow rate, relay state
     */
    async getUsage(deviceId) {
      if (config.USE_MOCK) {
        return MockEngine.getUsage();
      }
      return await this.request(`/api/usage/${encodeURIComponent(deviceId)}`);
    },

    /**
     * GET /api/readings/<device>
     * Returns: history array for chart
     */
    async getReadings(deviceId) {
      if (config.USE_MOCK) {
        return MockEngine.getReadings();
      }
      return await this.request(`/api/readings/${encodeURIComponent(deviceId)}`);
    },

    /**
     * POST /api/relay/<device> with body {"relay":"ON"|"OFF"}
     * Returns: updated relay status
     */
    async setRelay(deviceId, relayState) {
      if (config.USE_MOCK) {
        return MockEngine.setRelay(relayState);
      }
      return await this.request(`/api/relay/${encodeURIComponent(deviceId)}`, {
        method: 'POST',
        body: JSON.stringify({ relay: relayState }),
      });
    },

    /**
     * GET /api/bills/<device>
     * Returns: list of bills
     */
    async getBills(deviceId) {
      if (config.USE_MOCK) {
        return MockEngine.getBills();
      }
      return await this.request(`/api/bills/${encodeURIComponent(deviceId)}`);
    },
  };

  /**
   * Field normalization adapter to safely handle minor naming variations from backend
   */
  function normalizeUsageData(raw) {
    return {
      total: raw.total ?? raw.total_l ?? raw.litres_total ?? state.totalUsageLitres,
      limit: raw.limit ?? raw.monthly_limit_l ?? raw.limit_l ?? config.DEFAULT_MONTHLY_LIMIT_L,
      flow_lpm: raw.flow_lpm ?? raw.flow_rate ?? raw.flowRate ?? 0.0,
      relay: (raw.relay ?? raw.relay_state ?? raw.relay_status ?? 'ON').toUpperCase(),
      status: raw.status ?? raw.device_status ?? 'online',
      rate_per_l: raw.rate_per_l ?? raw.rate ?? config.DEFAULT_RATE_PER_L,
    };
  }

  /* ==========================================================================
     4. Chart.js Visualization Engine
     ========================================================================== */
  const ChartEngine = {
    init() {
      const ctx = document.getElementById('usageHistoryChart');
      if (!ctx) return;

      const isDark = state.theme === 'dark';
      const gridColor = isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)';
      const textColor = isDark ? '#94a3b8' : '#64748b';

      this.chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            {
              label: 'Flow Rate (L/min)',
              data: [],
              borderColor: '#06b6d4',
              backgroundColor: isDark ? 'rgba(6, 182, 212, 0.15)' : 'rgba(6, 182, 212, 0.2)',
              borderWidth: 2.5,
              tension: 0.35,
              fill: true,
              pointRadius: 3,
              pointBackgroundColor: '#06b6d4',
              pointHoverRadius: 6,
            },
            {
              label: 'Total Usage (L)',
              data: [],
              borderColor: '#3b82f6',
              backgroundColor: 'transparent',
              borderWidth: 2,
              borderDash: [5, 5],
              tension: 0.2,
              yAxisID: 'y1',
              pointRadius: 0,
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 400 },
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              position: 'top',
              labels: {
                color: textColor,
                font: { family: 'Inter', size: 12, weight: '500' },
                usePointStyle: true,
                boxWidth: 8,
              }
            },
            tooltip: {
              backgroundColor: isDark ? 'rgba(15, 23, 42, 0.9)' : 'rgba(255, 255, 255, 0.95)',
              titleColor: isDark ? '#f8fafc' : '#0f172a',
              bodyColor: isDark ? '#cbd5e1' : '#334155',
              borderColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
              borderWidth: 1,
              padding: 10,
              boxPadding: 4,
              bodyFont: { family: 'Inter' },
              titleFont: { family: 'Inter', weight: '600' },
            }
          },
          scales: {
            x: {
              grid: { color: gridColor },
              ticks: { color: textColor, font: { family: 'Inter', size: 11 }, maxTicksLimit: 8 },
            },
            y: {
              type: 'linear',
              display: true,
              position: 'left',
              title: { display: true, text: 'Flow (L/min)', color: textColor, font: { size: 11 } },
              grid: { color: gridColor },
              ticks: { color: textColor, font: { family: 'Inter', size: 11 } },
              min: 0,
              suggestedMax: 5,
            },
            y1: {
              type: 'linear',
              display: true,
              position: 'right',
              title: { display: true, text: 'Total (L)', color: textColor, font: { size: 11 } },
              grid: { drawOnChartArea: false },
              ticks: { color: textColor, font: { family: 'Inter', size: 11 } },
            }
          }
        }
      });

      state.chartInstance = this.chartInstance;
    },

    update(readings) {
      if (!this.chartInstance || !readings || !readings.length) return;

      const recentReadings = readings.slice(-15);
      const labels = recentReadings.map(r => {
        const d = new Date(r.ts || r.timestamp || Date.now());
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      });

      const flowData = recentReadings.map(r => r.flow_lpm ?? r.flow_rate ?? 0);
      const totalData = recentReadings.map(r => r.total_l ?? r.total ?? 0);

      this.chartInstance.data.labels = labels;
      this.chartInstance.data.datasets[0].data = flowData;
      this.chartInstance.data.datasets[1].data = totalData;
      this.chartInstance.update('none');
    },

    updateTheme(isDark) {
      if (!this.chartInstance) return;
      const gridColor = isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)';
      const textColor = isDark ? '#94a3b8' : '#64748b';

      this.chartInstance.options.scales.x.grid.color = gridColor;
      this.chartInstance.options.scales.x.ticks.color = textColor;
      this.chartInstance.options.scales.y.grid.color = gridColor;
      this.chartInstance.options.scales.y.ticks.color = textColor;
      this.chartInstance.options.scales.y.title.color = textColor;
      this.chartInstance.options.scales.y1.ticks.color = textColor;
      this.chartInstance.options.scales.y1.title.color = textColor;
      this.chartInstance.options.plugins.legend.labels.color = textColor;
      this.chartInstance.update();
    }
  };

  /* ==========================================================================
     5. UI Renderers
     ========================================================================== */
  const UI = {
    elements: {
      gaugeCircle: document.getElementById('gauge-progress-circle'),
      gaugeUsedText: document.getElementById('gauge-used-litres'),
      gaugeLimitText: document.getElementById('gauge-limit-litres'),
      gaugePercentBadge: document.getElementById('gauge-percent-badge'),
      gaugeCard: document.getElementById('card-gauge'),
      gaugeDesc: document.getElementById('gauge-status-desc'),

      liveFlowText: document.getElementById('live-flow-rate'),
      flowHourlyText: document.getElementById('flow-hourly'),
      flowCard: document.getElementById('card-flow'),

      relayPill: document.getElementById('relay-status-pill'),
      relayText: document.getElementById('relay-status-text'),
      relayIcon: document.getElementById('relay-icon'),
      relayDesc: document.getElementById('relay-desc'),
      relayToggleBtn: document.getElementById('btn-toggle-relay'),
      relayToggleBtnText: document.getElementById('btn-toggle-relay-text'),

      deviceStatusBadge: document.getElementById('device-status-badge'),
      deviceStatusText: document.getElementById('device-status-text'),
      mockBadge: document.getElementById('mock-badge'),

      policyQuota: document.getElementById('policy-quota'),
      policyRate: document.getElementById('policy-rate'),
      policyExcessLitres: document.getElementById('policy-excess-litres'),
      policyExcessAmount: document.getElementById('policy-excess-amount'),

      billsTableBody: document.getElementById('bills-table-body'),
      billsCount: document.getElementById('bills-count'),

      alertBanner: document.getElementById('system-alert-banner'),
      alertMessage: document.getElementById('alert-message'),
      closeAlertBtn: document.getElementById('close-alert-btn'),

      themeToggleBtn: document.getElementById('theme-toggle-btn'),
      themeIcon: document.getElementById('theme-icon'),
      syncSpinner: document.getElementById('sync-spinner'),
    },

    renderUsage(data) {
      const used = Number(data.total).toFixed(1);
      const limit = Number(data.limit);
      const percent = Math.min(Math.round((used / limit) * 100), 100);
      const isOverLimit = used >= limit;

      // Update Gauge Number & Texts
      this.elements.gaugeUsedText.textContent = used;
      this.elements.gaugeLimitText.textContent = limit;
      this.elements.gaugePercentBadge.textContent = `${percent}%`;

      // Circumference = 2 * PI * 90 = 565.487
      const circumference = 565.487;
      const progressPercent = Math.min(used / limit, 1.0);
      const offset = circumference - (progressPercent * circumference);
      this.elements.gaugeCircle.style.strokeDashoffset = offset;

      // Quota policy summary
      this.elements.policyQuota.textContent = `${limit} Litres`;
      this.elements.policyRate.textContent = `₹${Number(data.rate_per_l).toFixed(2)} / Litre`;

      const excess = Math.max(0, used - limit);
      const excessAmount = excess * data.rate_per_l;
      this.elements.policyExcessLitres.textContent = `${excess.toFixed(1)} L`;
      this.elements.policyExcessAmount.textContent = `₹${excessAmount.toFixed(2)}`;

      // Gauge warning & danger states
      if (isOverLimit) {
        this.elements.gaugeCard.classList.add('gauge-danger');
        this.elements.gaugeCircle.style.stroke = 'var(--gauge-danger)';
        this.elements.gaugeDesc.innerHTML = `<strong style="color: var(--danger);">Quota Exceeded by ${excess.toFixed(1)} L</strong>`;
        this.showAlert(`Monthly limit of ${limit}L exceeded! Motor valve shut off. Pay excess bill to restore flow.`, 'danger');
      } else if (percent >= 80) {
        this.elements.gaugeCard.classList.remove('gauge-danger');
        this.elements.gaugeCircle.style.stroke = 'var(--gauge-warn)';
        this.elements.gaugeDesc.textContent = 'Approaching monthly allowance threshold';
      } else {
        this.elements.gaugeCard.classList.remove('gauge-danger');
        this.elements.gaugeCircle.style.stroke = 'var(--gauge-safe)';
        this.elements.gaugeDesc.textContent = 'Consumption within standard allowance';
      }
    },

    renderFlow(flowRate) {
      const flow = Number(flowRate).toFixed(1);
      this.elements.liveFlowText.textContent = flow;
      this.elements.flowHourlyText.textContent = `${(flow * 60).toFixed(0)} L/h`;

      if (flow > 0) {
        this.elements.flowCard.classList.add('flow-active');
      } else {
        this.elements.flowCard.classList.remove('flow-active');
      }
    },

    renderRelay(relayState) {
      const isOn = relayState.toUpperCase() === 'ON';

      if (isOn) {
        this.elements.relayPill.className = 'relay-status-pill on';
        this.elements.relayText.textContent = 'VALVE OPEN (ON)';
        this.elements.relayIcon.className = 'fa-solid fa-circle-check';
        this.elements.relayDesc.textContent = 'Motor valve is active. Extraction allowed under monthly quota.';

        this.elements.relayToggleBtn.className = 'btn-relay-toggle action-turn-off';
        this.elements.relayToggleBtnText.textContent = 'Turn Relay OFF';
      } else {
        this.elements.relayPill.className = 'relay-status-pill off';
        this.elements.relayText.textContent = 'VALVE SHUT (OFF)';
        this.elements.relayIcon.className = 'fa-solid fa-circle-xmark';
        this.elements.relayDesc.textContent = 'Motor valve is shut off. Extraction halted.';

        this.elements.relayToggleBtn.className = 'btn-relay-toggle action-turn-on';
        this.elements.relayToggleBtnText.textContent = 'Turn Relay ON';
      }
      this.elements.relayToggleBtn.disabled = false;
    },

    renderDeviceStatus(status) {
      const isOnline = status.toLowerCase() === 'online';
      if (isOnline) {
        this.elements.deviceStatusBadge.className = 'badge badge-online';
        this.elements.deviceStatusText.textContent = 'ONLINE';
      } else {
        this.elements.deviceStatusBadge.className = 'badge badge-offline';
        this.elements.deviceStatusText.textContent = 'OFFLINE';
      }
    },

    renderBills(bills) {
      this.elements.billsCount.textContent = `${bills.length} ${bills.length === 1 ? 'bill' : 'bills'}`;

      if (!bills || bills.length === 0) {
        this.elements.billsTableBody.innerHTML = `
          <tr>
            <td colspan="6" class="empty-state">
              <i class="fa-regular fa-folder-open" style="font-size: 1.5rem; display: block; margin-bottom: 8px;"></i>
              No excess bills generated yet. Consumption is within monthly limits.
            </td>
          </tr>
        `;
        return;
      }

      this.elements.billsTableBody.innerHTML = bills.map(b => {
        const isPaid = (b.status || '').toLowerCase() === 'paid';
        const badgeClass = isPaid ? 'status-paid' : 'status-unpaid';
        const formattedDate = new Date(b.ts || b.timestamp || Date.now()).toLocaleString([], {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        });

        return `
          <tr>
            <td><strong>#${b.id}</strong></td>
            <td>${b.device_id || state.deviceId}</td>
            <td>${Number(b.excess_l ?? b.excess_litres ?? 0).toFixed(1)} L</td>
            <td><strong>₹${Number(b.amount ?? 0).toFixed(2)}</strong></td>
            <td><span class="status-badge ${badgeClass}">${b.status}</span></td>
            <td style="color: var(--text-muted); font-size: 0.85rem;">${formattedDate}</td>
          </tr>
        `;
      }).join('');
    },

    showAlert(message, type = 'danger') {
      this.elements.alertBanner.className = `alert-banner active ${type}`;
      this.elements.alertMessage.textContent = message;
    },

    hideAlert() {
      this.elements.alertBanner.className = 'alert-banner';
    },

    setTheme(theme) {
      state.theme = theme;
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem('aquapulse_theme', theme);
      if (theme === 'dark') {
        this.elements.themeIcon.className = 'fa-solid fa-moon';
      } else {
        this.elements.themeIcon.className = 'fa-solid fa-sun';
      }
      ChartEngine.updateTheme(theme === 'dark');
    }
  };

  /* ==========================================================================
     6. Polling Engine & Application Lifecycle
     ========================================================================== */
  async function pollData() {
    UI.elements.syncSpinner.style.animation = 'spin 0.6s linear infinite';

    try {
      // 1. Fetch current usage & device telemetry
      const rawUsage = await ApiClient.getUsage(state.deviceId);
      const usage = normalizeUsageData(rawUsage);

      state.totalUsageLitres = usage.total;
      state.monthlyLimitLitres = usage.limit;
      state.flowRateLpm = usage.flow_lpm;
      state.relayState = usage.relay;
      state.deviceStatus = usage.status;

      UI.renderUsage(usage);
      UI.renderFlow(usage.flow_lpm);
      UI.renderRelay(usage.relay);
      UI.renderDeviceStatus(usage.status);

      // 2. Fetch history readings for chart
      const readings = await ApiClient.getReadings(state.deviceId);
      state.readingsHistory = readings;
      ChartEngine.update(readings);

      // 3. Fetch bills
      const bills = await ApiClient.getBills(state.deviceId);
      state.bills = bills;
      UI.renderBills(bills);

      // If online and usage under limit, ensure alert is clear
      if (usage.total < usage.limit && !state.mockNetworkFail) {
        UI.hideAlert();
      }

    } catch (err) {
      console.warn('[Dashboard Polling Error]:', err.message);
      UI.renderDeviceStatus('offline');
      UI.showAlert(`Unable to reach backend API (${config.API_BASE_URL}). Retrying in ${config.POLL_INTERVAL_MS / 1000}s...`, 'warning');
    } finally {
      setTimeout(() => {
        UI.elements.syncSpinner.style.animation = 'spin 2s linear infinite';
      }, 500);
    }
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    pollData();
    state.pollTimer = setInterval(pollData, config.POLL_INTERVAL_MS);
  }

  /* ==========================================================================
     7. Event Listeners & Interactive Handlers
     ========================================================================== */
  function setupEventListeners() {
    // Theme toggle
    UI.elements.themeToggleBtn.addEventListener('click', () => {
      const nextTheme = state.theme === 'dark' ? 'light' : 'dark';
      UI.setTheme(nextTheme);
    });

    // Alert close button
    UI.elements.closeAlertBtn.addEventListener('click', () => {
      UI.hideAlert();
    });

    // Relay Manual Toggle Button
    UI.elements.relayToggleBtn.addEventListener('click', async () => {
      const targetState = state.relayState === 'ON' ? 'OFF' : 'ON';
      UI.elements.relayToggleBtn.disabled = true;
      UI.elements.relayToggleBtnText.textContent = 'Updating...';

      try {
        const res = await ApiClient.setRelay(state.deviceId, targetState);
        state.relayState = res.relay || targetState;
        UI.renderRelay(state.relayState);
        if (state.relayState === 'OFF') {
          UI.renderFlow(0.0);
        }
        await pollData();
      } catch (err) {
        alert(`Failed to toggle relay: ${err.message}`);
        UI.renderRelay(state.relayState);
      }
    });

    // Mock Mode Toolbar: Add +30 L
    const btnAddFlow = document.getElementById('mock-btn-add-flow');
    if (btnAddFlow) {
      btnAddFlow.addEventListener('click', () => {
        state.totalUsageLitres += 30.0;
        pollData();
      });
    }

    // Mock Mode Toolbar: Trigger Limit Breach (>500L)
    const btnTriggerBreach = document.getElementById('mock-btn-trigger-breach');
    if (btnTriggerBreach) {
      btnTriggerBreach.addEventListener('click', () => {
        state.totalUsageLitres = 502.5;
        state.relayState = 'OFF';
        state.flowRateLpm = 0.0;
        pollData();
      });
    }

    // Mock Mode Toolbar: Toggle Network Failure
    const btnToggleNetwork = document.getElementById('mock-btn-toggle-network');
    if (btnToggleNetwork) {
      btnToggleNetwork.addEventListener('click', () => {
        state.mockNetworkFail = !state.mockNetworkFail;
        btnToggleNetwork.style.borderColor = state.mockNetworkFail ? 'var(--danger)' : 'var(--border-subtle)';
        pollData();
      });
    }

    // Mock Mode Toolbar: Reset Demo State
    const btnReset = document.getElementById('mock-btn-reset');
    if (btnReset) {
      btnReset.addEventListener('click', () => {
        state.totalUsageLitres = 460.0;
        state.monthlyLimitLitres = config.DEFAULT_MONTHLY_LIMIT_L;
        state.flowRateLpm = 2.4;
        state.relayState = 'ON';
        state.bills = [];
        state.mockNetworkFail = false;
        if (btnToggleNetwork) btnToggleNetwork.style.borderColor = 'var(--border-subtle)';
        MockEngine.init();
        UI.hideAlert();
        pollData();
      });
    }
  }

  /* ==========================================================================
     8. Bootstrap Application
     ========================================================================== */
  document.addEventListener('DOMContentLoaded', () => {
    UI.setTheme(state.theme);

    if (config.USE_MOCK) {
      MockEngine.init();
      UI.elements.mockBadge.style.display = 'inline-flex';
      document.getElementById('mock-toolbar').style.display = 'flex';
    } else {
      UI.elements.mockBadge.style.display = 'none';
      document.getElementById('mock-toolbar').style.display = 'none';
    }

    ChartEngine.init();
    setupEventListeners();
    startPolling();
  });

})();
