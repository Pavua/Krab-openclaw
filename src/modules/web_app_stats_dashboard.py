STATS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Krab Stats</title>
  <style>
    :root {
      --bg: #0d0d0d;
      --card-bg: #121212;
      --tile-bg: #1a1a1a;
      --border: #2a2a2a;
      --text: #e0e0e0;
      --text-muted: #a0a0a0;
      --accent: #7dd3fc;
      --red: #f87171;
      --red-bg: #7f1d1d;
      --green: #34d399;
      --green-bg: #064e3b;
      --yellow: #fbbf24;
      --yellow-bg: #78350f;
      --orange: #fdba74;
      --orange-bg: #9a3412;
    }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 20px;
      line-height: 1.5;
    }
    h1 {
      font-size: 24px;
      margin-bottom: 24px;
      color: var(--text);
    }
    .mono {
      font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }
    @media (max-width: 1023px) {
      .grid { grid-template-columns: 1fr; }
    }
    .card {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      background-color: var(--card-bg);
    }
    .card-title {
      color: var(--accent);
      font-size: 16px;
      margin-top: 0;
      margin-bottom: 16px;
      font-weight: 600;
    }
    .progress-bg {
      background-color: var(--tile-bg);
      border-radius: 4px;
      height: 8px;
      width: 100%;
      overflow: hidden;
      margin: 8px 0;
    }
    .progress-bar {
      height: 100%;
      transition: width 0.3s ease, background-color 0.3s ease;
    }
    .tile-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }
    @media (max-width: 600px) {
      .tile-grid { grid-template-columns: repeat(2, 1fr); }
    }
    .tile {
      background-color: var(--tile-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      display: flex;
      flex-direction: column;
    }
    .tile.alert {
      border-color: var(--red);
    }
    .tile-label {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 4px;
    }
    .tile-value {
      font-size: 20px;
      font-weight: bold;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      background-color: var(--border);
      font-size: 12px;
      margin: 2px 4px 2px 0;
    }
    .badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: bold;
      margin-left: 8px;
    }
    .badge-green { background: var(--green-bg); color: var(--green); }
    .badge-yellow { background: var(--yellow-bg); color: var(--yellow); }
    .badge-red { background: var(--red-bg); color: var(--red); }
    .badge-orange { background: var(--orange-bg); color: var(--orange); }
    .error { color: var(--yellow); font-size: 14px; }
    .footer {
      margin-top: 24px;
      font-size: 12px;
      color: var(--text-muted);
      text-align: center;
    }
    .flex-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }
  </style>
