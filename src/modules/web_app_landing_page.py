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
            --accent: #7dd3fc;
        }
        body {
            background-color: var(--bg);
            color: var(--text);
            font-family: system-ui, -apple-system, sans-serif;
            margin: 0;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            box-sizing: border-box;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.9rem;
            color: #aaa;
        }
        .status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #22c55e;
            box-shadow: 0 0 8px #22c55e88;
            transition: background-color 0.3s, box-shadow 0.3s;
        }
        .dot.offline {
            background-color: #ef4444;
            box-shadow: 0 0 8px #ef444488;
        }
        h1 {
            text-align: center;
            font-size: 2.5rem;
            margin: 3rem 0;
            font-weight: 600;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1.5rem;
            max-width: 900px;
            margin: 0 auto;
            flex: 1;
            width: 100%;
        }
        @media (max-width: 600px) {
            .grid { grid-template-columns: 1fr; }
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            text-decoration: none;
            color: inherit;
            position: relative;
            transition: all 0.2s ease;
            display: flex;
            flex-direction: column;
        }
        .card.active:hover {
            border-color: var(--accent);
            box-shadow: 0 0 20px rgba(125, 211, 252, 0.1);
            transform: translateY(-2px);
        }
        .card.disabled {
            opacity: 0.5;
            cursor: default;
        }
        .icon {
            font-size: 2rem;
            margin-bottom: 1rem;
        }
        .title {
            font-size: 1.25rem;
            font-weight: 600;
            margin: 0 0 0.5rem 0;
        }
        .desc {
            font-size: 0.9rem;
            color: #888;
            margin: 0;
            line-height: 1.4;
        }
        .badge {
            position: absolute;
            top: 1.5rem;
            right: 1.5rem;
            background: #2a2a2a;
            color: #aaa;
            font-size: 0.7rem;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .footer {
            text-align: center;
            margin-top: 3rem;
            font-size: 0.8rem;
            color: #555;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="status">
            <div class="dot" id="status-dot"></div>
            <span id="status-text">Online</span>
        </div>
        <div id="clock">00:00:00</div>
    </div>

    <h1>🦀 Krab Control Panel</h1>

    <div class="grid">
        <a href="/stats" class="card active">
            <div class="icon">📊</div>
            <h2 class="title">Runtime Stats</h2>
            <p class="desc">Метрики, лимиты, кэш, голос и маршрутизация OpenClaw.</p>
        </a>
        
        <a href="/inbox" class="card active">
            <div class="icon">📥</div>
            <h2 class="title">Inbox</h2>
            <p class="desc">Входящие элементы с фильтрами и карточками.</p>
        </a>

        <div class="card disabled">
            <div class="badge">скоро</div>
            <div class="icon">🤖</div>
            <h2 class="title">Swarm</h2>
            <p class="desc">Командные раунды мульти-агентов.</p>
        </div>

        <div class="card disabled">
            <div class="badge">скоро</div>
            <div class="icon">💰</div>
            <h2 class="title">Costs</h2>
            <p class="desc">Аналитика биллинга и расходов.</p>
        </div>
    </div>

    <div class="footer">
        Session 4+ · Opus 4.6 + Gemini 3.1 Pro
    </div>

    <script>
        function updateClock() {
            const now = new Date();
            const timeString = now.toLocaleTimeString('ru-RU', { hour12: false });
            document.getElementById('clock').textContent = timeString;
        }
        setInterval(updateClock, 1000);
        updateClock();

        async function checkHealth() {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            try {
                const res = await fetch('/api/health/lite');
                if (res.ok) {
                    dot.className = 'dot';
                    text.textContent = 'Online';
                } else {
                    throw new Error('Bad status');
                }
            } catch (err) {
                dot.className = 'dot offline';
                text.textContent = 'Offline';
            }
        }
        checkHealth();
        setInterval(checkHealth, 10000);
    </script>
</body>
</html>"""