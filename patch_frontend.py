import re
import sys

def patch_html(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Russian Localization - OpenClaw Control Center
    replacements = {
        "Route & Model": "Маршрут и Модель",
        "Routing Mode:": "Режим роутинга:",
        "Local Node:": "Локальный узел:",
        "Cloud Slots:": "Облачные слоты:",
        "Channels Health": "Состояние каналов",
        "Autoswitch & Queue": "Автопереключение и Очередь",
        "Status:": "Статус:",
        "Last Switch:": "Последнее перекл.:",
        "Reason:": "Причина:",
        "Local Model Engine": "Движок локальной модели",
        "Loaded Model:": "Загруженная модель:",
        "Load Default": "Загрузить по умолчанию",
        "Unload": "Выгрузить",
        "Sync Engine State": "Синхронизировать статус",
        "Cloud Keys Diagnostics": "Диагностика облачных ключей",
        "Deep Check OpenClaw Connection": "Глубокая проверка подключения OpenClaw",
        "Cloud Probe": "Проверка облака",
        "Apply Autoswitch": "Применить автопереключение",
        "Set Local": "Режим Local",
        "Set Auto": "Режим Auto",
        "Set Cloud": "Режим Cloud",
        "Refresh Status": "Обновить статус",
        '<span class="card-meta" id="ocMeta" style="margin-left: auto; color: var(--text-main)">Ready</span>': '<span class="card-meta" id="ocMeta" style="margin-left: auto; color: var(--text-main)">Готов</span>',
        "Ready": "Готов"
    }
    
    # 2. Russian Localization - AI Assistant Interface
    replacements.update({
        "AI Assistant Interface": "Интерфейс AI Ассистента",
        "Reload Catalog": "Обновить каталог",
        "Apply Mode": "Применить режим",
        "Apply Preset": "Применить пресет",
        "Save Slot": "Сохранить слот",
        "Model catalog loaded.": "Каталог моделей загружен.",
        "API Key (optional)": "API Ключ (опционально)",
        "Enter task, prompt, or code snippet here...": "Введите задачу, промпт или фрагмент кода...",
        "Plan (Preflight)": "План (Preflight)",
        "Готов к запуску": "Готов к запуску",
        "Submit Feedback": "Отправить отзыв",
        "Show Stats": "Показать стату",
        "Score is applied to the latest engine run profile.": "Оценка применяется к последнему профилю запуска движка.",
        "Feedback notes...": "Комментарии к отзыву...",
        "Profile (opt)": "Профиль (опц)"
    })

    # 3. Russian Localization - Quick Utilities
    replacements.update({
        "Быстрые утилиты": "Быстрые утилиты",
        "Поисковый запрос...": "Поисковый запрос...",
        "Тема deep research...": "Тема deep research...",
        "URL для разбора...": "URL для разбора...",
        "Выбрать файл для загрузки": "Выбрать файл для загрузки",
        "Прикрепить файл": "Прикрепить файл",
        "Поиск": "Поиск",
        "Глубокое исследование": "Глубокое исследование",
        "Анализ URL": "Анализ URL"
    })

    for eng, ru in replacements.items():
        # Careful replacement to not break classes or logic.
        # Replacing mostly exact matches of text.
        content = content.replace(f">{eng}<", f">{ru}<")
        content = content.replace(f'"{eng}"', f'"{ru}"')
        content = content.replace(f"'{eng}'", f"'{ru}'")
        content = content.replace(f"placeholder=\"{eng}\"", f"placeholder=\"{ru}\"")
        content = content.replace(f"aria-label=\"{eng}\"", f"aria-label=\"{ru}\"")
    
    # Some specific replacements
    content = content.replace("Route &amp; Model", "Маршрут и Модель")
    content = content.replace(">Route & Model<", ">Маршрут и Модель<")
    content = content.replace(">Routing Mode:<", ">Режим роутинга:<")
    content = content.replace(">Local Node:<", ">Локальный узел:<")
    content = content.replace(">Cloud Slots:<", ">Облачные слоты:<")
    content = content.replace(">Channels Health<", ">Состояние каналов<")
    content = content.replace(">Autoswitch & Queue<", ">Автопереключение и Очередь<")
    content = content.replace(">Autoswitch &amp; Queue<", ">Автопереключение и Очередь<")
    content = content.replace(">Status:<", ">Статус:<")
    content = content.replace(">Last Switch:<", ">Последнее перекл.:<")
    content = content.replace(">Reason:<", ">Причина:<")
    content = content.replace(">Local Model Engine<", ">Движок локальной модели<")
    content = content.replace(">Loaded Model:<", ">Загруженная модель:<")
    content = content.replace(">Load Default<", ">Загрузить по умолчанию<")
    content = content.replace(">Unload<", ">Выгрузить<")
    content = content.replace(">Sync Engine State<", ">Синхронизировать статус<")
    content = content.replace(">Cloud Keys Diagnostics<", ">Диагностика облачных ключей<")
    content = content.replace(">Deep Check OpenClaw Connection<", ">Глубокая проверка OpenClaw<")
    content = content.replace(">Cloud Probe<", ">Проверка облака<")
    content = content.replace(">Apply Autoswitch<", ">Применить автопереключение<")
    content = content.replace(">Set Local<", ">Режим Local<")
    content = content.replace(">Set Auto<", ">Режим Auto<")
    content = content.replace(">Set Cloud<", ">Режим Cloud<")
    content = content.replace(">Refresh Status<", ">Обновить статус<")
    content = content.replace(">Ready<", ">Готов<")
    content = content.replace(">AI Assistant Interface<", ">Интерфейс AI Ассистента<")
    content = content.replace(">Reload Catalog<", ">Обновить каталог<")
    content = content.replace(">Apply Mode<", ">Применить режим<")
    content = content.replace(">Apply Preset<", ">Применить пресет<")
    content = content.replace(">Save Slot<", ">Сохранить слот<")
    content = content.replace(">Model catalog loaded.<", ">Каталог моделей загружен.<")
    content = content.replace("placeholder=\"API Key (optional)\"", "placeholder=\"API Ключ (опционально)\"")
    content = content.replace("placeholder=\"Enter task, prompt, or code snippet here...\"", "placeholder=\"Введите задачу, промпт или фрагмент кода...\"")
    content = content.replace(">Plan (Preflight)<", ">План (Preflight)<")
    content = content.replace(">Submit Feedback<", ">Отправить отзыв<")
    content = content.replace(">Show Stats<", ">Показать стату<")
    content = content.replace(">Score is applied to the latest engine run profile.<", ">Оценка применяется к последнему профилю запуска движка.<")
    content = content.replace("placeholder=\"Feedback notes...\"", "placeholder=\"Комментарии к отзыву...\"")
    content = content.replace("placeholder=\"Profile (opt)\"", "placeholder=\"Профиль (опц)\"")
    content = content.replace("OpenClaw (OpenAI):", "OpenClaw (OpenAI):")
    content = content.replace("Google Gemini:", "Google Gemini:")
    

    # 4. Insert Block 1 and Block 2
    block_1_2 = """
        <!-- Control UI Compat -->
        <div class="card state-container"
          style="background: var(--bg-surface); padding: 20px; border: 1px solid var(--border-subtle); box-shadow: inset 0 0 20px rgba(0,0,0,0.3);">
          <h4 class="form-label" style="margin-bottom: 16px; display: flex; align-items: center; gap: 8px;">
            Совместимость Control UI
          </h4>
          <div style="display: flex; flex-direction: column; gap: 12px">
            <div
              style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Runtime:</span>
              <span id="ocCompatRuntime" class="card-value" style="font-size: 0.95rem;">—</span>
            </div>
            <div
              style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Schema Warning:</span>
              <span id="ocCompatWarn" class="badge ok">—</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Уровень влияния:</span>
              <span id="ocCompatImpact" class="card-value text-ellipsis"
                style="font-size: 0.9rem; max-width: 140px; text-align: right; color: var(--text-muted);">—</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              <span class="card-meta" style="margin: 0;">Рекомендация:</span>
              <span id="ocCompatAction" class="card-value text-ellipsis"
                style="font-size: 0.85rem; text-align: left; color: var(--accent-cyan); white-space: normal;">—</span>
            </div>
          </div>
        </div>

        <!-- Effective Routing -->
        <div class="card state-container"
          style="background: var(--bg-surface); padding: 20px; border: 1px solid var(--border-subtle); box-shadow: inset 0 0 20px rgba(0,0,0,0.3);">
          <h4 class="form-label" style="margin-bottom: 16px; display: flex; align-items: center; gap: 8px; color: var(--accent-orange);">
            Эффективный роутинг
          </h4>
          <div style="display: flex; flex-direction: column; gap: 12px">
            <div
              style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Запрошено:</span>
              <span id="ocrouteReq" class="badge">—</span>
            </div>
            <div
              style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Эффективный:</span>
              <span id="ocrouteEff" class="badge" style="border-color: var(--accent-cyan); color: var(--accent-cyan);">—</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Активный слот/модель:</span>
              <span id="ocrouteActive" class="card-value text-ellipsis"
                style="font-size: 0.9rem; max-width: 140px; text-align: right;">—</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border-card); padding-bottom: 8px;">
              <span class="card-meta" style="margin: 0;">Cloud Fallback:</span>
              <span id="ocrouteFallback" class="card-value"
                style="font-size: 0.9rem; color: var(--text-muted);">—</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              <span class="card-meta" style="margin: 0;">Decision notes:</span>
              <span id="ocrouteNotes" class="card-value"
                style="font-size: 0.8rem; text-align: left; color: var(--text-muted); white-space: normal;">—</span>
            </div>
          </div>
        </div>
"""

    if 'Совместимость Control UI' not in content:
        # Insert after the "Local Model Engine" card but before "Cloud Keys Diagnostics"
        insert_marker_pos = content.find('<!-- Cloud Keys Diagnostics [R17] -->')
        if insert_marker_pos != -1:
             content = content[:insert_marker_pos] + block_1_2 + '\n' + content[insert_marker_pos:]
        else:
             print("Could not find insert marker!")

    # 5. Insert JS Functions
    js_fns = """
    async function loadControlCompatStatus(showStatus = true) {
      try {
        const res = await fetch("/api/openclaw/control-compat/status");
        if (res.ok) {
          const payload = await res.json();
          const root = document.getElementById("ocCompatRuntime");
          const warnBadge = document.getElementById("ocCompatWarn");
          const actionText = document.getElementById("ocCompatAction");
          
          if (root) {
             const st = payload.runtime_status || "UNKNOWN";
             if (st === "OK" && payload.impact_level === "ui_only") {
                 root.innerHTML = `<span class="badge ok">OK</span>`;
             } else {
                 const stClass = st === 'OK' ? 'ok' : 'bad';
                 root.innerHTML = `<span class="badge ${stClass}">${st}</span>`;
             }
          }
          if (warnBadge) {
             const hasWarn = payload.has_schema_warning;
             warnBadge.textContent = hasWarn ? "Да" : "Нет";
             warnBadge.className = hasWarn ? "badge warn" : "badge ok";
          }
          if (actionText) actionText.textContent = payload.recommended_action || "-";
          
          let impactRU = "нет";
          if (payload.impact_level === "ui_only") impactRU = "только UI";
          else if (payload.impact_level === "runtime_risk") impactRU = "риск runtime";
          setText("ocCompatImpact", impactRU);
        }
      } catch (e) {
          console.error("Control Compat Error:", e);
      }
    }

    async function loadEffectiveRouting(showStatus = true) {
       try {
         const res = await fetch("/api/openclaw/routing/effective");
         if (res.ok) {
            const data = await res.json();
            setText("ocrouteReq", data.requested_mode || "-");
            setText("ocrouteEff", data.effective_mode || "-");
            setText("ocrouteActive", data.active_slot_or_model || "-");
            setText("ocrouteFallback", data.cloud_fallback ? "Да" : "Нет");
            setText("ocrouteNotes", data.decision_notes || "-");
         }
       } catch(e) {
           console.error("Effective Routing Error:", e);
       }
    }
    """

    if 'async function loadControlCompatStatus' not in content:
        insert_js_pos = content.find('async function loadOpenclawStatus')
        if insert_js_pos != -1:
            content = content[:insert_js_pos] + js_fns + '\n' + content[insert_js_pos:]
            
            # update loadOpenclawStatus to call them, or update refreshAll
            # Since loadOpenclawStatus is called to refresh OpenClaw specific status, let's call them there.
            # Right after `if (showStatus) setText("ocMeta", "Загружаю статус OpenClaw...");`
            content = content.replace(
                'if (showStatus) setText("ocMeta", "Загружаю статус OpenClaw...");\n',
                'if (showStatus) setText("ocMeta", "Загружаю статус OpenClaw...");\n        loadControlCompatStatus(false);\n        loadEffectiveRouting(false);\n'
            )
        else:
            print("Could not find js insert marker!")

    # 6. Replace assistantMeta payload logic
    # Find: setText("assistantMeta", `OK [${sourceLabel} | ${elapsedS}s] • режим: ${effectiveMode} • профиль: ${payload.profile || "-"} • task: ${payload.task_type || "-"} • depth: ${depthRaw}`);
    # Replacing with: setText("assistantMeta", `Маршрут: ${effectiveMode} • OK [${sourceLabel} | ${elapsedS}s] • профиль: ${payload.profile || "-"} • task: ${payload.task_type || "-"} • depth: ${depthRaw}`);
    
    old_assistant_meta = 'setText(\n          "assistantMeta",\n          `OK [${sourceLabel} | ${elapsedS}s] • режим: ${effectiveMode} • профиль: ${payload.profile || "-"} • task: ${payload.task_type || "-"} • depth: ${depthRaw}`,\n        );'
    
    new_assistant_meta = 'setText(\n          "assistantMeta",\n          `Маршрут: ${effectiveMode} • OK [${sourceLabel} | ${elapsedS}s] • профиль: ${payload.profile || "-"} • task: ${payload.task_type || "-"} • depth: ${depthRaw}`,\n        );'
    
    if old_assistant_meta in content:
         content = content.replace(old_assistant_meta, new_assistant_meta)
    else:
         # let's try regex if simple replace fails
         content = re.sub(
             r'setText\(\s*"assistantMeta",\s*`OK \[\$\{sourceLabel\} \| \$\{elapsedS\}s\] • режим: \$\{effectiveMode\} • профиль: \$\{payload\.profile \|\| "-"\} • task: \$\{payload\.task_type \|\| "-"\} • depth: \$\{depthRaw\}`,\s*\);',
             r'setText("assistantMeta", `Маршрут: ${effectiveMode} • OK [${sourceLabel} | ${elapsedS}s] • профиль: ${payload.profile || "-"} • task: ${payload.task_type || "-"} • depth: ${depthRaw}`);',
             content, flags=re.MULTILINE
         )

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print("HTML patched successfully!")

if __name__ == '__main__':
    patch_html('/Users/pablito/Antigravity_AGENTS/Краб/src/web/index.html')