</head>
<body>
  <h1>Krab Runtime Stats</h1>
  <div class="grid">
    <div class="card">
      <h2 class="card-title">Telegram API Лимит</h2>
      <div id="tg-content"><span class="error">Загрузка...</span></div>
    </div>

    <div class="card">
      <h2 class="card-title">Кэш банов и возможностей</h2>
      <div id="cache-content"><span class="error">Загрузка...</span></div>
    </div>

    <div class="card">
      <h2 class="card-title">Голосовой Runtime</h2>
      <div id="voice-content"><span class="error">Загрузка...</span></div>
    </div>

    <div class="card">
      <h2 class="card-title">Статус Inbox</h2>
      <div id="inbox-content"><span class="error">Загрузка...</span></div>
    </div>

    <div class="card">
      <h2 class="card-title">OpenClaw Маршрутизация</h2>
      <div id="openclaw-content"><span class="error">Загрузка...</span></div>
    </div>
  </div>
  
  <div class="footer" id="timestamp">Last update: --:--:--</div>

  <script>
    async function fetchSafe(url) {
      try {
        const res = await fetch(url);
        if (!res.ok) return null;
        return await res.json();
      } catch (e) {
        return null;
      }
    }

    function renderError(elementId) {
      document.getElementById(elementId).innerHTML = '<div class="error">⚠️ unavailable</div>';
    }

    async function updateDashboard() {
      const now = new Date();
      document.getElementById('timestamp').innerText = 'Last update: ' + now.toLocaleTimeString('ru-RU');

      const tgData = await fetchSafe('/api/health/lite');
      if (tgData && tgData.telegram_rate_limiter) {
        const tg = tgData.telegram_rate_limiter;
        const pct = Math.min(100, (tg.current_in_window / tg.max_per_sec) * 100);
        const barColor = pct > 80 ? 'var(--red)' : 'var(--accent)';
        document.getElementById('tg-content').innerHTML = `
          <div class="flex-row">
            <span>Лимит: <span class="mono">${tg.max_per_sec}</span> req/s</span>
            <span>В окне: <span class="mono">${tg.current_in_window}</span></span>
          </div>
          <div class="progress-bg">
            <div class="progress-bar" style="width: ${pct}%; background-color: ${barColor};"></div>
          </div>
          <div style="margin-top: 12px; font-size: 14px; color: var(--text-muted);">
            Всего acquired: <span class="mono">${tg.total_acquired}</span><br>
            Всего waited: <span class="mono">${tg.total_waited}</span> (<span class="mono">${(tg.total_wait_sec || 0).toFixed(3)}</span>s)
          </div>
        `;
      } else {
        renderError('tg-content');
      }

      const cacheData = await fetchSafe('/api/stats/caches');
      if (cacheData) {
        let banBadge = cacheData.ban_cache_count > 0 ? '<span class="badge badge-orange">Active</span>' : '';
        let voiceBadge = cacheData.voice_blocked_count > 0 ? '<span class="badge badge-yellow">Blocked</span>' : '';
        document.getElementById('cache-content').innerHTML = `
          <div class="tile-grid" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 12px;">
            <div class="tile">
              <div class="tile-label">Ban Cache ${banBadge}</div>
              <div class="tile-value mono">${cacheData.ban_cache_count}</div>
            </div>
            <div class="tile">
              <div class="tile-label">Capability Cache</div>
              <div class="tile-value mono">${cacheData.capability_cache_count}</div>
            </div>
            <div class="tile">
              <div class="tile-label">Voice Blocked ${voiceBadge}</div>
              <div class="tile-value mono">${cacheData.voice_blocked_count}</div>
            </div>
          </div>
          <div style="font-size: 14px; color: var(--text-muted);">
            Voice явно запрещён: <span class="mono">${cacheData.capability_voice_disallowed}</span><br>
            Slow mode активен: <span class="mono">${cacheData.capability_slow_mode}</span>
          </div>
        `;
      } else {
        renderError('cache-content');
      }

      const voiceRaw = await fetchSafe('/api/voice/runtime');
      const voiceData = voiceRaw ? (voiceRaw.voice || voiceRaw) : null;
      if (voiceData && typeof voiceData === 'object') {
        const statusColor = voiceData.enabled ? 'var(--green)' : 'var(--text-muted)';
        const statusText = voiceData.enabled ? 'ВКЛ' : 'ВЫКЛ';
        const pills = (voiceData.blocked_chats || []).map(c => `<span class="pill mono">${c}</span>`).join('') || '<span class="text-muted" style="font-size:14px;">нет</span>';
        
        document.getElementById('voice-content').innerHTML = `
          <div class="flex-row" style="margin-bottom: 12px;">
            <span style="font-size: 16px;">Озвучка: <strong style="color: ${statusColor}">${statusText}</strong></span>
            <span style="font-size: 14px; color: var(--text-muted);">Delivery: <span class="mono">${voiceData.delivery}</span></span>
          </div>
          <div style="font-size: 14px; margin-bottom: 12px;">
            Скорость: <span class="mono">${voiceData.speed}</span> | Голос: <span class="mono">${typeof voiceData.voice === 'string' ? voiceData.voice : 'N/A'}</span>
          </div>
          <div style="font-size: 14px; color: var(--text-muted); margin-bottom: 4px;">Заблокированные чаты:</div>
          <div>${pills}</div>
        `;
      } else {
        renderError('voice-content');
      }

      const inboxRaw = await fetchSafe('/api/inbox/status');
      const inboxData = inboxRaw ? (inboxRaw.summary || inboxRaw) : null;
      if (inboxData) {
        const attClass = inboxData.attention_items > 0 ? 'alert' : '';
        document.getElementById('inbox-content').innerHTML = `
          <div class="tile-grid">
            <div class="tile"><div class="tile-label">Total</div><div class="tile-value mono">${inboxData.total_items}</div></div>
            <div class="tile"><div class="tile-label">Open</div><div class="tile-value mono">${inboxData.open_items}</div></div>
            <div class="tile"><div class="tile-label">Fresh</div><div class="tile-value mono">${inboxData.fresh_open_items}</div></div>
            <div class="tile"><div class="tile-label">Stale</div><div class="tile-value mono">${inboxData.stale_open_items}</div></div>
            <div class="tile ${attClass}"><div class="tile-label">Attention</div><div class="tile-value mono">${inboxData.attention_items}</div></div>
            <div class="tile"><div class="tile-label">Pending</div><div class="tile-value mono">${inboxData.pending_approvals}</div></div>
            <div class="tile"><div class="tile-label">New Owner</div><div class="tile-value mono">${inboxData.new_owner_requests}</div></div>
          </div>
        `;
      } else {
        renderError('inbox-content');
      }

      // OpenClaw: берём routing info из tgData.last_runtime_route (уже fetched)
      if (tgData && tgData.last_runtime_route) {
        const route = tgData.last_runtime_route;
        const model = route.model || 'N/A';
        const provider = route.provider || '?';
        const routeStatus = (route.status || 'unknown').toLowerCase();
        let badgeClass = 'badge-green';
        if (routeStatus.includes('error') || routeStatus.includes('fail')) badgeClass = 'badge-red';
        else if (routeStatus !== 'ok') badgeClass = 'badge-yellow';
        document.getElementById('openclaw-content').innerHTML = `
          <div style="margin-bottom: 12px; font-size: 14px;">
            <span style="color: var(--text-muted);">Active model:</span> <span class="mono">${model}</span><br>
            <span style="color: var(--text-muted);">Provider:</span> <span class="mono">${provider}</span><br>
            <span style="color: var(--text-muted);">Route:</span> <span class="mono">${route.route_reason || 'N/A'}</span>
          </div>
          <div class="flex-row" style="justify-content: flex-start; font-size: 14px;">
            <span style="color: var(--text-muted);">Status:</span>
            <span class="badge ${badgeClass}">${routeStatus}</span>
          </div>
        `;
      } else {
        renderError('openclaw-content');
      }
    }

    updateDashboard();
    setInterval(updateDashboard, 5000);
  </script>
</body>
</html>
"""