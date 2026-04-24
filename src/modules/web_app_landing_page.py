LANDING_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab Control Panel</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.5;
        }

        .mono {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }

        /* Status Bar */
        .status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 16px;
            background-color: #000;
            border-bottom: 1px solid var(--border);
            font-size: 0.85rem;
            color: var(--text-muted);
        }

        .status-left { display: flex; align-items: center; gap: 8px; }
        .status-dot {
            width: 8px; height: 8px;
            background-color: #22c55e;
            border-radius: 50%;
            box-shadow: 0 0 8px #22c55e;
        }

        /* Navigation Component */
        .nav-bar {
            display: flex;
            justify-content: center;
            gap: 24px;
            padding: 12px 16px;
            background-color: var(--card-bg);
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
        }

        .nav-bar a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.95rem;
            padding-bottom: 4px;
            transition: color 0.2s ease;
            white-space: nowrap;
        }

        .nav-bar a:hover { color: var(--text); }

        .nav-bar a.active {
            color: var(--accent);
            border-bottom: 2px solid var(--accent);
            font-weight: 500;
        }

        /* Main Content */
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 16px;
        }

        h1 {
            text-align: center;
            font-size: 2.5rem;
            margin: 0 0 32px 0;
            font-weight: 600;
            letter-spacing: -0.02em;
        }

        /* Quick Stats */
        .quick-stats {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 40px;
        }

        .stat-tile {
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }

        .stat-label {
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }

        .stat-value {
            font-size: 1.25rem;
            color: var(--accent);
            font-weight: 500;
        }

        /* Card Grid */
        .card-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 24px;
            margin-bottom: 48px;
        }

        .card {
            display: block;
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            text-decoration: none;
            color: inherit;
            transition: all 0.3s ease;
            opacity: 0;
            animation: fadeIn 0.6s ease forwards;
        }

        .card:hover {
            border-color: var(--accent);
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(125, 211, 252, 0.15);
        }

        .card .emoji {
            font-size: 2.5rem;
            margin-bottom: 16px;
            line-height: 1;
        }

        .card h2 {
            margin: 0 0 8px 0;
            font-size: 1.25rem;
            font-weight: 600;
        }

        .card p {
            margin: 0;
            color: var(--text-muted);
            font-size: 0.95rem;
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(16px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Footer */
        footer {
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
            padding: 32px 0;
            border-top: 1px solid var(--border);
        }

        /* Responsive */
        @media (max-width: 768px) {
            .quick-stats { grid-template-columns: repeat(2, 1fr); }
            .card-grid { grid-template-columns: 1fr; }
            .status-bar { flex-direction: column; gap: 8px; }
        }
    </style>
</head>
<body>

    <!-- STATUS BAR -->
    <div class="status-bar">
        <div class="status-left">
            <div class="status-dot"></div>
            <span>Online</span>
        </div>
        <div class="mono" id="model-name">loading_model...</div>
        <div class="mono" id="clock">00:00:00</div>
    </div>

    <!-- NAV COMPONENT START (Reusable) -->
    <nav class="nav-bar">
        <a href="/" class="active">Главная</a>
        <a href="/stats">Stats</a>
        <a href="/inbox">Inbox</a>
        <a href="/costs">Costs</a>
        <a href="/swarm">Swarm</a>
    </nav>
    <!-- NAV COMPONENT END -->

    <div class="container">
        <header>
            <h1>🦀 Krab Control Panel</h1>
        </header>

        <!-- QUICK STATS -->
        <div class="quick-stats">
            <div class="stat-tile">
                <div class="stat-label">Telegram</div>
                <div class="stat-value mono" id="stat-tg">--</div>
            </div>
            <div class="stat-tile">
                <div class="stat-label">Inbox Open</div>
                <div class="stat-value mono" id="stat-inbox">0</div>
            </div>
            <div class="stat-tile">
                <div class="stat-label">Voice</div>
                <div class="stat-value mono" id="stat-voice">--</div>
            </div>
            <div class="stat-tile">
                <div class="stat-label">Scheduler</div>
                <div class="stat-value mono" id="stat-sched">--</div>
            </div>
        </div>

        <!-- CARD GRID -->
        <div class="card-grid">
            <a href="/stats" class="card" style="animation-delay: 0.1s;">
                <div class="emoji">📊</div>
                <h2>Runtime Stats</h2>
                <p>Лимиты, кэши, голос, инбокс<br>и маршрутизация запросов.</p>
            </a>

            <a href="/inbox" class="card" style="animation-delay: 0.2s;">
                <div class="emoji">📥</div>
                <h2>Inbox</h2>
                <p>Входящие элементы с фильтрами<br>и расширяемыми карточками.</p>
            </a>

            <a href="/costs" class="card" style="animation-delay: 0.3s;">
                <div class="emoji">💰</div>
                <h2>Costs</h2>
                <p>Бюджет, разбивка по моделям<br>и метрики использования.</p>
            </a>

            <a href="/swarm" class="card" style="animation-delay: 0.4s;">
                <div class="emoji">🐝</div>
                <h2>Swarm</h2>
                <p>Мульти-агентные команды,<br>раунды и управление памятью.</p>
            </a>
        </div>

        <footer>
            Krab v8 · <span id="footer-model">—</span> · Session 4
        </footer>
    </div>

    <script>
        // Clock Update
        function updateClock() {
            const now = new Date();
            document.getElementById('clock').textContent = now.toLocaleTimeString('ru-RU', { hour12: false });
        }
        setInterval(updateClock, 1000);
        updateClock();

        // Fetch Health/Lite Data
        async function fetchStats() {
            try {
                const res = await fetch('/api/health/lite');
                if (!res.ok) throw new Error('Network response was not ok');
                const data = await res.json();
                updateUI(data);
            } catch (e) {
                console.warn('Fetch failed, using fallback data for preview.');
                // Fallback на "—" если API недоступен — без хардкода имён моделей
                updateUI({
                    last_runtime_route: { model: "—" },
                    telegram: { session_state: "unknown" },
                    inbox_summary: { open_items: 0 },
                    voice: { enabled: false },
                    scheduler: { enabled: false }
                });
            }
        }

        function updateUI(data) {
            if (data.last_runtime_route && data.last_runtime_route.model) {
                const liveModel = data.last_runtime_route.model;
                document.getElementById('model-name').textContent = liveModel;
                const footerModel = document.getElementById('footer-model');
                if (footerModel) footerModel.textContent = liveModel;
            }

            if (data.telegram) {
                document.getElementById('stat-tg').textContent = data.telegram.session_state || 'unknown';
            }

            if (data.inbox_summary) {
                document.getElementById('stat-inbox').textContent = data.inbox_summary.open_items ?? 0;
            }

            if (data.voice) {
                document.getElementById('stat-voice').textContent = data.voice.enabled ? 'ВКЛ' : 'ВЫКЛ';
            }

            if (data.scheduler) {
                document.getElementById('stat-sched').textContent = data.scheduler.enabled ? 'ВКЛ' : 'ВЫКЛ';
            }
        }

        // Initial fetch and polling
        fetchStats();
        setInterval(fetchStats, 10000);
    </script>
</body>
</html>
"""
