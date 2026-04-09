SWARM_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab Swarm Visualizer</title>
    <style>
        :root {
            --bg: #0f172a;
            --card-bg: #1e293b;
            --card-hover: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #3b82f6;
            --success: #22c55e;
            --border: #334155;
            --warning: #ca8a04;
        }

        * { box-sizing: border-box; }

        body {
            font-family: system-ui, -apple-system, sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            margin: 0;
            padding: 0;
            line-height: 1.5;
        }

        /* Status Bar */
        .status-bar {
            background-color: var(--card-bg);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .status-bar h1 {
            margin: 0;
            font-size: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .demo-badge {
            background-color: var(--warning);
            color: #fff;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 600;
            display: none;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }

        .section-title {
            font-size: 1.5rem;
            font-weight: 600;
            margin: 2rem 0 1rem 0;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .section-title:first-child { margin-top: 0; }

        /* Teams Grid */
        .teams-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1rem;
        }
        @media (min-width: 640px) { .teams-grid { grid-template-columns: repeat(2, 1fr); } }
        @media (min-width: 1024px) { .teams-grid { grid-template-columns: repeat(4, 1fr); } }

        .team-card {
            background: var(--card-bg);
            border: 2px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.5rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .team-card:hover { border-color: var(--text-muted); }
        .team-card.selected {
            border-color: var(--accent);
            background: rgba(59, 130, 246, 0.05);
        }

        .team-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            font-size: 1.25rem;
            font-weight: bold;
        }

        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background-color: #475569;
        }
        .status-dot.active {
            background-color: var(--success);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(34, 197, 94, 0); }
            100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
        }

        .stat-row {
            display: flex;
            justify-content: space-between;
            font-size: 0.875rem;
            color: var(--text-muted);
            margin-top: 0.5rem;
        }
        .stat-row strong { color: var(--text-main); }

        /* Memory Section */
        .memory-list {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .memory-item {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.25rem;
            transition: background 0.2s;
        }
        .memory-item.expandable { cursor: pointer; }
        .memory-item.expandable:hover { background: var(--card-hover); }

        .memory-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.5rem;
        }

        .memory-topic {
            font-weight: 600;
            color: var(--accent);
            font-size: 1.1rem;
        }
        .memory-round {
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: normal;
            margin-left: 0.5rem;
        }

        .memory-time {
            color: var(--text-muted);
            font-size: 0.875rem;
            white-space: nowrap;
        }

        .memory-summary {
            color: var(--text-main);
            font-size: 0.95rem;
            white-space: pre-wrap;
        }

        .expand-hint {
            color: var(--accent);
            font-size: 0.8rem;
            margin-top: 0.5rem;
            display: none;
        }
        .memory-item.expandable .expand-hint { display: block; }
        .memory-item.expanded .expand-hint { display: none; }

        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
        }

        .stat-tile {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 0.75rem;
            padding: 1.5rem;
            text-align: center;
        }

        .stat-value {
            font-size: 2.5rem;
            font-weight: bold;
            color: var(--text-main);
            margin-bottom: 0.25rem;
        }

        .stat-label {
            color: var(--text-muted);
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
    </style>
</head>
<body>
<nav style="background:#121212;border-bottom:1px solid #2a2a2a;padding:8px 20px;display:flex;gap:20px;align-items:center;font-size:14px;position:sticky;top:0;z-index:100;"><a href="/" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">🦀 Главная</a><a href="/stats" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">📊 Stats</a><a href="/inbox" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">📥 Inbox</a><a href="/costs" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">💰 Costs</a><a href="/swarm" style="color:#7dd3fc;text-decoration:none;border-bottom:2px solid #7dd3fc;padding-bottom:2px;">🐝 Swarm</a></nav>


    <div class="status-bar">
        <h1>🐝 Krab Swarm</h1>
        <div id="demo-badge" class="demo-badge">🔧 Демо-режим</div>
    </div>

    <div class="container">
        <div class="section-title">Команды агентов</div>
        <div class="teams-grid" id="teams-grid">
            <!-- Teams injected here -->
        </div>

        <div class="section-title">
            Память: <span id="selected-team-name" style="color: var(--accent); margin-left: 0.5rem;">Трейдеры</span>
        </div>
        <div class="memory-list" id="memory-list">
            <!-- Memory injected here -->
        </div>

        <div class="section-title">Общая статистика</div>
        <div class="stats-grid" id="stats-grid">
            <!-- Stats injected here -->
        </div>
    </div>

    <script>
        const TEAM_CONFIG = {
            traders: { emoji: '🐂', name: 'Трейдеры' },
            coders: { emoji: '💻', name: 'Кодеры' },
            analysts: { emoji: '📊', name: 'Аналитики' },
            creative: { emoji: '🎨', name: 'Креатив' }
        };

        let currentTeam = 'traders';
        let isDemoMode = false;

        // --- Demo Data ---
        const DEMO_STATUS = {
            "ok": true,
            "teams": {
                "traders": {"active": false, "last_round_at": new Date(Date.now() - 2*3600000).toISOString(), "rounds_total": 12},
                "coders": {"active": false, "last_round_at": new Date(Date.now() - 5*3600000).toISOString(), "rounds_total": 8},
                "analysts": {"active": true, "last_round_at": new Date(Date.now() - 15*60000).toISOString(), "rounds_total": 15},
                "creative": {"active": false, "last_round_at": new Date(Date.now() - 24*3600000).toISOString(), "rounds_total": 3}
            },
            "memory_entries": 50,
            "scheduler_jobs": 0
        };

        const DEMO_MEMORY = {
            traders: [
                {"topic": "BTC analysis", "summary": "Price target $95k based on strong institutional inflows and recent ETF approvals. Support levels holding at $88k. Recommend scaling into long positions over the next 48 hours to minimize slippage. Monitoring order book depth for sudden whale movements.", "timestamp": new Date(Date.now() - 2*3600000).toISOString(), "round_id": "r12"},
                {"topic": "ETH/SOL ratio", "summary": "SOL showing relative strength. Rotation expected.", "timestamp": new Date(Date.now() - 26*3600000).toISOString(), "round_id": "r11"}
            ],
            coders: [
                {"topic": "API Refactoring", "summary": "Optimized database queries in the main loop. Reduced latency by 45%. Need to monitor memory usage over the next 24 hours to ensure no leaks were introduced during the async rewrite. All unit tests passing.", "timestamp": new Date(Date.now() - 5*3600000).toISOString(), "round_id": "r8"}
            ],
            analysts: [
                {"topic": "Market Sentiment", "summary": "Fear & Greed index at 82. Retail FOMO increasing. Historical data suggests a 10-15% correction is imminent within the next week. Advising traders to tighten stop losses.", "timestamp": new Date(Date.now() - 15*60000).toISOString(), "round_id": "r15"}
            ],
            creative: [
                {"topic": "Meme Generation", "summary": "Created 5 new Pepe variants for the upcoming marketing push. Engagement metrics on previous batch show a 20% increase in retweets. Focusing on laser-eyes motif for the next round.", "timestamp": new Date(Date.now() - 24*3600000).toISOString(), "round_id": "r3"}
            ]
        };

        // --- Utils ---
        function timeAgo(dateString) {
            if (!dateString) return 'Никогда';
            const date = new Date(dateString);
            const diffSec = Math.floor((new Date() - date) / 1000);
            
            if (diffSec < 60) return 'Только что';
            const diffMin = Math.floor(diffSec / 60);
            if (diffMin < 60) return `${diffMin} мин назад`;
            const diffHour = Math.floor(diffMin / 60);
            if (diffHour < 24) return `${diffHour} ч назад`;
            const diffDay = Math.floor(diffHour / 24);
            return `${diffDay} дн назад`;
        }

        // --- API Calls ---
        async function fetchStatus() {
            try {
                const res = await fetch('/api/swarm/status');
                if (!res.ok) throw new Error('API Error');
                const data = await res.json();
                isDemoMode = false;
                document.getElementById('demo-badge').style.display = 'none';
                return data;
            } catch (e) {
                isDemoMode = true;
                document.getElementById('demo-badge').style.display = 'block';
                return DEMO_STATUS;
            }
        }

        async function fetchMemory(team) {
            try {
                if (isDemoMode) throw new Error('Demo');
                const res = await fetch(`/api/swarm/memory?team=${team}&limit=5`);
                if (!res.ok) throw new Error('API Error');
                const data = await res.json();
                return data.entries;
            } catch (e) {
                return DEMO_MEMORY[team] || [];
            }
        }

        // --- Renderers ---
        function renderTeams(teamsData) {
            const grid = document.getElementById('teams-grid');
            grid.innerHTML = '';

            for (const [key, config] of Object.entries(TEAM_CONFIG)) {
                const data = teamsData[key] || { active: false, rounds_total: 0, last_round_at: null };
                const card = document.createElement('div');
                card.className = `team-card ${key === currentTeam ? 'selected' : ''}`;
                
                card.onclick = async () => {
                    currentTeam = key;
                    document.getElementById('selected-team-name').innerText = config.name;
                    renderTeams(teamsData); // Update selection highlight
                    const memory = await fetchMemory(key);
                    renderMemory(memory);
                };

                card.innerHTML = `
                    <div class="team-header">
                        <span>${config.emoji} ${config.name}</span>
                        <div class="status-dot ${data.active ? 'active' : ''}" title="${data.active ? 'В работе' : 'Ожидание'}"></div>
                    </div>
                    <div class="stat-row">
                        <span>Всего раундов:</span>
                        <strong>${data.rounds_total}</strong>
                    </div>
                    <div class="stat-row">
                        <span>Последний:</span>
                        <strong>${timeAgo(data.last_round_at)}</strong>
                    </div>
                `;
                grid.appendChild(card);
            }
        }

        function renderStats(data) {
            const totalRounds = Object.values(data.teams).reduce((sum, t) => sum + t.rounds_total, 0);
            const activeTeams = Object.values(data.teams).filter(t => t.active).length;

            document.getElementById('stats-grid').innerHTML = `
                <div class="stat-tile">
                    <div class="stat-value">${totalRounds}</div>
                    <div class="stat-label">Всего раундов</div>
                </div>
                <div class="stat-tile">
                    <div class="stat-value">${data.memory_entries}</div>
                    <div class="stat-label">Записей в памяти</div>
                </div>
                <div class="stat-tile">
                    <div class="stat-value" style="color: ${activeTeams > 0 ? 'var(--success)' : 'var(--text-main)'}">${activeTeams} / 4</div>
                    <div class="stat-label">Активных команд</div>
                </div>
                <div class="stat-tile">
                    <div class="stat-value">${data.scheduler_jobs}</div>
                    <div class="stat-label">Задач в планировщике</div>
                </div>
            `;
        }

        function renderMemory(entries) {
            const list = document.getElementById('memory-list');
            list.innerHTML = '';

            if (!entries || entries.length === 0) {
                list.innerHTML = '<div class="memory-item" style="text-align:center; color:var(--text-muted)">Нет записей в памяти</div>';
                return;
            }

            entries.forEach(entry => {
                const item = document.createElement('div');
                const isLong = entry.summary.length > 150;
                const shortText = isLong ? entry.summary.substring(0, 150) + '...' : entry.summary;
                
                item.className = `memory-item ${isLong ? 'expandable' : ''}`;
                
                item.innerHTML = `
                    <div class="memory-header">
                        <div class="memory-topic">
                            ${entry.topic} <span class="memory-round">(${entry.round_id})</span>
                        </div>
                        <div class="memory-time">${timeAgo(entry.timestamp)}</div>
                    </div>
                    <div class="memory-summary" data-full="${entry.summary.replace(/"/g, '&quot;')}" data-short="${shortText.replace(/"/g, '&quot;')}">${shortText}</div>
                    ${isLong ? '<div class="expand-hint">Нажмите, чтобы развернуть</div>' : ''}
                `;

                if (isLong) {
                    item.onclick = function() {
                        const summaryDiv = this.querySelector('.memory-summary');
                        const isExpanded = this.classList.toggle('expanded');
                        summaryDiv.innerText = isExpanded ? summaryDiv.getAttribute('data-full') : summaryDiv.getAttribute('data-short');
                    };
                }

                list.appendChild(item);
            });
        }

        // --- Initialization ---
        async function init() {
            const statusData = await fetchStatus();
            renderTeams(statusData.teams);
            renderStats(statusData);
            
            const memoryData = await fetchMemory(currentTeam);
            renderMemory(memoryData);

            // Poll status every 10 seconds
            setInterval(async () => {
                const newStatus = await fetchStatus();
                renderTeams(newStatus.teams);
                renderStats(newStatus);
            }, 10000);
        }

        document.addEventListener('DOMContentLoaded', init);
    </script>
</body>
</html>
"""