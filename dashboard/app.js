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
    currentUser: JSON.parse(localStorage.getItem('aquapulse_user')) || { role: 'admin', username: 'Admin' },
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
      } else {
        // Relay is OFF
        state.flowRateLpm = 0.0;
      }

      // Check limit threshold breach whenever total >= limit
      if (state.totalUsageLitres >= state.monthlyLimitLitres) {
        const excess = +(state.totalUsageLitres - state.monthlyLimitLitres).toFixed(2);
        const amount = +(excess * state.ratePerLitre).toFixed(2);

        // Check if unpaid bill already exists
        const existingUnpaid = state.bills.find(b => (b.status || '').toLowerCase() === 'unpaid');
        if (!existingUnpaid && excess > 0) {
          state.bills.unshift({
            id: `INV-${Date.now().toString().slice(-6)}`,
            device_id: state.deviceId,
            excess_l: excess,
            amount: amount > 0 ? amount : 0.05,
            status: 'unpaid',
            ts: new Date().toISOString(),
          });
          // Automatic valve cutoff
          state.relayState = 'OFF';
          state.flowRateLpm = 0.0;
        }
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
    },

    createOrder(billId) {
      return {
        success: true,
        order_id: `order_mock_${Date.now()}`,
        amount: 100,
        currency: 'INR',
        key_id: 'rzp_test_mock',
        bill_id: billId
      };
    },

    verifyPayment(payload) {
      const bill = state.bills.find(b => String(b.id) === String(payload.bill_id));
      if (bill) {
        bill.status = 'paid';
        bill.payment_id = payload.payment_id || `pay_mock_${Date.now()}`;
      }
      state.totalUsageLitres = 0.0;
      state.relayState = 'ON';
      state.flowRateLpm = 2.4;
      return {
        success: true,
        message: 'Mock payment verified successfully and quota cycle reset.',
        bill_id: payload.bill_id,
        status: 'paid',
        payment_id: bill ? bill.payment_id : 'pay_mock',
        relay: 'ON'
      };
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
      const timeoutMs = options.timeout || 12000;
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

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
          let errMsg = `HTTP Error ${response.status}: ${response.statusText}`;
          try {
            const errJson = await response.json();
            if (errJson && errJson.error) errMsg = errJson.error;
          } catch (_) {}
          throw new Error(errMsg);
        }
        return await response.json();
      } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError' || (err.message && err.message.toLowerCase().includes('abort'))) {
          throw new Error('Backend is waking up from sleep mode. Please retry in a few seconds.');
        }
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
     * GET /api/bills/<device> (or /api/bills for all devices)
     * Returns: list of bills
     */
    async getBills(deviceId) {
      if (config.USE_MOCK) {
        return MockEngine.getBills();
      }
      return await this.request(`/api/bills/${encodeURIComponent(deviceId)}`);
    },

    /**
     * GET /api/bills
     * Returns: all bills across devices (admin overview)
     */
    async getAllBills() {
      if (config.USE_MOCK) {
        return MockEngine.getBills();
      }
      return await this.request('/api/bills');
    },

    /**
     * POST /api/device/<device>/quota with body {"monthly_limit_l": <num>, "rate_per_l": <num>}
     */
    async updateQuota(deviceId, limit, rate) {
      if (config.USE_MOCK) {
        state.monthlyLimitLitres = Number(limit);
        state.ratePerLitre = Number(rate);
        return { success: true, monthly_limit_l: limit, rate_per_l: rate };
      }
      return await this.request(`/api/device/${encodeURIComponent(deviceId)}/quota`, {
        method: 'POST',
        body: JSON.stringify({ monthly_limit_l: Number(limit), rate_per_l: Number(rate) }),
      });
    },

    /**
     * POST /api/auth/login with body {"username", "password"}
     */
    async login(username, password) {
      if (config.USE_MOCK) {
        const u = (username || '').toLowerCase().trim();
        if (u === 'admin') {
          return { role: 'admin', username: 'Admin', device: state.deviceId };
        }
        return { role: 'user', username: username || 'User', device: state.deviceId };
      }
      return await this.request('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
    },

    /**
     * POST /api/pay/create-order with body {"bill_id": <int>}
     * Returns: Razorpay order details {order_id, amount, currency, key_id, bill_id}
     */
    async createOrder(billId) {
      if (config.USE_MOCK) {
        return MockEngine.createOrder(billId);
      }
      return await this.request('/api/pay/create-order', {
        method: 'POST',
        body: JSON.stringify({ bill_id: Number(billId) }),
      });
    },

    /**
     * POST /api/pay/verify with body {"bill_id", "order_id", "payment_id", "signature"}
     * Returns: verification result {success, message, bill_id, status, relay}
     */
    async verifyPayment(payload) {
      if (config.USE_MOCK) {
        return MockEngine.verifyPayment(payload);
      }
      return await this.request('/api/pay/verify', {
        method: 'POST',
        body: JSON.stringify({
          bill_id: Number(payload.bill_id),
          order_id: String(payload.order_id),
          payment_id: String(payload.payment_id),
          signature: String(payload.signature),
        }),
      });
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
      userRelayLock: document.getElementById('user-relay-lock'),

      deviceStatusBadge: document.getElementById('device-status-badge'),
      deviceStatusText: document.getElementById('device-status-text'),
      mockBadge: document.getElementById('mock-badge'),

      policyQuota: document.getElementById('policy-quota'),
      policyRate: document.getElementById('policy-rate'),
      policyExcessLitres: document.getElementById('policy-excess-litres'),
      policyExcessAmount: document.getElementById('policy-excess-amount'),
      cardPayActionContainer: document.getElementById('card-pay-action-container'),
      btnQuickPayCard: document.getElementById('btn-quick-pay-card'),
      quickPayAmount: document.getElementById('quick-pay-amount'),

      billsTableBody: document.getElementById('bills-table-body'),
      billsCount: document.getElementById('bills-count'),
      billsCardTitle: document.getElementById('bills-card-title'),
      billsActionHeader: document.getElementById('bills-action-header'),

      alertBanner: document.getElementById('system-alert-banner'),
      alertMessage: document.getElementById('alert-message'),
      closeAlertBtn: document.getElementById('close-alert-btn'),

      themeToggleBtn: document.getElementById('theme-toggle-btn'),
      themeIcon: document.getElementById('theme-icon'),
      syncSpinner: document.getElementById('sync-spinner'),

      // Auth & Role Elements
      authModal: document.getElementById('auth-modal-overlay'),
      authForm: document.getElementById('auth-login-form'),
      authUsername: document.getElementById('auth-username'),
      authPassword: document.getElementById('auth-password'),
      authErrorMsg: document.getElementById('auth-error-msg'),
      btnSelectRoleAdmin: document.getElementById('btn-select-role-admin'),
      btnSelectRoleUser: document.getElementById('btn-select-role-user'),
      btnQuickAdmin: document.getElementById('btn-quick-admin'),
      btnQuickUser: document.getElementById('btn-quick-user'),
      currentRoleBadge: document.getElementById('current-role-badge'),
      currentRoleIcon: document.getElementById('current-role-icon'),
      currentRoleText: document.getElementById('current-role-text'),
      btnLogout: document.getElementById('btn-logout'),

      // Admin Quota Modal
      btnOpenQuotaModal: document.getElementById('btn-open-quota-modal'),
      quotaModalOverlay: document.getElementById('quota-modal-overlay'),
      btnCloseQuotaModal: document.getElementById('btn-close-quota-modal'),
      btnCancelQuota: document.getElementById('btn-cancel-quota'),
      formUpdateQuota: document.getElementById('form-update-quota'),
      inputQuotaDevice: document.getElementById('input-quota-device'),
      inputQuotaLimit: document.getElementById('input-quota-limit'),
      inputQuotaRate: document.getElementById('input-quota-rate'),
    },

    renderRole() {
      const role = state.currentUser.role || 'admin';
      const username = state.currentUser.username || (role === 'admin' ? 'Admin' : 'User');

      if (role === 'admin') {
        if (this.elements.currentRoleBadge) {
          this.elements.currentRoleBadge.className = 'badge badge-role admin';
          this.elements.currentRoleBadge.title = `Signed in as Administrator (${username})`;
        }
        if (this.elements.currentRoleIcon) this.elements.currentRoleIcon.className = 'fa-solid fa-user-shield';
        if (this.elements.currentRoleText) this.elements.currentRoleText.textContent = 'Admin';
        if (this.elements.btnOpenQuotaModal) this.elements.btnOpenQuotaModal.style.display = 'inline-flex';
        if (this.elements.cardPayActionContainer) this.elements.cardPayActionContainer.style.display = 'none';
        if (this.elements.billsCardTitle) {
          this.elements.billsCardTitle.innerHTML = '<i class="fa-solid fa-file-invoice-dollar"></i> Excess Usage Invoices & Payment Audit';
        }
        if (this.elements.billsActionHeader) {
          this.elements.billsActionHeader.textContent = 'Action / Payment Reference';
        }
      } else {
        if (this.elements.currentRoleBadge) {
          this.elements.currentRoleBadge.className = 'badge badge-role user';
          this.elements.currentRoleBadge.title = `Signed in as ${username}`;
        }
        if (this.elements.currentRoleIcon) this.elements.currentRoleIcon.className = 'fa-solid fa-user';
        if (this.elements.currentRoleText) this.elements.currentRoleText.textContent = username;
        if (this.elements.btnOpenQuotaModal) this.elements.btnOpenQuotaModal.style.display = 'none';
        if (this.elements.billsCardTitle) {
          this.elements.billsCardTitle.innerHTML = '<i class="fa-solid fa-file-invoice-dollar"></i> My Excess Invoices';
        }
        if (this.elements.billsActionHeader) {
          this.elements.billsActionHeader.textContent = 'Action';
        }
      }
      this.renderRelay(state.relayState);
    },

    renderUsage(data, hasUnpaidBills = false) {
      const used = Number(data.total).toFixed(1);
      const limit = Number(data.limit);
      const percent = Math.min(Math.round((used / limit) * 100), 100);
      const isOverLimit = used >= limit;
      const isUser = (state.currentUser.role || 'user') === 'user';

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

      // Update Quick Pay Button in Card
      const unpaidBill = (state.bills || []).find(b => (b.status || '').toLowerCase() === 'unpaid');
      if (this.elements.cardPayActionContainer) {
        if (isUser && (hasUnpaidBills || unpaidBill)) {
          this.elements.cardPayActionContainer.style.display = 'block';
          const dueAmt = unpaidBill ? Number(unpaidBill.amount) : excessAmount;
          if (this.elements.quickPayAmount) this.elements.quickPayAmount.textContent = `₹${dueAmt.toFixed(2)}`;
          if (this.elements.btnQuickPayCard && unpaidBill) {
            this.elements.btnQuickPayCard.setAttribute('data-bill-id', unpaidBill.id);
          }
        } else {
          this.elements.cardPayActionContainer.style.display = 'none';
        }
      }

      // Gauge warning, danger, and paid excess states
      if (isOverLimit) {
        this.elements.gaugeCard.classList.add('gauge-danger');
        this.elements.gaugeCircle.style.stroke = 'var(--gauge-danger)';
        const hasUnpaid = hasUnpaidBills || (state.bills && state.bills.some(b => (b.status || '').toLowerCase() === 'unpaid'));
        if (hasUnpaid) {
          this.elements.gaugeDesc.innerHTML = `<strong style="color: var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> Limit Breached (+${excess.toFixed(1)} L) — Bill Unpaid</strong>`;
          this.showAlert(`Monthly limit of ${limit}L exceeded! Motor valve shut off. Pay excess bill to restore flow.`, 'danger');
        } else {
          this.elements.gaugeDesc.innerHTML = `<strong style="color: var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> Limit Breached (+${excess.toFixed(1)} L) — Exceeds Quota</strong>`;
        }
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
      const isAdmin = (state.currentUser.role || 'admin') === 'admin';

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

      // Role permission: Admin has toggle button, User sees automatic system lock
      if (isAdmin) {
        this.elements.relayToggleBtn.style.display = 'inline-flex';
        if (this.elements.userRelayLock) this.elements.userRelayLock.style.display = 'none';
      } else {
        this.elements.relayToggleBtn.style.display = 'none';
        if (this.elements.userRelayLock) this.elements.userRelayLock.style.display = 'flex';
      }
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
      const isAdmin = (state.currentUser.role || 'admin') === 'admin';

      if (!bills || bills.length === 0) {
        this.elements.billsTableBody.innerHTML = `
          <tr>
            <td colspan="7" class="empty-state">
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

        let actionHtml = '';
        if (isAdmin) {
          // Admin audit view: shows payment verification and transaction reference (no Pay button)
          if (isPaid) {
            const refText = b.payment_id ? `Ref: ${b.payment_id}` : 'Verified & Cleared';
            actionHtml = `<span class="paid-badge"><i class="fa-solid fa-circle-check"></i> Paid</span> <span class="payment-ref">${refText}</span>`;
          } else {
            actionHtml = `<span class="status-badge status-unpaid"><i class="fa-solid fa-clock"></i> Unpaid (Pending)</span>`;
          }
        } else {
          // User consumer view: displays Pay Now checkout button for unpaid bills
          actionHtml = isPaid
            ? `<span class="paid-badge"><i class="fa-solid fa-circle-check"></i> Paid</span>`
            : `<button class="btn-pay-now" data-bill-id="${b.id}" aria-label="Pay Bill #${b.id}">
                 <i class="fa-solid fa-credit-card"></i> Pay Now (₹${Number(b.amount ?? 0).toFixed(2)})
               </button>`;
        }

        return `
          <tr>
            <td><strong>#${b.id}</strong></td>
            <td>${b.device || b.device_id || state.deviceId}</td>
            <td>${Number(b.excess_l ?? b.excess_litres ?? 0).toFixed(1)} L</td>
            <td><strong>₹${Number(b.amount ?? 0).toFixed(2)}</strong></td>
            <td><span class="status-badge ${badgeClass}">${b.status}</span></td>
            <td style="color: var(--text-muted); font-size: 0.85rem;">${formattedDate}</td>
            <td>${actionHtml}</td>
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
     6. Razorpay Checkout Flow Handler
     ========================================================================== */
  async function initiatePayment(billId, buttonEl) {
    if (!billId) return;

    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.innerHTML = `<span class="spinner-sm"></span> Initializing...`;
    }

    try {
      // 1. Create order on backend
      const orderData = await ApiClient.createOrder(billId);
      if (!orderData || !orderData.order_id) {
        throw new Error('Could not create payment order from backend.');
      }

      // 2. Mock mode handling or missing Razorpay SDK fallback
      if (config.USE_MOCK || !window.Razorpay) {
        if (!window.Razorpay && !config.USE_MOCK) {
          throw new Error('Razorpay SDK failed to load. Please check internet connection.');
        }

        UI.showAlert('Mock Mode: Simulating Razorpay checkout...', 'warning');
        setTimeout(async () => {
          try {
            await ApiClient.verifyPayment({
              bill_id: billId,
              order_id: orderData.order_id,
              payment_id: `pay_mock_${Date.now()}`,
              signature: 'mock_signature'
            });
            UI.showAlert(`Mock Payment for Bill #${billId} successful! Relay valve restored to ON.`, 'success');
            await pollData();
          } catch (mockErr) {
            UI.showAlert(`Mock Payment failed: ${mockErr.message}`, 'danger');
            UI.renderBills(state.bills);
          }
        }, 800);
        return;
      }

      // 3. Open Razorpay Standard Checkout modal
      const options = {
        key: orderData.key_id,
        amount: orderData.amount,
        currency: orderData.currency || 'INR',
        name: 'AquaPulse Groundwater',
        description: `Excess Water Usage Invoice #${billId} (${state.deviceId})`,
        order_id: orderData.order_id,
        theme: {
          color: '#0284c7'
        },
        modal: {
          ondismiss: function () {
            UI.showAlert('Payment checkout cancelled. Invoice remains unpaid and valve is off.', 'warning');
            UI.renderBills(state.bills);
          }
        },
        handler: async function (response) {
          try {
            if (buttonEl) {
              buttonEl.disabled = true;
              buttonEl.innerHTML = `<span class="spinner-sm"></span> Verifying...`;
            }
            UI.showAlert('Payment received! Verifying cryptographic signature with backend...', 'warning');

            const verifyRes = await ApiClient.verifyPayment({
              bill_id: billId,
              order_id: response.razorpay_order_id,
              payment_id: response.razorpay_payment_id,
              signature: response.razorpay_signature,
            });

            UI.showAlert(`Payment of ₹${(orderData.amount / 100).toFixed(2)} verified! Monthly quota cycle reset and relay turned ON.`, 'success');
            state.totalUsageLitres = 0.0;
            await pollData();
          } catch (verifyErr) {
            console.error('[Payment Verification Error]:', verifyErr);
            UI.showAlert(`Payment verification failed: ${verifyErr.message}`, 'danger');
            await pollData();
          }
        },
        prefill: {
          name: 'Meter Customer',
          email: 'customer@aquapulse.io',
          contact: '9999999999'
        }
      };

      const rzp = new window.Razorpay(options);
      rzp.on('payment.failed', function (resp) {
        console.error('[Razorpay Payment Failed]:', resp.error);
        UI.showAlert(`Payment failed: ${resp.error.description || resp.error.reason}`, 'danger');
        UI.renderBills(state.bills);
      });
      rzp.open();

    } catch (err) {
      console.error('[Initiate Payment Error]:', err);
      UI.showAlert(`Unable to initiate payment: ${err.message}`, 'danger');
      if (buttonEl) {
        buttonEl.disabled = false;
        buttonEl.innerHTML = `<i class="fa-solid fa-credit-card"></i> Pay Now`;
      }
    }
  }

  /* ==========================================================================
     7. Polling Engine & Application Lifecycle
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

      // 2. Fetch bills to determine unpaid status
      const bills = await ApiClient.getBills(state.deviceId);
      state.bills = bills;
      UI.renderBills(bills);

      const hasUnpaidBills = bills.some(b => (b.status || '').toLowerCase() === 'unpaid');

      // 3. Render telemetry & gauge states
      UI.renderUsage(usage, hasUnpaidBills);
      UI.renderFlow(usage.flow_lpm);
      UI.renderRelay(usage.relay);
      UI.renderDeviceStatus(usage.status);

      // 4. Fetch history readings for chart
      const readings = await ApiClient.getReadings(state.deviceId);
      state.readingsHistory = readings;
      ChartEngine.update(readings);

      // If online and no unpaid bills, clear danger alert
      if (!hasUnpaidBills && !state.mockNetworkFail) {
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
     8. Event Listeners & Interactive Handlers
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

    // Auth Modal: Switch Account / Sign Out button in header
    if (UI.elements.btnLogout) {
      UI.elements.btnLogout.addEventListener('click', () => {
        openAuthModal();
      });
    }

    // Auth Modal: Role Select Tabs
    if (UI.elements.btnSelectRoleAdmin) {
      UI.elements.btnSelectRoleAdmin.addEventListener('click', () => {
        UI.elements.btnSelectRoleAdmin.classList.add('active');
        UI.elements.btnSelectRoleUser.classList.remove('active');
        UI.elements.authUsername.value = 'admin';
        UI.elements.authPassword.value = 'admin123';
        if (UI.elements.authErrorMsg) UI.elements.authErrorMsg.style.display = 'none';
      });
    }

    if (UI.elements.btnSelectRoleUser) {
      UI.elements.btnSelectRoleUser.addEventListener('click', () => {
        UI.elements.btnSelectRoleUser.classList.add('active');
        UI.elements.btnSelectRoleAdmin.classList.remove('active');
        UI.elements.authUsername.value = 'user';
        UI.elements.authPassword.value = 'user123';
        if (UI.elements.authErrorMsg) UI.elements.authErrorMsg.style.display = 'none';
      });
    }

    // Quick 1-Click Demo Logins
    if (UI.elements.btnQuickAdmin) {
      UI.elements.btnQuickAdmin.addEventListener('click', () => {
        applyLogin('admin', 'Admin');
      });
    }

    if (UI.elements.btnQuickUser) {
      UI.elements.btnQuickUser.addEventListener('click', () => {
        applyLogin('user', 'User');
      });
    }

    // Auth Form Submission
    if (UI.elements.authForm) {
      UI.elements.authForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = UI.elements.authUsername.value.trim();
        const password = UI.elements.authPassword.value.trim();

        if (UI.elements.authErrorMsg) UI.elements.authErrorMsg.style.display = 'none';

        try {
          const authRes = await ApiClient.login(username, password);
          applyLogin(authRes.role || (username.toLowerCase() === 'admin' ? 'admin' : 'user'), authRes.username || username);
        } catch (err) {
          // Client-side fallback if backend unavailable
          const role = username.toLowerCase() === 'admin' ? 'admin' : 'user';
          applyLogin(role, username.charAt(0).toUpperCase() + username.slice(1));
        }
      });
    }

    // Admin Quota Modal Open
    if (UI.elements.btnOpenQuotaModal) {
      UI.elements.btnOpenQuotaModal.addEventListener('click', () => {
        if (UI.elements.inputQuotaLimit) UI.elements.inputQuotaLimit.value = state.monthlyLimitLitres;
        if (UI.elements.inputQuotaRate) UI.elements.inputQuotaRate.value = state.ratePerLitre;
        if (UI.elements.inputQuotaDevice) UI.elements.inputQuotaDevice.value = state.deviceId;
        if (UI.elements.quotaModalOverlay) UI.elements.quotaModalOverlay.style.display = 'flex';
      });
    }

    // Admin Quota Modal Close / Cancel
    if (UI.elements.btnCloseQuotaModal) {
      UI.elements.btnCloseQuotaModal.addEventListener('click', () => {
        if (UI.elements.quotaModalOverlay) UI.elements.quotaModalOverlay.style.display = 'none';
      });
    }
    if (UI.elements.btnCancelQuota) {
      UI.elements.btnCancelQuota.addEventListener('click', () => {
        if (UI.elements.quotaModalOverlay) UI.elements.quotaModalOverlay.style.display = 'none';
      });
    }

    // Admin Quota Modal Form Submit
    if (UI.elements.formUpdateQuota) {
      UI.elements.formUpdateQuota.addEventListener('submit', async (e) => {
        e.preventDefault();
        const limitVal = parseFloat(UI.elements.inputQuotaLimit.value);
        const rateVal = parseFloat(UI.elements.inputQuotaRate.value);

        if (isNaN(limitVal) || limitVal <= 0) {
          UI.showAlert('Monthly limit must be a positive number.', 'warning');
          return;
        }
        if (isNaN(rateVal) || rateVal < 0) {
          UI.showAlert('Tariff rate cannot be negative.', 'warning');
          return;
        }

        try {
          await ApiClient.updateQuota(state.deviceId, limitVal, rateVal);
          state.monthlyLimitLitres = limitVal;
          state.ratePerLitre = rateVal;
          if (UI.elements.quotaModalOverlay) UI.elements.quotaModalOverlay.style.display = 'none';
          UI.showAlert(`Quota policy updated: Limit ${limitVal}L, Rate ₹${rateVal}/L`, 'success');
          await pollData();
        } catch (err) {
          UI.showAlert(`Failed to update quota: ${err.message}`, 'danger');
        }
      });
    }

    // Relay Manual Toggle Button (Admin only)
    UI.elements.relayToggleBtn.addEventListener('click', async () => {
      if (state.currentUser.role !== 'admin') {
        UI.showAlert('Relay override is restricted to Administrators.', 'warning');
        return;
      }

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
        console.warn('[Relay Toggle Error]:', err.message);
        UI.showAlert(`Unable to switch valve: ${err.message}`, 'warning');
        UI.renderRelay(state.relayState);
      } finally {
        UI.elements.relayToggleBtn.disabled = false;
      }
    });

    // Bills Table Pay Now Button Click Delegation (User role checkout)
    UI.elements.billsTableBody.addEventListener('click', (e) => {
      const payBtn = e.target.closest('.btn-pay-now');
      if (payBtn) {
        const billId = payBtn.getAttribute('data-bill-id');
        initiatePayment(billId, payBtn);
      }
    });

    // Policy & Billing Card Quick Pay Button
    if (UI.elements.btnQuickPayCard) {
      UI.elements.btnQuickPayCard.addEventListener('click', () => {
        const billId = UI.elements.btnQuickPayCard.getAttribute('data-bill-id');
        if (billId) {
          initiatePayment(billId, UI.elements.btnQuickPayCard);
        } else if (state.bills && state.bills.length > 0) {
          const unpaid = state.bills.find(b => (b.status || '').toLowerCase() === 'unpaid');
          if (unpaid) initiatePayment(unpaid.id, UI.elements.btnQuickPayCard);
        }
      });
    }

    // Mock Mode Toolbar: Add +15 L
    const btnAdd15 = document.getElementById('mock-btn-add-15');
    if (btnAdd15) {
      btnAdd15.addEventListener('click', () => {
        state.totalUsageLitres += 15.0;
        pollData();
      });
    }

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

  function openAuthModal() {
    if (UI.elements.authModal) {
      UI.elements.authModal.classList.remove('hidden');
      if (UI.elements.authErrorMsg) UI.elements.authErrorMsg.style.display = 'none';
    }
  }

  function applyLogin(role, username) {
    state.currentUser = {
      role: role.toLowerCase() === 'admin' ? 'admin' : 'user',
      username: username || (role === 'admin' ? 'Admin' : 'User')
    };
    localStorage.setItem('aquapulse_user', JSON.stringify(state.currentUser));
    if (UI.elements.authModal) {
      UI.elements.authModal.classList.add('hidden');
    }
    UI.renderRole();
    UI.renderBills(state.bills);
    UI.showAlert(`Signed in as ${state.currentUser.role === 'admin' ? 'Administrator' : 'User (' + state.currentUser.username + ')'}`, 'success');
  }

  /* ==========================================================================
     9. Bootstrap Application
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

    // Check if user has an existing saved session; if not, open login modal
    const savedUser = localStorage.getItem('aquapulse_user');
    if (!savedUser) {
      openAuthModal();
    } else {
      if (UI.elements.authModal) UI.elements.authModal.classList.add('hidden');
    }

    UI.renderRole();
    ChartEngine.init();
    setupEventListeners();
    startPolling();
  });

})();
