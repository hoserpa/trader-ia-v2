const { createApp, ref, computed, onMounted, onUnmounted, nextTick } = Vue;

const API_BASE = '/api';
const WS_URL = `ws://${location.host}/ws/live`;
const api = axios.create({ baseURL: API_BASE });

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
    const gridState = ref({ enabled: false, running: false, pairs: {}, config: {} });
    const historyDays = ref(30);
    const logContainer = ref(null);
    const configModalOpen = ref(false);
    const configForm = ref({});
    const configGroups = ref([]);
    const configSaving = ref(false);
    const configSaved = ref(false);
    const configFieldsMeta = ref([]);
    const chartPairs = ref([]);
    const chartPair = ref('');
    const chartTimeframes = ref(['15m', '1h', '4h']);
    const chartTimeframe = ref('15m');
    const chartDays = ref(30);
    let portfolioChart = null;
    let priceChart = null;
    let priceSeries = null;
    let priceCandles = [];
    let ws = null;
    let wsReconnectTimer = null;

    const connectWS = () => {
      ws = new WebSocket(WS_URL);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'portfolio_update') portfolio.value = msg.data;
        else if (msg.type === 'bot_status') botStatus.value = msg.data;
        else if (msg.type === 'price_update') prices.value[msg.data.pair] = msg.data.price;
        else if (msg.type === 'candle') { updateLiveCandle(msg.data); if (botStatus.value.status === 'error') botStatus.value.status = 'running'; }
        else if (msg.type === 'signal') { updateSignal(msg.data); if (botStatus.value.status === 'error') botStatus.value.status = 'running'; }
        else if (msg.type === 'trade_executed') { loadTrades(); if (botStatus.value.status === 'error') botStatus.value.status = 'running'; }
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
        const [portRes, tradesRes, statsRes, sigRes, configRes, logsRes, pricesRes, gridRes] = await Promise.all([
          api.get('/portfolio'),
          api.get('/trades/operations?limit=50'),
          api.get('/trades/stats'),
          api.get('/market/signals'),
          api.get('/bot/config'),
          api.get('/logs?limit=100'),
          api.get('/market/prices'),
          api.get('/bot/grid'),
        ]);
        portfolio.value = portRes.data;
        trades.value = tradesRes.data;
        stats.value = statsRes.data;
        latestSignals.value = dedupSignals(sigRes.data);
        botConfig.value = configRes.data;
        systemLogs.value = logsRes.data;
        prices.value = pricesRes.data;
        if (gridRes.data.enabled) gridState.value = gridRes.data;
      } catch (e) { console.error('Error cargando datos:', e); }
    };

    const loadTrades = async () => {
      const res = await api.get('/trades/operations?limit=50');
      trades.value = res.data;
    };

    const loadPortfolioHistory = async (days) => {
      historyDays.value = days;
      const res = await api.get(`/portfolio/history?days=${days}`);
      renderPortfolioChart(res.data);
    };

    const resetPortfolio = async () => {
      if (!confirm('⚠️ Reset COMPLETO: Se borrarán TODOS los datos (trades, posiciones, historial, balance). ¿Continuar?')) return;
      try {
        const res = await api.post('/portfolio/reset-full');
        if (res.data.error) {
          alert(res.data.error);
          return;
        }
        const defaults = { total_value_eur: 100, balance_eur: 100, total_pnl_eur: 0, total_pnl_pct: 0, positions: {} };
        portfolio.value = defaults;
        trades.value = [];
        latestSignals.value = [];
        stats.value = {};
        const portRes = await api.get('/portfolio');
        if (!portRes.data.error) portfolio.value = portRes.data;
        trades.value = (await api.get('/trades/operations?limit=50')).data;
        latestSignals.value = (await api.get('/market/signals')).data;
        stats.value = (await api.get('/trades/stats')).data;
        await loadPortfolioHistory(historyDays.value);
        alert('✓ Reset completo: ' + res.data.trades_deleted + ' trades, ' + res.data.positions_deleted + ' posiciones, ' + res.data.snapshots_deleted + ' snapshots borrados');
      } catch (e) { alert('Error al resetear: ' + e.message); }
    };

    const loadConfig = async () => {
      try {
        const res = await api.get('/config');
        const fields = res.data.fields;
        configFieldsMeta.value = fields;
        const form = {};
        fields.forEach(f => { form[f.key] = f.current; });
        configForm.value = form;
        const groups = [];
        const sections = { risk: 'Gestión de Riesgo', trading: 'Trading' };
        const sectionMap = {};
        fields.forEach(f => {
          const label = sections[f.section] || f.section;
          if (!sectionMap[label]) { sectionMap[label] = { label, fields: [] }; groups.push(sectionMap[label]); }
          sectionMap[label].fields.push(f);
        });
        configGroups.value = groups;
      } catch (e) { console.error('Error cargando config:', e); }
    };

    const openConfig = async () => {
      configModalOpen.value = true;
      configSaved.value = false;
      await loadConfig();
    };

    const closeConfig = () => {
      configModalOpen.value = false;
    };

    const saveConfig = async () => {
      configSaving.value = true;
      configSaved.value = false;
      try {
        const keys = Object.keys(configForm.value);
        for (const key of keys) {
          await api.put(`/config/${key}`, { value: configForm.value[key] });
        }
        configSaved.value = true;
        setTimeout(() => configSaved.value = false, 3000);
      } catch (e) { alert('Error guardando configuración: ' + e.message); }
      finally { configSaving.value = false; }
    };

    const restoreField = async (key) => {
      try {
        await api.delete(`/config/${key}`);
        await loadConfig();
      } catch (e) { alert('Error restaurando campo: ' + e.message); }
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

    const toPriceTime = (iso) => Math.floor(new Date(iso).getTime() / 1000);

    const renderPriceChart = (candles) => {
      const el = document.getElementById('priceChart');
      if (!el) return;
      el.classList.toggle('empty', !candles.length);
      if (priceChart) priceChart.remove();
      const chart = LightweightCharts.createChart(el, {
        autoSize: true,
        layout: { background: { color: '#1c1f26' }, textColor: '#9ca3af' },
        grid: { vertLines: { color: '#2d333b' }, horzLines: { color: '#2d333b' } },
        rightPriceScale: { borderColor: '#2d333b' },
        timeScale: { borderColor: '#2d333b' },
        localization: { priceFormatter: p => p.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
      });
      const series = chart.addCandlestickSeries({
        upColor: '#4ade80', downColor: '#f87171',
        borderUpColor: '#4ade80', borderDownColor: '#f87171',
        wickUpColor: '#4ade80', wickDownColor: '#f87171',
      });
      priceCandles = candles.map(c => ({
        time: toPriceTime(c.timestamp), open: c.open, high: c.high,
        low: c.low, close: c.close,
      }));
      series.setData(priceCandles);
      chart.timeScale().fitContent();
      priceChart = chart;
      priceSeries = series;
    };

    const loadPriceChart = async () => {
      if (!chartPair.value) return;
      const perDay = chartTimeframe.value === '15m' ? 96 : chartTimeframe.value === '1h' ? 24 : 6;
      const limit = Math.min(chartDays.value * perDay, 10000);
      try {
        const res = await api.get(`/market/candles`, { params: { pair: chartPair.value, timeframe: chartTimeframe.value, days: chartDays.value, limit } });
        renderPriceChart(res.data);
      } catch (e) { console.error('Error cargando velas:', e); }
    };

    const selectChartPair = (p) => { chartPair.value = p; loadPriceChart(); };
    const selectChartTimeframe = (tf) => { chartTimeframe.value = tf; loadPriceChart(); };
    const selectChartRange = (d) => { chartDays.value = d; loadPriceChart(); };

    const updateLiveCandle = (data) => {
      if (!priceSeries || !priceCandles.length) return;
      // Solo la vela nativa (15m) se actualiza en vivo; los timeframes agregados se recargan.
      if (chartTimeframe.value !== '15m') return;
      if (data.pair !== chartPair.value) return;
      const t = toPriceTime(data.timestamp || new Date().toISOString());
      const last = priceCandles[priceCandles.length - 1];
      if (t === last.time) {
        last.high = Math.max(last.high, data.close);
        last.low = Math.min(last.low, data.close);
        last.close = data.close;
        priceSeries.update(last);
      } else if (t > last.time) {
        const bar = { time: t, open: last.close, high: data.close, low: data.close, close: data.close };
        priceCandles.push(bar);
        priceSeries.update(bar);
      }
    };

    const formatPrice = (v) => v != null ? Number(v).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
    const formatDate = (ts) => new Date(ts).toLocaleString('es-ES');
    const isBuy = (side) => side === 'buy' || side === 'buy_to_close';
    const tradeColor = (t) => {
      if (isBuy(t.side)) return 'buy';
      return 'sell';
    };
    const opSideLabel = (t) => {
      if (t.status === 'open') return 'ABIERTA';
      return isBuy(t.side) ? 'LARGO' : 'CORTO';
    };
    const badgeClass = (t) => {
      if (t.status === 'open') return 'badge-open';
      return isBuy(t.side) ? 'badge-buy' : 'badge-sell';
    };
    const pnlClass = (t) => {
      if (t.pnl_eur == null) return '';
      if (t.pnl_eur > 0) return 'positive';
      if (t.pnl_eur < 0) return 'negative';
      return 'positive-neutral';
    };
    const formatPnl = (v) => {
      if (v == null) return '—';
      const n = Number(v);
      const s = n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      return (n > 0 ? '+' : '') + s + '€';
    };
    const modeClass = computed(() => botStatus.value.mode === 'real' ? 'badge-real' : 'badge-demo');
    const statusClass = computed(() => ({ 
      'dot-green': botStatus.value.status === 'running', 
      'dot-red': botStatus.value.status === 'error', 
      'dot-yellow': botStatus.value.status === 'starting',
      'dot-gray': botStatus.value.status !== 'running' && botStatus.value.status !== 'error' && botStatus.value.status !== 'starting'
    }));
    const statusText = computed(() => {
      const s = botStatus.value.status;
      if (s === 'starting') return 'Iniciando...';
      if (s === 'running') return 'Ejecutando';
      if (s === 'error') return 'Error';
      if (s === 'connecting') return 'Conectando...';
      return s || 'Desconocido';
    });
    const statusTextClass = computed(() => {
      const s = botStatus.value.status;
      if (s === 'starting') return 'starting';
      if (s === 'error') return 'error';
      return '';
    });
    const openPositions = computed(() => Object.keys(portfolio.value.positions || {}));
    const signalClass = (s) => ({ 'badge-buy': s === 'BUY', 'badge-sell': s === 'SELL', 'badge-hold': s === 'HOLD' });

    onMounted(async () => {
      await loadAll();
      await loadPortfolioHistory(30);
      chartPairs.value = Object.keys(prices.value) || [];
      chartPair.value = chartPairs.value[0] || '';
      await loadPriceChart();
      connectWS();
      setInterval(loadAll, 60000);
      setInterval(loadPriceChart, 60000);
    });

    onUnmounted(() => {
      if (ws) ws.close();
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    });

    return {
      portfolio, botStatus, botConfig, prices, trades, systemLogs,
      latestSignals, stats, gridState, historyDays, logContainer, openPositions,
      formatPrice, formatDate, modeClass, statusClass, statusTextClass, signalClass,
      tradeColor, badgeClass, pnlClass, formatPnl, opSideLabel,
      loadPortfolioHistory, resetPortfolio,
      chartPairs, chartPair, chartTimeframes, chartTimeframe, chartDays,
      selectChartPair, selectChartTimeframe, selectChartRange,
      configModalOpen, configForm, configGroups, configSaving, configSaved,
      openConfig, closeConfig, saveConfig, restoreField,
    };
  }
}).mount('#app');
