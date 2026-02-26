import os
import re

def process_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Update Badge rendering in alertsList
    # Look for the map function inside alertsList rendering
    alerts_map_old = r'const html = alertsList\s*\.map\(\(item\) => \{\s*const ack = item\.acknowledged[\s\S]*? \? ` <span class="badge ok">ACK \$\{item\.ack\?\.actor \|\| ""\}</span>`\s*: "";\s*return `<div><span class="badge \$\{item\.severity === "high" \? "bad" : "warn"\}">\$\{item\.severity \|\| "info"\}</span> \$\{item\.code \|\| ""\} — \$\{item\.message \|\| ""\}\$\{ack\}</div>`;\s*\}\)\s*\.join\(""\);'
    
    alerts_map_new = r'''const html = alertsList
            .map((item) => {
              const bg = item.severity === "high" ? "background: var(--state-error-muted); border-left: 3px solid var(--state-error);" : "background: var(--state-warn-muted); border-left: 3px solid var(--state-warn);";
              const ack = item.acknowledged
                ? ` <span class="badge ok" title="${item.ack?.actor || ''} at ${item.ack?.ts || ''}">ACK</span>`
                : (item.revoked ? ` <span class="badge" style="background: var(--bg-element); color: var(--text-placeholder)">REVOKED</span>` : "");
              
              const statusCls = item.severity === "high" ? "bad" : "warn";
              return `<div class="alert-item" data-code="${(item.code || '').toLowerCase()}" style="padding: 8px; margin-bottom: 4px; border-radius: 4px; ${bg} display:flex; justify-content:space-between; align-items:flex-start;">
                <div><span class="badge ${statusCls}">${item.severity || "info"}</span> <strong>${item.code || ""}</strong> <span style="font-size:0.9em">— ${item.message || ""}</span></div>
                <div>${ack}</div>
              </div>`;
            })
            .join("");'''
    
    content = re.sub(alerts_map_old, alerts_map_new, content)

    # 2. Update History Rendering
    history_old = r'document\.getElementById\("opsHistory"\)\.innerHTML = history\s*\.map\(\s*\(item\) =>\s*`\$\{item\.ts \|\| "-"} • \$\{item\.status \|\| "-"} • alerts=\$\{item\.alerts_count \?\? 0\} • codes=\$\{\(item\.codes \|\| \[\]\)\.join\(","\) \|\| "-"}`,\s*\)\s*\.join\("<br>"\);'
    
    history_new = r'''document.getElementById("opsHistory").innerHTML = history
            .map((item) => {
                const badgeCls = item.status === 'ok' ? 'ok' : (item.status === 'error' ? 'bad' : 'warn');
                return `<div style="display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid var(--border-subtle); align-items:center;">
                    <div style="font-size:0.9em;"><span class="badge ${badgeCls}">${item.status || "-"}</span> ${item.ts || "-"}</div>
                    <div style="font-size:0.85em; color:var(--text-placeholder);">alerts: ${item.alerts_count ?? 0} ${item.codes?.length ? `(${item.codes.join(',')})` : ''}</div>
                </div>`;
            })
            .join("");'''
    
    # for index_redesign.html Single Quotes
    history_old2 = r"document\.getElementById\('opsHistory'\)\.innerHTML = history\s*\.map\(\(item\) => `\$\{item\.ts \|\| '-'\} • \$\{item\.status \|\| '-'\} • alerts=\$\{item\.alerts_count \?\? 0\} • codes=\$\{\(item\.codes \|\| \[\]\)\.join\(\',\'\) \|\| '\-'}`\)\s*\.join\('<br>'\);"
    
    history_new2 = '''document.getElementById('opsHistory').innerHTML = history
            .map((item) => {
                const badgeCls = item.status === 'ok' ? 'ok' : (item.status === 'error' ? 'bad' : 'warn');
                return `<div style="display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid var(--border-subtle); align-items:center;">
                    <div style="font-size:0.9em;"><span class="badge ${badgeCls}">${item.status || "-"}</span> ${item.ts || "-"}</div>
                    <div style="font-size:0.85em; color:var(--text-placeholder);">alerts: ${item.alerts_count ?? 0} ${item.codes?.length ? `(${item.codes.join(',')})` : ''}</div>
                </div>`;
            })
            .join("");'''
            
    content = re.sub(history_old, history_new, content)
    content = re.sub(history_old2, history_new2, content)

    # 3. API Error Behavior Updates
    content = content.replace('setText("assistantMeta", "Запрос завершился с ошибкой");', 'setText("assistantMeta", "Запрос завершился с ошибкой: " + error.message);')
    content = content.replace('setText(\'assistantMeta\', \'Запрос завершился с ошибкой\');', 'setText(\'assistantMeta\', \'Запрос завершился с ошибкой: \' + error.message);')
    content = content.replace('setText("assistantMeta", "Preflight завершился с ошибкой");', 'setText("assistantMeta", "Preflight завершился с ошибкой: " + error.message);')
    content = content.replace('setText(\'assistantMeta\', \'Preflight завершился с ошибкой\');', 'setText(\'assistantMeta\', \'Preflight завершился с ошибкой: \' + error.message);')

    # 4. Refresh timestamp alignment
    # Check if we need to add a timestamp to the header or keep it in 'updated_at'
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Updated {path}")

process_file('src/web/index.html')
process_file('src/web/prototypes/nano/index_redesign.html')
