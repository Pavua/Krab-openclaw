INBOX_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab Inbox Dashboard</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
            --success: #22c55e;
            --error: #ef4444;
        }
        
        * { box-sizing: border-box; }
        
        body {
            background-color: var(--bg);
            color: var(--text);
            font-family: system-ui, -apple-system, sans-serif;
            margin: 0;
            padding: 20px;
            line-height: 1.5;
        }

        .mono {
            font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }

        h1 { margin: 0; font-size: 24px; font-weight: 600; }

        .status-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
            color: var(--text-muted);
        }

        .dot {
            width: 10px;
            height: 10px;
            background-color: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--success);
        }

        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }

        .summary-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }

        .summary-val {
            font-size: 24px;
            font-weight: bold;
            color: var(--accent);
            margin-bottom: 4px;
        }

        .summary-label {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .tabs {
            display: flex;
            gap: 16px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }

        .tab {
            background: none;
            border: none;
            color: var(--text-muted);
            padding: 10px 4px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 500;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }

        .tab:hover { color: var(--text); }
        .tab.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
        }

        .items-container {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .item-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            cursor: pointer;
            transition: box-shadow 0.2s, border-color 0.2s;
        }

        .item-card:hover {
            border-color: #3a3a3a;
            box-shadow: 0 0 12px rgba(125, 211, 252, 0.05);
        }

        .item-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 8px;
        }

        .item-title {
            font-weight: 600;
            font-size: 16px;
        }

        .item-time {
            font-size: 12px;
            color: var(--text-muted);
            white-space: nowrap;
            margin-left: 12px;
        }

        .item-badges {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }

        .badge {
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* Severities */
        .sev-info { background: #2a2a2a; color: #e0e0e0; }
        .sev-warning { background: #854d0e; color: #fef08a; }
        .sev-error { background: #991b1b; color: #fecaca; }
        .sev-critical { 
            background: #dc2626; 
            color: #ffffff; 
            animation: pulse 1.5s infinite; 
        }

        /* Statuses */
        .stat-open { background: #1e3a8a; color: #bfdbfe; }
        .stat-acked { background: #166534; color: #bbf7d0; }
        .stat-processing { background: #9a3412; color: #fed7aa; }
        
        .kind-label { background: #374151; color: #e5e7eb; border: 1px solid #4b5563; }

        .item-body-trunc {
            font-size: 14px;
            color: #a3a3a3;
        }

        .item-details {
            display: none;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px dashed var(--border);
        }

        .item-card.expanded .item-details { display: block; }
        .item-card.expanded .item-body-trunc { display: none; }

        .details-body {
            font-size: 14px;
            white-space: pre-wrap;
            margin-bottom: 16px;
            color: #d4d4d4;
        }

        .details-meta {
            background: #0a0a0a;
            padding: 12px;
            border-radius: 6px;
            font-size: 12px;
            color: var(--text-muted);
            overflow-x: auto;
        }

        .empty-state {
            text-align: center;
            padding: 40px;
            color: var(--text-muted);
            font-size: 16px;
            background: var(--card-bg);
            border-radius: 8px;
            border: 1px dashed var(--border);
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.6; }
            100% { opacity: 1; }
        }
    </style>
</head>
<body>

    <header>
        <h1>Входящие</h1>
        <div class="status-bar">
            <span class="dot" id="status-dot"></span>
            <span id="status-text">Online</span>
            <span id="clock" class="mono"></span>
        </div>
    </header>

    <div class="summary-grid">
        <div class="summary-card"><div class="summary-val mono" id="val-total">-</div><div class="summary-label">Всего</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-open">-</div><div class="summary-label">Открытые</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-fresh">-</div><div class="summary-label">Новые</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-stale">-</div><div class="summary-label">Зависшие</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-attention">-</div><div class="summary-label">Внимание</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-pending">-</div><div class="summary-label">Ожидают</div></div>
        <div class="summary-card"><div class="summary-val mono" id="val-owner">-</div><div class="summary-label">Запросы</div></div>
    </div>

    <div class="tabs" id="tabs">
        <button class="tab active" data-filter="all">Все</button>
        <button class="tab" data-filter="open">Открытые</button>
        <button class="tab" data-filter="attention">Внимание</button>
        <button class="tab" data-filter="stale">Устаревшие</button>
    </div>

    <div class="items-container" id="items-container">
        <!-- Items injected here -->
    </div>

    <script>
        const API_BASE = 'http://127.0.0.1:8080/api/inbox';
        let allItems = [];
        let currentFilter = 'all';

        const translations = {
            sev: { 'info': 'Инфо', 'warning': 'Предупреждение', 'error': 'Ошибка', 'critical': 'Критично' },
            stat: { 'open': 'Открыт', 'acked': 'Принят', 'processing': 'В работе' }
        };

        function escapeHtml(unsafe) {
            return (unsafe || '').toString()
                 .replace(/&/g, "&amp;")
                 .replace(/</g, "&lt;")
                 .replace(/>/g, "&gt;")
                 .replace(/"/g, "&quot;")
                 .replace(/'/g, "&#039;");
        }

        function timeAgo(dateStr) {
            if (!dateStr) return 'Неизвестно';
            const date = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
            const seconds = Math.floor((new Date() - date) / 1000);
            
            let interval = seconds / 86400;
            if (interval > 1) return Math.floor(interval) + ' дн. назад';
            interval = seconds / 3600;
            if (interval > 1) return Math.floor(interval) + ' ч. назад';
            interval = seconds / 60;
            if (interval > 1) return Math.floor(interval) + ' мин. назад';
            return Math.floor(seconds) + ' сек. назад';
        }

        function updateClock() {
            const now = new Date();
            document.getElementById('clock').textContent = now.toLocaleTimeString('ru-RU');
        }

        function setStatus(ok) {
            const dot = document.getElementById('status-dot');
            const txt = document.getElementById('status-text');
            dot.style.backgroundColor = ok ? 'var(--success)' : 'var(--error)';
            dot.style.boxShadow = ok ? '0 0 8px var(--success)' : '0 0 8px var(--error)';
            txt.textContent = ok ? 'Online' : 'Offline';
        }

        async function fetchDashboard() {
            try {
                const [statusRes, itemsRes] = await Promise.all([
                    fetch(`${API_BASE}/status`),
                    fetch(`${API_BASE}/items?limit=50`)
                ]);
                
                const statusData = await statusRes.json();
                const itemsData = await itemsRes.json();

                if (statusData.ok) {
                    const s = statusData.summary || {};
                    document.getElementById('val-total').textContent = s.total_items || 0;
                    document.getElementById('val-open').textContent = s.open_items || 0;
                    document.getElementById('val-fresh').textContent = s.fresh_open_items || 0;
                    document.getElementById('val-stale').textContent = s.stale_open_items || 0;
                    document.getElementById('val-attention').textContent = s.attention_items || 0;
                    document.getElementById('val-pending').textContent = s.pending_approvals || 0;
                    document.getElementById('val-owner').textContent = s.new_owner_requests || 0;
                }

                if (itemsData.ok) {
                    allItems = itemsData.items || [];
                    renderItems();
                }
                setStatus(true);
            } catch (err) {
                console.error('Fetch error:', err);
                setStatus(false);
            }
        }

        function renderItems() {
            const container = document.getElementById('items-container');
            let filtered = allItems;
            const now = new Date();

            if (currentFilter === 'open') {
                filtered = allItems.filter(i => i.status === 'open');
            } else if (currentFilter === 'attention') {
                filtered = allItems.filter(i => i.severity === 'error' || i.severity === 'critical');
            } else if (currentFilter === 'stale') {
                filtered = allItems.filter(i => {
                    if (i.status !== 'open') return false;
                    const updated = new Date((i.updated_at_utc || i.created_at_utc) + 'Z');
                    return (now - updated) > 24 * 60 * 60 * 1000; // > 24h
                });
            }

            if (filtered.length === 0) {
                container.innerHTML = '<div class="empty-state">📭 Нет элементов</div>';
                return;
            }

            container.innerHTML = filtered.map(i => {
                const sev = i.severity || 'info';
                const stat = i.status || 'open';
                const sevText = translations.sev[sev] || sev;
                const statText = translations.stat[stat] || stat;
                
                const bodyStr = i.body || '';
                const bodyTrunc = bodyStr.length > 100 ? bodyStr.substring(0, 100) + '...' : bodyStr;
                
                const metaStr = i.metadata ? JSON.stringify(i.metadata, null, 2) : '{}';
                const opId = i.identity?.operator_id || 'N/A';
                const chId = i.identity?.channel_id || 'N/A';

                return `
                <div class="item-card" onclick="this.classList.toggle('expanded')">
                    <div class="item-header">
                        <div class="item-title">${escapeHtml(i.title || 'Без названия')}</div>
                        <div class="item-time mono">${timeAgo(i.created_at_utc)}</div>
                    </div>
                    <div class="item-badges">
                        <span class="badge sev-${sev}">${escapeHtml(sevText)}</span>
                        <span class="badge stat-${stat}">${escapeHtml(statText)}</span>
                        <span class="badge kind-label">${escapeHtml(i.kind || 'unknown')}</span>
                    </div>
                    <div class="item-body-trunc">${escapeHtml(bodyTrunc)}</div>
                    <div class="item-details">
                        <div class="details-body">${escapeHtml(bodyStr)}</div>
                        <div class="details-meta mono">
ID: ${escapeHtml(i.item_id)}
Operator: ${escapeHtml(opId)}
Channel: ${escapeHtml(chId)}
Updated: ${escapeHtml(i.updated_at_utc)}

Metadata:
${escapeHtml(metaStr)}
                        </div>
                    </div>
                </div>
                `;
            }).join('');
        }

        document.getElementById('tabs').addEventListener('click', (e) => {
            if (e.target.classList.contains('tab')) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                e.target.classList.add('active');
                currentFilter = e.target.getAttribute('data-filter');
                renderItems();
            }
        });

        setInterval(updateClock, 1000);
        setInterval(fetchDashboard, 10000);
        
        updateClock();
        fetchDashboard();
    </script>
</body>
</html>"""