LANDING_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab Owner Panel</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
            --ok: #22c55e;
            --warn: #facc15;
            --err: #f87171;
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
            background-color: var(--ok);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--ok);
        }

        /* Navigation Component */
        .nav-bar {
            display: flex;
            justify-content: center;
            gap: 20px;
            padding: 12px 16px;
            background-color: var(--card-bg);
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
            flex-wrap: wrap;
        }

        .nav-bar a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.92rem;
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
            max-width: 1100px;
            margin: 0 auto;
            padding: 32px 16px 8px 16px;
        }

        /* Hero Section */
        .hero {
            text-align: center;
            margin-bottom: 32px;
        }

        h1 {
            font-size: 2.2rem;
            margin: 0 0 12px 0;
            font-weight: 600;
            letter-spacing: -0.02em;
        }

        .hero-badges {
            display: inline-flex;
            gap: 12px;
            flex-wrap: wrap;
            justify-content: center;
            margin-top: 4px;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 6px 14px;
            font-size: 0.85rem;
            color: var(--text-muted);
        }

        .badge .label { color: var(--text-muted); }
        .badge .value { color: var(--accent); font-weight: 500; }

        /* Quick Stats */
        .quick-stats {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            margin-bottom: 36px;
        }

        .stat-tile {
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }

        .stat-label {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }

        .stat-value {
            font-size: 1.1rem;
            color: var(--accent);
            font-weight: 500;
        }

        /* Card Grid — 3 columns */
        .card-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 18px;
            margin-bottom: 40px;
        }

        .card {
            display: block;
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            text-decoration: none;
            color: inherit;
            transition: all 0.25s ease;
            position: relative;
            opacity: 0;
            animation: fadeIn 0.6s ease forwards;
        }

        .card:hover {
            border-color: var(--accent);
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(125, 211, 252, 0.15);
        }

        .card .emoji {
            font-size: 2rem;
            margin-bottom: 10px;
            line-height: 1;
        }

        .card h2 {
            margin: 0 0 6px 0;
            font-size: 1.1rem;
            font-weight: 600;
        }

        .card p {
            margin: 0;
            color: var(--text-muted);
            font-size: 0.9rem;
            line-height: 1.45;
        }

        .card .indicator {
            position: absolute;
            top: 16px;
            right: 16px;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--text-muted);
        }

        .card .indicator.ok { background-color: var(--ok); box-shadow: 0 0 6px var(--ok); }
        .card .indicator.warn { background-color: var(--warn); }
        .card .indicator.err { background-color: var(--err); }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Footer */
        footer {
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
            padding: 24px 0;
            border-top: 1px solid var(--border);
            margin-top: 24px;
        }

        footer a {
            color: var(--text-muted);
            text-decoration: none;
            margin: 0 10px;
            transition: color 0.2s ease;
        }

        footer a:hover { color: var(--accent); }

        footer .separator { color: var(--border); }

        /* Responsive */
        @media (max-width: 1024px) {
            .card-grid { grid-template-columns: repeat(2, 1fr); }
        }

        @media (max-width: 768px) {
            .quick-stats { grid-template-columns: repeat(2, 1fr); }
            .card-grid { grid-template-columns: 1fr; }
            .status-bar { flex-direction: column; gap: 8px; }
            h1 { font-size: 1.7rem; }
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
        <a href="/admin/models">Models</a>
        <a href="/admin/routing">Routing</a>
        <a href="/admin/swarm">Swarm</a>
        <a href="/admin/costs">Costs</a>
        <a href="/stats">Stats</a>
        <a href="/inbox">Inbox</a>
    </nav>
    <!-- NAV COMPONENT END -->

    <div class="container">

        <!-- HERO -->
        <header class="hero">
            <h1>🦀 Krab Owner Panel</h1>
            <div class="hero-badges">
                <div class="badge">
                    <span class="label">Model:</span>
                    <span class="value mono" id="hero-model">—</span>
                </div>
                <div class="badge">
                    <span class="label">Uptime:</span>
                    <span class="value mono" id="hero-uptime">—</span>
                </div>
                <div class="badge">
                    <span class="label">Session:</span>
                    <span class="value mono">47</span>
                </div>
            </div>
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

        <!-- ADMIN CARD GRID — 10 cards к admin pages -->
        <div class="card-grid">
            <a href="/admin/models" class="card" style="animation-delay: 0.05s;">
                <div class="indicator" id="ind-models"></div>
                <div class="emoji">🎛️</div>
                <h2>Models</h2>
                <p>Управление моделями, primary/fallback, ping и переключение провайдеров.</p>
            </a>

            <a href="/admin/routing" class="card" style="animation-delay: 0.1s;">
                <div class="indicator" id="ind-routing"></div>
                <div class="emoji">🎯</div>
                <h2>Routing</h2>
                <p>Smart Routing 5-stage pipeline: hard gates, policies, classifier, feedback.</p>
            </a>

            <a href="/admin/swarm" class="card" style="animation-delay: 0.15s;">
                <div class="indicator" id="ind-swarm"></div>
                <div class="emoji">🤖</div>
                <h2>Swarm</h2>
                <p>Мульти-агентные команды: traders, coders, analysts, creative; task board.</p>
            </a>

            <a href="/admin/costs" class="card" style="animation-delay: 0.2s;">
                <div class="indicator" id="ind-costs"></div>
                <div class="emoji">💰</div>
                <h2>Costs</h2>
                <p>FinOps дашборд: расходы по провайдерам, бюджеты и trend usage.</p>
            </a>

            <a href="/api/ecosystem/health" class="card" style="animation-delay: 0.25s;">
                <div class="indicator" id="ind-ecosystem"></div>
                <div class="emoji">🌐</div>
                <h2>Ecosystem</h2>
                <p>Здоровье всей экосистемы: Krab, Voice Gateway, Krab Ear, MCP сервисы.</p>
            </a>

            <a href="/inbox" class="card" style="animation-delay: 0.3s;">
                <div class="indicator" id="ind-inbox"></div>
                <div class="emoji">📥</div>
                <h2>Inbox</h2>
                <p>Входящие элементы с фильтрами, расширяемыми карточками и bulk-ack.</p>
            </a>

            <a href="/admin/cron" class="card" style="animation-delay: 0.5s;">
                <div class="emoji">⏱️</div>
                <h2>Cron</h2>
                <p>Статус launchd-агентов:<br>schedule, last_run, trigger/pause/resume.</p>
            </a>

            <a href="/admin/sentry" class="card" style="animation-delay: 0.6s;">
                <div class="emoji">🛡️</div>
                <h2>Sentry</h2>
                <p>События, квота и unresolved issues<br>с one-click resolve.</p>
            </a>

            <a href="/admin/logs" class="card" style="animation-delay: 0.7s;">
                <div class="emoji">📋</div>
                <h2>Logs</h2>
                <p>Live tail structlog (krab_main.log)<br>с level-фильтром, grep и download.</p>
            </a>

            <a href="/admin/db" class="card" style="animation-delay: 0.8s;">
                <div class="emoji">🗃️</div>
                <h2>DB</h2>
                <p>SQLite БД: размер, integrity_check,<br>WAL checkpoint и VACUUM actions.</p>
            </a>

            <a href="/admin/network" class="card" style="animation-delay: 0.9s;">
                <div class="emoji">🛰️</div>
                <h2>Network</h2>
                <p>MTProto session, DC, heartbeat, FloodWait,<br>ping и DNS-диагностика.</p>
            </a>

            <a href="/admin/voice" class="card" style="animation-delay: 1.0s;">
                <div class="emoji">🎙️</div>
                <h2>Voice</h2>
                <p>TTS state, Voice Gateway, Krab Ear,<br>STT cost и restart actions.</p>
            </a>

            <a href="/admin/memory" class="card" style="animation-delay: 1.1s;">
                <div class="emoji">🧠</div>
                <h2>Memory</h2>
                <p>RAG: archive.db rows, vec health,<br>retrieval metrics и search interface.</p>
            </a>

            <a href="/admin/health" class="card" style="animation-delay: 1.2s;">
                <div class="emoji">🩺</div>
                <h2>Health</h2>
                <p>Unified single-pane-of-glass:<br>traffic light + 7 subsystem cards.</p>
            </a>

            <a href="/admin/help" class="card" style="animation-delay: 1.3s;">
                <div class="emoji">📚</div>
                <h2>Help</h2>
                <p>Индекс всех admin-страниц:<br>назначение, endpoints, recent changes.</p>
            </a>

            <a href="/admin/env" class="card" style="animation-delay: 1.4s;">
                <div class="emoji">⚙️</div>
                <h2>Env</h2>
                <p>Read-only env dashboard:<br>KRAB_* vars с автомаскировкой секретов.</p>
            </a>

            <a href="/admin/commands" class="card" style="animation-delay: 1.5s;">
                <div class="emoji">⚡</div>
                <h2>Commands</h2>
                <p>162 Telegram-команды: search, фильтр<br>по категории, usage stats и aliases.</p>
            </a>

            <a href="/admin/skills" class="card" style="animation-delay: 1.6s;">
                <div class="emoji">🧩</div>
                <h2>Skills</h2>
                <p>Browser src/skills/* модулей:<br>файлы, public funcs, curator reports.</p>
            </a>

            <a href="/admin/silence" class="card" style="animation-delay: 1.7s;">
                <div class="emoji">🔇</div>
                <h2>Silence</h2>
                <p>Per-chat silence mode + расписание:<br>list active, add/remove mutes.</p>
            </a>

            <a href="/admin/aliases" class="card" style="animation-delay: 1.8s;">
                <div class="emoji">🏷️</div>
                <h2>Aliases</h2>
                <p>Editor алиасов команд: добавить,<br>удалить, conflict-check vs registry.</p>
            </a>

            <a href="/admin/typing" class="card" style="animation-delay: 1.9s;">
                <div class="emoji">⌨️</div>
                <h2>Typing</h2>
                <p>Typing indicator metrics: per-action,<br>FloodWait, duration histogram.</p>
            </a>
        </div>

        <footer>
            <div>
                <a href="/docs">API Docs</a>
                <span class="separator">·</span>
                <a href="/metrics">Metrics</a>
                <span class="separator">·</span>
                <a href="https://github.com" target="_blank" rel="noopener">GitHub</a>
            </div>
            <div style="margin-top: 12px;">
                Krab v8 · <span class="mono" id="footer-model">—</span>
            </div>
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

        // Format uptime в human-readable вид
        function formatUptime(seconds) {
            if (!seconds || seconds < 0) return '—';
            const d = Math.floor(seconds / 86400);
            const h = Math.floor((seconds % 86400) / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            if (d > 0) return d + 'd ' + h + 'h';
            if (h > 0) return h + 'h ' + m + 'm';
            return m + 'm';
        }

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
                    scheduler: { enabled: false },
                    uptime_seconds: 0
                });
            }
        }

        function updateUI(data) {
            if (data.last_runtime_route && data.last_runtime_route.model) {
                const liveModel = data.last_runtime_route.model;
                document.getElementById('model-name').textContent = liveModel;
                const heroModel = document.getElementById('hero-model');
                if (heroModel) heroModel.textContent = liveModel;
                const footerModel = document.getElementById('footer-model');
                if (footerModel) footerModel.textContent = liveModel;
            }

            if (typeof data.uptime_seconds === 'number') {
                const heroUptime = document.getElementById('hero-uptime');
                if (heroUptime) heroUptime.textContent = formatUptime(data.uptime_seconds);
            }

            if (data.telegram) {
                const tg = data.telegram.session_state || 'unknown';
                document.getElementById('stat-tg').textContent = tg;
                // Routing indicator (lights up если Telegram online)
                const ind = document.getElementById('ind-routing');
                if (ind) ind.classList.toggle('ok', tg === 'connected' || tg === 'online');
            }

            if (data.inbox_summary) {
                const open = data.inbox_summary.open_items ?? 0;
                document.getElementById('stat-inbox').textContent = open;
                const ind = document.getElementById('ind-inbox');
                if (ind) ind.classList.toggle('warn', open > 0);
            }

            if (data.voice) {
                document.getElementById('stat-voice').textContent = data.voice.enabled ? 'ВКЛ' : 'ВЫКЛ';
            }

            if (data.scheduler) {
                document.getElementById('stat-sched').textContent = data.scheduler.enabled ? 'ВКЛ' : 'ВЫКЛ';
            }

            // Models indicator (есть active modal)
            if (data.last_runtime_route && data.last_runtime_route.model && data.last_runtime_route.model !== '—') {
                const ind = document.getElementById('ind-models');
                if (ind) ind.classList.add('ok');
            }
        }

        // Initial fetch and polling
        fetchStats();
        setInterval(fetchStats, 10000);
    </script>
</body>
</html>
"""
