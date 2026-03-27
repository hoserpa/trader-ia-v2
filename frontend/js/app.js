const { createApp, ref, computed, onMounted, onUnmounted, nextTick } = Vue;

const API_BASE = '/api';
const WS_URL = `ws://${location.host}/ws/live`;

createApp({
  setup() {
    const portfolio = ref({ total_value_eur: 0, balance_eur: 0, total_pnl_eur: 0, total_pnl_pct: 0, positions: {} });
    const botStatus = ref({ status: 'connecting', mode: 'demo' });
    const botConfig = ref({ risk: {} });
    const prices = ref({});
    const trades = ref([]);
    const systemLogs = ref([]);
    const latestSignals = ref([]);
    const stats = ref({});
    const historyDays = ref(30);
    const logContainer = ref(null);
    let portfolioChart = null;
    let ws = null;
    let wsReconnectTimer = null;

    const connectWS = () => {
      ws = new WebSocket(WS_URL);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'portfolio_update') portfolio.value = msg.data;
        else if (msg.type === 'bot_status') botStatus.value = msg.data;
        else if (msg.type === 'price_update') prices.value[msg.data.pair] = msg.data.price;
        else if (msg.type === 'signal') updateSignal(msg.data);
        else if (msg.type === 'trade_executed') { trades.value.unshift(msg.data); loadTrades(); }
      };
      ws.onclose = () => {
        wsReconnectTimer = setTimeout(connectWS, 5000);
      };
    };

    const updateSignal = (data) => {
      const idx = latestSignals.value.findIndex(s => s.pair === data.pair);
      if (idx >= 0) latestSignals.value[idx] = data;
      else latestSignals.value.push(data);
    };

    const loadAll = async () => {
      try {
        const [portRes, tradesRes, statsRes, sigRes, configRes, logsRes, pricesRes] = await Promise.all([
          api.get('/portfolio'),
          api.get('/trades?limit=50'),
          api.get('/trades/stats'),
          api.get('/market/signals'),
          api.get('/bot/config'),
          api.get('/logs?limit=100'),
          api.get('/market/prices'),
        ]);
        portfolio.value = portRes.data;
        trades.value = tradesRes.data;
        stats.value = statsRes.data;
        latestSignals.value = dedupSignals(sigRes.data);
        botConfig.value = configRes.data;
        systemLogs.value = logsRes.data;
        prices.value = pricesRes.data;
      } catch (e) { console.error('Error cargando datos:', e); }
    };

    const loadTrades = async () => {
      const res = await api.get('/trades?limit=50');
      trades.value = res.data;
    };

    const loadPortfolioHistory = async (days) => {
      historyDays.value = days;
      const res = await api.get(`/portfolio/history?days=${days}`);
      renderPortfolioChart(res.data);
    };

    const dedupSignals = (signals) => {
      const map = {};
      signals.forEach(s => { if (!map[s.pair] || s.timestamp > map[s.pair].timestamp) map[s.pair] = s; });
      return Object.values(map);
    };

    const renderPortfolioChart = (history) => {
      const ctx = document.getElementById('portfolioChart');
      if (!ctx) return;
      if (portfolioChart) portfolioChart.destroy();
      portfolioChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: history.map(h => new Date(h.timestamp).toLocaleDateString('es-ES')),
          datasets: [{
            label: 'Portfolio (€)',
            data: history.map(h => h.total_value_eur),
            borderColor: '#4f8ef7',
            backgroundColor: 'rgba(79,142,247,0.08)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { y: { ticks: { callback: v => v.toFixed(0) + '€' } } }
        }
      });
    };

    const formatPrice = (v) => v != null ? Number(v).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
    const formatDate = (ts) => new Date(ts).toLocaleString('es-ES');
    const modeClass = computed(() => botStatus.value.mode === 'real' ? 'badge-real' : 'badge-demo');
    const statusClass = computed(() => ({ 
      'dot-green': botStatus.value.status === 'running', 
      'dot-red': botStatus.value.status === 'error', 
      'dot-yellow': botStatus.value.status === 'starting',
      'dot-gray': botStatus.value.status !== 'running' && botStatus.value.status !== 'error' && botStatus.value.status !== 'starting'
    }));
    const statusTextClass = computed(() => botStatus.value.status === 'starting' ? 'starting' : '');
    const openPositions = computed(() => Object.keys(portfolio.value.positions || {}));
    const signalClass = (s) => ({ 'badge-buy': s === 'BUY', 'badge-sell': s === 'SELL', 'badge-hold': s === 'HOLD' });

    onMounted(async () => {
      await loadAll();
      await loadPortfolioHistory(30);
      connectWS();
      setInterval(loadAll, 60000);
    });

    onUnmounted(() => {
      if (ws) ws.close();
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    });

    return {
      portfolio, botStatus, botConfig, prices, trades, systemLogs,
      latestSignals, stats, historyDays, logContainer, openPositions,
      formatPrice, formatDate, modeClass, statusClass, statusTextClass, signalClass,
      loadPortfolioHistory,
    };
  }
}).mount('#app');
