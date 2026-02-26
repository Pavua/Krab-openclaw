import re
import os

def update_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Step C1: File protocol guard enhancement
    # Replace existing fileProtocolWarning with a better styled one
    old_warning = '<div id="fileProtocolWarning" class="card" style="display:none; border-color: var(--state-warn);">'
    new_warning = '''<div id="fileProtocolWarning" class="card" style="display:none; border-left: 4px solid var(--state-warn); background: rgba(255, 160, 0, 0.1);">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <h4 class="card-title" style="color: var(--state-warn); margin:0 0 4px 0;">Внимание: Неверный режим открытия</h4>
                    <p class="card-meta" style="margin:0;">Панель работает в ограниченном режиме (file://). Для полной работы API необходимо использовать локальный сервер.</p>
                </div>
                <a href="http://127.0.0.1:8080" class="badge ok" style="text-decoration:none; padding:8px 12px;">Открыть через http://127.0.0.1:8080</a>
            </div>
        </div>'''
    content = content.replace(old_warning, new_warning)
    
    # Step A2/B1: Add Ops Alerts filter & Improve History
    # Find opsAlerts block
    if '<div id="opsAlerts" class="card-meta">—</div>' in content:
        alerts_html = '''<div style="display:flex; gap:8px; margin-bottom:8px;">
                        <input id="opsAlertSearch" class="field" placeholder="Поиск по коду alert..." style="flex:1;" oninput="filterAlerts()" />
                    </div>
                    <div id="opsAlerts" class="card-meta" style="max-height: 200px; overflow-y: auto;">—</div>'''
        content = content.replace('<div id="opsAlerts" class="card-meta">—</div>', alerts_html)

    # In JS: update Alerts rendering to add data-code for filtering
    js_alert_old = '''const ack = item.acknowledged ? ` <span class="badge ok">ACK ${item.ack?.actor || ''}</span>` : '';
                            return `<div><span class="badge ${item.severity === 'high' ? 'bad' : 'warn'}">${item.severity || 'info'}</span> ${item.code || ''} — ${item.message || ''}${ack}</div>`;'''
    js_alert_new = '''const ack = item.acknowledged ? ` <span class="badge ok" title="ACK by ${item.ack?.actor || ''}">ACK</span>` : '';
                            const statusCls = item.severity === 'high' ? 'bad' : 'warn';
                            return `<div class="alert-item" data-code="${(item.code || '').toLowerCase()}" style="padding:4px 0; border-bottom:1px solid var(--border-subtle); display:flex; justify-content:space-between; align-items:flex-start;">
                                <div><span class="badge ${statusCls}">${item.severity || 'info'}</span> ${item.code || ''} — ${item.message || ''}</div>
                                <div>${ack}</div>
                            </div>`;'''
    content = content.replace(js_alert_old, js_alert_new)

    # In JS: filterAlerts function
    if 'function filterAlerts' not in content:
        content = content.replace('async function updateStats()', '''function filterAlerts() {
            const query = (document.getElementById('opsAlertSearch')?.value || '').toLowerCase();
            const items = document.querySelectorAll('#opsAlerts .alert-item');
            items.forEach(el => {
                if (query === '' || el.getAttribute('data-code').includes(query) || el.textContent.toLowerCase().includes(query)) {
                    el.style.display = 'flex';
                } else {
                    el.style.display = 'none';
                }
            });
        }
        
        async function updateStats()''')

    # General API Error behavior: improve error reporting
    content = content.replace('setText(\'assistantMeta\', \'Запрос завершился с ошибкой\');', 'setText(\'assistantMeta\', \'Запрос завершился с ошибкой: \' + error.message);')
    content = content.replace('setText(\'assistantMeta\', \'Preflight завершился с ошибкой\');', 'setText(\'assistantMeta\', \'Preflight завершился с ошибкой: \' + error.message);')

    # Fix file protocol display handling
    content = content.replace('document.getElementById(\'fileProtocolWarning\').style.display = \'block\';', 'document.getElementById(\'fileProtocolWarning\').style.display = \'flex\';')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print(f"Updated {path}")

# Run
update_file('src/web/index.html')
update_file('src/web/prototypes/nano/index_redesign.html')
