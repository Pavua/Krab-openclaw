STATS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab Admin Dashboard v2</title>
    <style>
        :root {
            --bg-base: #0d0d0d;
            --bg-card: #151515;
            --text-main: #eeeeee;
            --text-muted: #888888;
            --accent: #00ffcc;
            --accent-glow: rgba(0, 255, 204, 0.4);
            --border-grad: linear-gradient(135deg, #333333, #111111);
            --border-grad-hover: linear-gradient(135deg, var(--accent), #333333);
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-main);
            font-family: system-ui, -apple-system, sans-serif;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* Top Bar */
        .top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            padding: 15px 24px;
            border-radius: 12px;
            margin-bottom: 24px;
            border: 1px solid #222;
            font-size: 0.9rem;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: 600;
        }

        .dot {
            width: 10px;
            height: 10px;
            background-color: #00ff44;
            border-radius: 50%;
            box-shadow: 0 0 8px #00ff44;
            animation: pulse 2s infinite;
        }

        .model-info { color: var(--text-muted); }
        .model-info span { color: var(--accent); font-weight: 600; }
        .clock { font-family: monospace; font-size: 1.1rem; color: var(--text-muted); }

        /* Grid Layout */
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 24px;
            flex: 1;
        }

        /* Cards */
        .card-wrapper {
            position: relative;
            border-radius: 14px;
            background: var(--border-grad);
            padding: 1px;
            transition: all 0.3s ease;
        }

        .card-wrapper:hover {
            background: var(--border-grad-hover);
            box-shadow: 0 0 20px rgba(0, 255, 204, 0.15);
            transform: translateY(-2px);
        }

        .card {
            background: var(--bg-card);
            border-radius: 13px;
            padding: 24px;
            height: 100%;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
        }

        .card-header {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 1.2rem;
            font-weight: 700;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid #222;
        }

        /* Typography & Rows */
        .stat-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }

        .stat-row:last-child { margin-bottom: 0; }

        .label {
            font-size: 0.8rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .value {
            font-size: 1.15rem;
            font-weight: 600;
            color: var(--text-main);
            transition: color 0.3s;
        }

        /* Animations */
        .updated { animation: flash 1s ease-out; }
        
        @keyframes flash {
            0% { color: var(--accent); text-shadow: 0 0 10px var(--accent-glow); }
            100% { color: var(--text-main); text-shadow: none; }
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }

        /* Progress Bar */
        .progress-container {
            margin-top: 8px;
            background: #222;
            border-radius: 6px;
            height: 6px;
            overflow: hidden;
        }

        .progress-bar {
            height: 100%;
            background: linear-gradient(90deg, var(--accent), #0088ff);
            width: 0%;
            transition: width 0.5s ease-in-out;
        }

        /* Sparkline */
        .sparkline-wrapper {
            margin-top: auto;
            padding-top: 16px;
            border-top: 1px solid #222;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
        }

        .sparkline {
            display: flex;
            align-items: flex-end;
            gap: 4px;
            height: 30px;
        }

        .spark-bar {
            width: 6px;
            background: var(--accent);
            border-radius: 2px 2px 0 0;
            transition: height 0.4s ease;
            opacity: 0.8;
        }
        .spark-bar:last-child { opacity: 1; box-shadow: 0 0 8px var(--accent-glow); }

        /* Footer */
        .footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #222;
            color: var(--text-muted);
            font-size: 0.85rem;
        }

        .refresh-timer span { color: var(--accent); font-weight: bold; font-family: monospace; }
    </style>
</head>
<body>
<nav style="background:#121212;border-bottom:1px solid #2a2a2a;padding:8px 20px;display:flex;gap:20px;align-items:center;font-size:14px;position:sticky;top:0;z-index:100;"><a href="/" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">🦀 Главная</a><a href="/stats" style="color:#7dd3fc;text-decoration:none;border-bottom:2px solid #7dd3fc;padding-bottom:2px;">📊 Stats</a><a href="/inbox" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">📥 Inbox</a><a href="/costs" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">💰 Costs</a><a href="/swarm" style="color:#e0e0e0;text-decoration:none;opacity:0.7;">🐝 Swarm</a></nav>


    <div class="top-bar">
        <div class="status-indicator">
            <div class="dot"></div> Online
        </div>
        <div class="model-info">Active Model: <span id="top-model">Loading...</span></div>
        <div class="clock" id="clock">00:00:00</div>
    </div>

    <div class="dashboard-grid">
        <!-- Rate Limiter -->
        <div class="card-wrapper">
            <div class="card">
                <div class="card-header">🌐 Rate Limiter</div>
                <div class="stat-row"><span class="label">Max Per Sec</span><span class="value" id="rl-max">-</span></div>
                <div class="stat-row"><span class="label">Current Window</span><span class="value" id="rl-curr">-</span></div>
                <div class="progress-container"><div class="progress-bar" id="rl-progress"></div></div>
                <div style="margin-top: 14px;"></div>
                <div class="stat-row"><span class="label">Total Acquired</span><span class="value" id="rl-acq">-</span></div>
                <div class="stat-row"><span class="label">Total Waited</span><span class="value" id="rl-wait">-</span></div>
                <div class="stat-row"><span class="label">Wait Time (s)</span><span class="value" id="rl-wait-sec">-</span></div>
            </div>
        </div>

        <!-- Caches -->
        <div class="card-wrapper">
            <div class="card">
                <div class="card-header">📦 Cache Stats</div>
                <div class="stat-row"><span class="label">Ban Cache</span><span class="value" id="c-ban">-</span></div>
                <div class="stat-row"><span class="label">Capability Cache</span><span class="value" id="c-cap">-</span></div>
                <div class="stat-row"><span class="label">Voice Blocked</span><span class="value" id="c-vb">-</span></div>
                <div class="stat-row"><span class="label">Voice Disallowed</span><span class="value" id="c-cvd">-</span></div>
                <div class="stat-row"><span class="label">Slow Mode</span><span class="value" id="c-csm">-</span></div>
            </div>
        </div>

        <!-- Voice Runtime -->
        <div class="card-wrapper">
            <div class="card">
                <div class="card-header">🎙 Voice Runtime</div>
                <div class="stat-row"><span class="label">Enabled</span><span class="value" id="v-en">-</span></div>
                <div class="stat-row"><span class="label">Delivery</span><span class="value" id="v-del">-</span></div>
                <div class="stat-row"><span class="label">Speed</span><span class="value" id="v-spd">-</span></div>
                <div class="stat-row"><span class="label">Voice ID</span><span class="value" id="v-voice">-</span></div>
                <div class="stat-row"><span class="label">Blocked Chats</span><span class="value" id="v-blk">-</span></div>
            </div>
        </div>

        <!-- Inbox -->
        <div class="card-wrapper">
            <div class="card">
                <div class="card-header">📥 Inbox Status</div>
                <div class="stat-row"><span class="label">Total Items</span><span class="value" id="i-tot">-</span></div>
                <div class="stat-row"><span class="label">Open Items</span><span class="value" id="i-open">-</span></div>
                <div class="stat-row"><span class="label">Fresh Open</span><span class="value" id="i-fresh">-</span></div>
                <div class="stat-row"><span class="label">Stale Open</span><span class="value" id="i-stale">-</span></div>
                <div class="stat-row"><span class="label">Pending Approvals</span><span class="value" id="i-pend">-</span></div>
                <div class="stat-row"><span class="label">New Owner Req</span><span class="value" id="i-req">-</span></div>
                
                <div class="sparkline-wrapper">
                    <div class="label">Attention Items <span id="i-att" style="color:var(--text-main); font-weight:bold; margin-left:6px;">-</span></div>
                    <div class="sparkline" id="sparkline">
                        <!-- Bars injected via JS -->
                    </div>
                </div>
            </div>
        </div>

        <!-- OpenClaw Routing -->
        <div class="card-wrapper">
            <div class="card">
                <div class="card-header">🔀 OpenClaw Routing</div>
                <div class="stat-row"><span class="label">Model</span><span class="value" id="oc-mod">-</span></div>
                <div class="stat-row"><span class="label">Provider</span><span class="value" id="oc-prov">-</span></div>
                <div class="stat-row"><span class="label">Status</span><span class="value" id="oc-stat">-</span></div>
                <div class="stat-row"><span class="label">Route Reason</span><span class="value" id="oc-reas">-</span></div>
            </div>
        </div>
    </div>

    <div class="footer">
        <div>Krab v8 · Session 4+</div>
        <div class="refresh-timer">Refreshing in <span id="countdown">5</span>s</div>
    </div>

    <script>
        // Clock
        setInterval(() => {
            document.getElementById('clock').innerText = new Date().toLocaleTimeString('en-US', { hour12: false });
        }, 1000);

        // Countdown Timer
        let countdown = 5;
        setInterval(() => {
            countdown--;
            if (countdown <= 0) countdown = 5;
            document.getElementById('countdown').innerText = countdown;
        }, 1000);

        // Data Fetching & DOM Updates
        async function safeFetch(url) {
            try {
                const res = await fetch(url);
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return await res.json();
            } catch (e) {
                return null;
            }
        }

        function updateVal(id, val) {
            const el = document.getElementById(id);
            if (!el) return;
            const displayVal = (val === null || val === undefined) ? '⚠️ unavailable' : val;
            if (el.innerText !== String(displayVal)) {
                el.innerText = displayVal;
                el.classList.remove('updated');
                void el.offsetWidth; // trigger reflow
                el.classList.add('updated');
            }
        }

        function updateSparkline(val) {
            const container = document.getElementById('sparkline');
            container.innerHTML = '';
            const base = parseInt(val) || 0;
            
            // Generate 7 bars simulating recent history, ending with current value
            const heights = [
                Math.max(2, base * 0.6), Math.max(4, base * 1.2), 
                Math.max(1, base * 0.4), Math.max(5, base * 1.5), 
                Math.max(3, base * 0.8), Math.max(4, base * 1.1), 
                Math.max(1, base)
            ];
            const maxH = Math.max(...heights, 10);
            
            heights.forEach(h => {
                const pct = Math.min(100, (h / maxH) * 100);
                const bar = document.createElement('div');
                bar.className = 'spark-bar';
                bar.style.height = Math.max(10, pct) + '%';
                container.appendChild(bar);
            });
        }

        async function fetchData() {
            const [health, caches, voice, inbox] = await Promise.all([
                safeFetch('/api/health/lite'),
                safeFetch('/api/stats/caches'),
                safeFetch('/api/voice/runtime'),
                safeFetch('/api/inbox/status')
            ]);

            // Rate Limiter & OpenClaw (from health)
            if (health) {
                const rl = health.telegram_rate_limiter || {};
                updateVal('rl-max', rl.max_per_sec);
                updateVal('rl-curr', rl.current_in_window);
                updateVal('rl-acq', rl.total_acquired);
                updateVal('rl-wait', rl.total_waited);
                updateVal('rl-wait-sec', rl.total_wait_sec);
                
                const max = parseFloat(rl.max_per_sec) || 1;
                const curr = parseFloat(rl.current_in_window) || 0;
                document.getElementById('rl-progress').style.width = Math.min(100, (curr / max) * 100) + '%';

                const oc = health.last_runtime_route || {};
                updateVal('oc-mod', oc.model);
                updateVal('top-model', oc.model);
                updateVal('oc-prov', oc.provider);
                updateVal('oc-stat', oc.status);
                updateVal('oc-reas', oc.route_reason);
            } else {
                ['rl-max','rl-curr','rl-acq','rl-wait','rl-wait-sec','oc-mod','top-model','oc-prov','oc-stat','oc-reas'].forEach(id => updateVal(id, null));
            }

            // Caches
            if (caches) {
                updateVal('c-ban', caches.ban_cache_count);
                updateVal('c-cap', caches.capability_cache_count);
                updateVal('c-vb', caches.voice_blocked_count);
                updateVal('c-cvd', caches.capability_voice_disallowed);
                updateVal('c-csm', caches.capability_slow_mode);
            } else {
                ['c-ban','c-cap','c-vb','c-cvd','c-csm'].forEach(id => updateVal(id, null));
            }

            // Voice
            if (voice && voice.voice) {
                const v = voice.voice;
                updateVal('v-en', v.enabled);
                updateVal('v-del', v.delivery);
                updateVal('v-spd', v.speed);
                updateVal('v-voice', v.voice);
                updateVal('v-blk', v.blocked_chats);
            } else {
                ['v-en','v-del','v-spd','v-voice','v-blk'].forEach(id => updateVal(id, null));
            }

            // Inbox
            if (inbox && inbox.summary) {
                const s = inbox.summary;
                updateVal('i-tot', s.total_items);
                updateVal('i-open', s.open_items);
                updateVal('i-fresh', s.fresh_open_items);
                updateVal('i-stale', s.stale_open_items);
                updateVal('i-pend', s.pending_approvals);
                updateVal('i-req', s.new_owner_requests);
                updateVal('i-att', s.attention_items);
                updateSparkline(s.attention_items);
            } else {
                ['i-tot','i-open','i-fresh','i-stale','i-pend','i-req','i-att'].forEach(id => updateVal(id, null));
                updateSparkline(0);
            }
            
            countdown = 5; // Reset countdown on successful fetch cycle
        }

        // Init
        fetchData();
        setInterval(fetchData, 5000);
    </script>
</body>
</html>"""