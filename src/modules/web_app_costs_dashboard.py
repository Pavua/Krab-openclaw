COSTS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Аналитика расходов</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #9ca3af;
            --accent: #7dd3fc;
            --success: #34d399;
            --danger: #ef4444;
            --local: #6b7280;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: system-ui, -apple-system, sans-serif;
            padding: 16px;
            max-width: 800px;
            margin: 0 auto;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }

        /* Status Bar */
        .status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            font-size: 14px;
            color: var(--text-muted);
        }
        .status-left { display: flex; align-items: center; gap: 8px; }
        .dot {
            width: 8px; height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--success);
        }
        .status-title { color: var(--text); font-weight: 600; }

        /* Cards */
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 16px;
        }
        h2 { font-size: 16px; font-weight: 600; }

        /* Budget */
        .budget-amounts {
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 16px;
            color: var(--text);
        }
        .progress-track {
            background: #222;
            border-radius: 6px;
            height: 16px;
            position: relative;
            overflow: hidden;
        }
        .progress-fill {
            position: absolute;
            top: 0; left: 0; bottom: 0;
            width: 100%;
            background: linear-gradient(90deg, var(--success) 0%, #eab308 50%, var(--danger) 100%);
            transform-origin: left;
            transform: scaleX(0);
            transition: transform 0.8s ease-out;
        }

        /* Stacked Bar */
        .stacked-bar {
            display: flex;
            height: 24px;
            border-radius: 6px;
            overflow: hidden;
            margin-bottom: 16px;
            background: #222;
        }
        .stacked-segment {
            height: 100%;
            transition: width 0.8s ease-out;
        }

        /* Legend */
        .legend-grid { display: flex; flex-direction: column; gap: 8px; }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 12px;
            background: rgba(255,255,255,0.03);
            padding: 12px;
            border-radius: 6px;
            border: 1px solid var(--border);
        }
        .legend-color {
            width: 14px; height: 14px;
            border-radius: 4px;
            flex-shrink: 0;
        }
        .legend-info { flex-grow: 1; display: flex; flex-direction: column; gap: 4px; }
        .legend-name { font-weight: 600; font-size: 14px; }
        .legend-stats { font-size: 13px; color: var(--text-muted); }

        /* Metrics */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }
        .metric-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .metric-title {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .metric-value {
            font-size: 24px;
            font-weight: 600;
            color: var(--accent);
        }

        @media (max-width: 480px) {
            .budget-amounts { font-size: 20px; }
            .metrics-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
<nav style="background:#121212;border-bottom:1px solid #2a2a2a;padding:8px 20px;display:flex;gap:20px;align-items:center;font-size:14px;position:sticky;top:0;z-index:100;"><a href="/" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">🦀 Главная</a><a href="/stats" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">📊 Stats</a><a href="/inbox" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">📥 Inbox</a><a href="/costs" style="color:#7dd3fc;text-decoration:none;border-bottom:2px solid #7dd3fc;padding-bottom:2px;">💰 Costs</a><a href="/swarm" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">🐝 Swarm</a></nav>


    <div class="status-bar">
        <div class="status-left"><span class="dot"></span> Online</div>
        <div class="status-title">Аналитика расходов</div>
        <div id="clock" class="mono">00:00:00</div>
    </div>

    <div class="card">
        <div class="card-header">
            <h2>Бюджет</h2>
            <span id="period" class="text-muted">Загрузка...</span>
        </div>
        <div class="budget-amounts mono" id="budget-text">$0.00 / $0.00 (0.00%)</div>
        <div class="progress-track">
            <div id="budget-fill" class="progress-fill"></div>
        </div>
    </div>

    <div class="card">
        <h2>Расход по моделям</h2>
        <div style="margin-top: 16px;">
            <div id="stacked-bar" class="stacked-bar"></div>
            <div id="model-legend" class="legend-grid"></div>
        </div>
    </div>

    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-title">Вызовы</div>
            <div class="metric-value mono" id="val-calls">0</div>
        </div>
        <div class="metric-card">
            <div class="metric-title">Средняя стоимость</div>
            <div class="metric-value mono" id="val-avg">$0.0000</div>
        </div>
        <div class="metric-card">
            <div class="metric-title">Входящие токены</div>
            <div class="metric-value mono" id="val-in">0</div>
        </div>
        <div class="metric-card">
            <div class="metric-title">Исходящие токены</div>
            <div class="metric-value mono" id="val-out">0</div>
        </div>
    </div>

    <script>
        // Clock
        function updateClock() {
            document.getElementById('clock').textContent = new Date().toLocaleTimeString('ru-RU');
        }
        setInterval(updateClock, 1000);
        updateClock();

        // Formatters
        function formatK(num) {
            return num >= 1000 ? (num / 1000).toFixed(1).replace('.0', '') + 'K' : num;
        }

        function formatDate(iso) {
            const d = new Date(iso);
            const months = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'];
            return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
        }

        function getModelColor(name) {
            const n = name.toLowerCase();
            if (n.includes('pro')) return 'var(--accent)';
            if (n.includes('flash')) return 'var(--success)';
            if (n.includes('local') || n.includes('qwen')) return 'var(--local)';
            return '#f59e0b';
        }

        // Fetch & Render
        async function fetchCosts() {
            try {
                const res = await fetch('/api/costs/report');
                if (!res.ok) throw new Error('HTTP error');
                const data = await res.json();
                if (!data.ok || !data.report) throw new Error('API error');
                render(data.report);
            } catch (err) {
                document.getElementById('budget-text').innerHTML = '⚠️ недоступно';
                console.error('Failed to load costs:', err);
            }
        }

        function render(r) {
            // Budget
            document.getElementById('period').textContent = `${formatDate(r.period_start)} — ${formatDate(r.period_end)}`;
            document.getElementById('budget-text').textContent = `$${r.total_cost_usd.toFixed(2)} / $${r.budget_monthly_usd.toFixed(2)} (${r.budget_used_pct}%)`;
            document.getElementById('budget-fill').style.transform = `scaleX(${Math.min(r.budget_used_pct / 100, 1)})`;

            // Metrics
            document.getElementById('val-calls').textContent = r.total_calls;
            const avg = r.total_calls > 0 ? r.total_cost_usd / r.total_calls : 0;
            document.getElementById('val-avg').textContent = `$${avg.toFixed(4)}`;

            // Models
            const models = Object.entries(r.by_model).sort((a, b) => b[1].cost_usd - a[1].cost_usd);
            let barHtml = '';
            let legendHtml = '';
            let inTok = 0, outTok = 0;

            models.forEach(([name, stats]) => {
                inTok += stats.input_tokens;
                outTok += stats.output_tokens;

                const color = getModelColor(name);
                const costPct = r.total_cost_usd > 0 ? (stats.cost_usd / r.total_cost_usd) * 100 : 0;

                if (costPct > 0) {
                    barHtml += `<div class="stacked-segment" style="width: ${costPct}%; background: ${color};"></div>`;
                }

                legendHtml += `
                    <div class="legend-item">
                        <div class="legend-color" style="background: ${color}"></div>
                        <div class="legend-info">
                            <div class="legend-name">${name}</div>
                            <div class="legend-stats mono">Вызовы: ${stats.calls} | $${stats.cost_usd.toFixed(2)} | Токены: ${formatK(stats.input_tokens + stats.output_tokens)}</div>
                        </div>
                    </div>
                `;
            });

            // Fallback for local models if total cost is 0 but there are calls
            if (r.total_cost_usd === 0 && r.total_calls > 0) {
                models.forEach(([name, stats]) => {
                    const color = getModelColor(name);
                    const callPct = (stats.calls / r.total_calls) * 100;
                    barHtml += `<div class="stacked-segment" style="width: ${callPct}%; background: ${color};"></div>`;
                });
            }

            document.getElementById('stacked-bar').innerHTML = barHtml || '<div style="width:100%; background:#333;"></div>';
            document.getElementById('model-legend').innerHTML = legendHtml;

            document.getElementById('val-in').textContent = formatK(inTok);
            document.getElementById('val-out').textContent = formatK(outTok);
        }

        // Init
        fetchCosts();
        setInterval(fetchCosts, 30000);
    </script>
</body>
</html>"""
