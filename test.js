
        function badge(ok, label) {
            const cls = ok ? 'ok' : 'warn';
            return `<span class="badge ${cls}">${ok ? 'ON' : 'OFF'} ${label}</span>`;
        }

        function setText(id, value) {
            const node = document.getElementById(id);
            if (node) node.textContent = value;
        }

        async function fetchJson(url) {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        }

        function readWebApiKey() {
            return (document.getElementById('assistantApiKey').value || '').trim();
        }

        async function postJson(url, body, withKey = true, method = 'POST') {
            const headers = { 'Content-Type': 'application/json' };
            if (withKey) {
                const webApiKey = readWebApiKey();
                if (webApiKey) {
                    headers['X-Krab-Web-Key'] = webApiKey;
                }
            }
            const response = await fetch(url, {
                method,
                headers,
                body: method === 'DELETE' ? undefined : JSON.stringify(body || {})
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload?.detail || `HTTP ${response.status}`);
            }
            return payload;
        }

        let modelCatalogState = null;

        function normalizedForceMode(rawMode) {
            const normalized = String(rawMode || '').trim().toLowerCase();
            if (normalized === 'force_local' || normalized === 'local') return 'local';
            if (normalized === 'force_cloud' || normalized === 'cloud') return 'cloud';
            return 'auto';
        }

        function fillSelect(nodeId, options, selectedValue = '') {
            const node = document.getElementById(nodeId);
            if (!node) return;
            const current = String(selectedValue || '');
            const html = options.map((item) => {
                const value = String(item.value ?? '');
                const label = String(item.label ?? value);
                const selected = value === current ? ' selected' : '';
                return `<option value="${value}"${selected}>${label}</option>`;
            }).join('');
            node.innerHTML = html || '<option value="">(пусто)</option>';
        }

        function getModelOptionsFromCatalog(catalog) {
            const result = [];
            const localModels = Array.isArray(catalog?.local_models) ? catalog.local_models : [];
            const cloudPresets = Array.isArray(catalog?.cloud_presets) ? catalog.cloud_presets : [];
            const cloudSlots = catalog?.cloud_slots || {};

            for (const item of localModels) {
                const modelId = String(item?.id || '').trim();
                if (!modelId) continue;
                const loadedSuffix = item?.loaded ? ' • loaded' : '';
                const sizeSuffix = item?.size_human ? ` • ${item.size_human}` : '';
                result.push({
                    value: modelId,
                    label: `LOCAL • ${modelId}${loadedSuffix}${sizeSuffix}`,
                });
            }

            for (const item of cloudPresets) {
                const modelId = String(item?.id || '').trim();
                if (!modelId) continue;
                result.push({
                    value: modelId,
                    label: `CLOUD • ${modelId}`,
                });
            }

            for (const modelId of Object.values(cloudSlots)) {
                const text = String(modelId || '').trim();
                if (!text) continue;
                result.push({
                    value: text,
                    label: `CURRENT • ${text}`,
                });
            }

            const dedup = [];
            const seen = new Set();
            for (const item of result) {
                if (seen.has(item.value)) continue;
                seen.add(item.value);
                dedup.push(item);
            }
            return dedup;
        }

        function syncSlotModelSelection() {
            const slotNode = document.getElementById('modelSlotSelect');
            const modelNode = document.getElementById('modelChoiceSelect');
            if (!slotNode || !modelNode || !modelCatalogState) return;
            const slot = String(slotNode.value || '').trim();
            const currentModel = String((modelCatalogState.cloud_slots || {})[slot] || '').trim();
            if (!currentModel) return;
            const found = Array.from(modelNode.options).some((option) => option.value === currentModel);
            if (found) {
                modelNode.value = currentModel;
            }
        }

        function syncModelControls(catalog) {
            modelCatalogState = catalog || {};
            const mode = normalizedForceMode(catalog?.force_mode);
            const slotNames = Array.isArray(catalog?.slots) ? catalog.slots : [];
            const quickPresets = Array.isArray(catalog?.quick_presets) ? catalog.quick_presets : [];
            const selectedSlot = String(document.getElementById('modelSlotSelect')?.value || '');

            fillSelect(
                'modelModeSelect',
                [
                    { value: 'auto', label: 'auto (local-first)' },
                    { value: 'local', label: 'local (force_local)' },
                    { value: 'cloud', label: 'cloud (force_cloud)' },
                ],
                mode
            );
            fillSelect(
                'modelPresetSelect',
                quickPresets.length
                    ? quickPresets.map((item) => ({
                        value: item.id,
                        label: `${item.title} — ${item.description}`,
                    }))
                    : [{ value: 'balanced_auto', label: 'Balanced Auto' }],
                String(document.getElementById('modelPresetSelect')?.value || 'balanced_auto')
            );
            fillSelect(
                'modelSlotSelect',
                slotNames.length
                    ? slotNames.map((slot) => ({ value: slot, label: slot }))
                    : [{ value: 'chat', label: 'chat' }],
                selectedSlot && slotNames.includes(selectedSlot) ? selectedSlot : (slotNames[0] || 'chat')
            );

            const modelOptions = getModelOptionsFromCatalog(catalog);
            fillSelect(
                'modelChoiceSelect',
                modelOptions.length ? modelOptions : [{ value: '', label: '(модели не обнаружены)' }],
                ''
            );
            syncSlotModelSelection();

            const localState = catalog?.local_available ? 'local=online' : 'local=offline';
            const active = catalog?.local_active_model ? ` • active=${catalog.local_active_model}` : '';
            setText('modelControlMeta', `Каталог обновлен • mode=${mode} • ${localState}${active}`);
        }

        async function loadModelCatalog(showStatus = true) {
            try {
                const payload = await fetchJson('/api/model/catalog');
                if (!payload?.ok) {
                    throw new Error(payload?.error || 'model_catalog_failed');
                }
                syncModelControls(payload.catalog || {});
                if (showStatus) {
                    setText('modelControlMeta', 'Каталог моделей готов.');
                }
            } catch (error) {
                setText('modelControlMeta', `Ошибка каталога моделей: ${error.message}`);
            }
        }

        async function applyModelAction(action, extraPayload = {}) {
            try {
                setText('modelControlMeta', 'Применяю изменения...');
                const payload = await postJson('/api/model/apply', { action, ...extraPayload }, true, 'POST');
                if (!payload?.ok) {
                    throw new Error(payload?.error || 'model_apply_failed');
                }
                syncModelControls(payload.catalog || {});
                if (payload.message) {
                    document.getElementById('assistantOutput').textContent = String(payload.message);
                }
                setText('modelControlMeta', payload.message || 'Изменения применены.');
                await updateStats();
            } catch (error) {
                setText('modelControlMeta', `Ошибка применения: ${error.message}`);
            }
        }

        function appendPromptChunk(chunk) {
            const node = document.getElementById('assistantPrompt');
            const existing = String(node.value || '').trim();
            const incoming = String(chunk || '').trim();
            if (!incoming) return;
            node.value = existing ? `${existing}\n\n${incoming}` : incoming;
        }

        async function attachSelectedFileToPrompt() {
            const input = document.getElementById('assistantFileInput');
            const files = input?.files || [];
            if (!files.length) {
                setText('quickActionMeta', 'Выбери файл перед добавлением в prompt.');
                return;
            }
            const file = files[0];
            try {
                setText('quickActionMeta', `Загружаю файл: ${file.name}...`);
                const formData = new FormData();
                formData.append('file', file);

                const headers = {};
                const webApiKey = readWebApiKey();
                if (webApiKey) {
                    headers['X-Krab-Web-Key'] = webApiKey;
                }

                const response = await fetch('/api/assistant/attachment', {
                    method: 'POST',
                    headers,
                    body: formData,
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload?.detail || `HTTP ${response.status}`);
                }
                const attachment = payload?.attachment || {};
                appendPromptChunk(String(attachment.prompt_snippet || ''));
                setText(
                    'quickActionMeta',
                    `Файл добавлен: ${attachment.file_name || file.name} • ${attachment.kind || 'attachment'}`
                );
            } catch (error) {
                setText('quickActionMeta', `Ошибка загрузки файла: ${error.message}`);
            }
        }

        async function runQuickWebSearch() {
            const query = (document.getElementById('quickWebQuery').value || '').trim();
            if (!query) {
                setText('quickActionMeta', 'Введи запрос для web-search.');
                return;
            }
            document.getElementById('assistantTaskType').value = 'chat';
            document.getElementById('assistantReasoningDepth').value = 'balanced';
            appendPromptChunk(
`Сделай web-search по теме: "${query}".
Нужен краткий отчет: ключевые факты, 3-5 надежных источников и практические выводы.`
            );
            setText('quickActionMeta', 'Шаблон web-search подготовлен. Запускаю...');
            await assistantQuery();
        }

        async function runQuickDeepResearch() {
            const topic = (document.getElementById('quickDeepTopic').value || '').trim();
            if (!topic) {
                setText('quickActionMeta', 'Введи тему для deep research.');
                return;
            }
            document.getElementById('assistantTaskType').value = 'reasoning';
            document.getElementById('assistantReasoningDepth').value = 'deep';
            appendPromptChunk(
`Deep research по теме: "${topic}".
Собери структурированный отчет:
1) краткое резюме,
2) ключевые риски/возможности,
3) сравнение подходов,
4) пошаговый план действий,
5) ссылки на проверяемые источники.`
            );
            setText('quickActionMeta', 'Шаблон deep research подготовлен. Запускаю...');
            await assistantQuery();
        }

        async function runQuickUrlReview() {
            const sourceUrl = (document.getElementById('quickSourceUrl').value || '').trim();
            if (!sourceUrl) {
                setText('quickActionMeta', 'Введи URL для разбора.');
                return;
            }
            document.getElementById('assistantTaskType').value = 'reasoning';
            document.getElementById('assistantReasoningDepth').value = 'balanced';
            appendPromptChunk(
`Разбери материал по ссылке: ${sourceUrl}
Нужно:
- краткое содержание;
- ключевые тезисы/факты;
- что это значит practically для меня;
- если это YouTube: выдели главные выводы и тайм-коды (если доступны).`
            );
            setText('quickActionMeta', 'Шаблон URL-анализа подготовлен. Запускаю...');
            await assistantQuery();
        }

        async function opsAckAction(isAck) {
            const code = (document.getElementById('opsAlertCode').value || '').trim();
            const note = (document.getElementById('opsAlertNote').value || '').trim();
            if (!code) {
                setText('opsActionMeta', 'Ошибка: укажи alert code');
                return;
            }
            setText('opsActionMeta', `${isAck ? 'Ack' : 'Unack'} в процессе...`);
            try {
                if (isAck) {
                    await postJson(`/api/ops/ack/${encodeURIComponent(code)}`, { actor: 'web_panel', note });
                    setText('opsActionMeta', `OK: alert ${code} подтвержден`);
                } else {
                    await postJson(`/api/ops/ack/${encodeURIComponent(code)}`, {}, true, 'DELETE');
                    setText('opsActionMeta', `OK: alert ${code} снят с подтверждения`);
                }
                await updateStats();
            } catch (error) {
                setText('opsActionMeta', `Ошибка: ${error.message}`);
            }
        }

        async function assistantQuery() {
            const prompt = (document.getElementById('assistantPrompt').value || '').trim();
            const taskTypeRaw = (document.getElementById('assistantTaskType').value || 'chat').trim();
            const depthRaw = (document.getElementById('assistantReasoningDepth').value || 'auto').trim().toLowerCase();
            const ragRaw = (document.getElementById('assistantUseRag').value || 'off').trim().toLowerCase();
            const webApiKey = (document.getElementById('assistantApiKey').value || '').trim();
            let taskType = taskTypeRaw || 'chat';
            const confirmExpensive = depthRaw === 'deep';

            // UX: depth=deep автоматически переводит chat -> reasoning.
            if (taskType === 'chat' && depthRaw === 'deep') {
                taskType = 'reasoning';
            }

            if (!prompt) {
                setText('assistantMeta', 'Ошибка: пустой prompt');
                return;
            }

            const useRag = ['1', 'on', 'true', 'yes', 'y'].includes(ragRaw);
            const headers = { 'Content-Type': 'application/json' };
            if (webApiKey) {
                headers['X-Krab-Web-Key'] = webApiKey;
            }

            setText('assistantMeta', 'Выполняю запрос...');
            document.getElementById('assistantOutput').textContent = '⏳ Выполнение...';

            try {
                const response = await fetch('/api/assistant/query', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({
                        prompt,
                        task_type: taskType || 'chat',
                        use_rag: useRag,
                        confirm_expensive: confirmExpensive
                    })
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload?.detail || `HTTP ${response.status}`);
                }

                document.getElementById('assistantOutput').textContent = payload.reply || '(пустой ответ)';
                setText(
                    'assistantMeta',
                    `OK • профиль: ${payload.profile || '-'} • task: ${payload.task_type || '-'} • depth: ${depthRaw}`
                );
                const lastRoute = payload.last_route || {};
                if (lastRoute.profile) {
                    document.getElementById('feedbackProfile').value = String(lastRoute.profile);
                }
                if (lastRoute.model || lastRoute.channel) {
                    setText(
                        'feedbackMeta',
                        `Последний прогон: ${lastRoute.model || '-'} (${lastRoute.channel || '-'})`
                    );
                }
            } catch (error) {
                document.getElementById('assistantOutput').textContent = `Ошибка: ${error.message}`;
                setText('assistantMeta', 'Запрос завершился с ошибкой');
            }
        }

        async function assistantPreflight() {
            const prompt = (document.getElementById('assistantPrompt').value || '').trim();
            const taskTypeRaw = (document.getElementById('assistantTaskType').value || 'chat').trim();
            const depthRaw = (document.getElementById('assistantReasoningDepth').value || 'auto').trim().toLowerCase();
            const webApiKey = (document.getElementById('assistantApiKey').value || '').trim();
            const selectedModel = (document.getElementById('modelChoiceSelect').value || '').trim();
            let taskType = taskTypeRaw || 'chat';
            const confirmExpensive = depthRaw === 'deep';

            if (taskType === 'chat' && depthRaw === 'deep') {
                taskType = 'reasoning';
            }

            if (!prompt) {
                setText('assistantMeta', 'Ошибка: пустой prompt');
                return;
            }

            const headers = { 'Content-Type': 'application/json' };
            if (webApiKey) {
                headers['X-Krab-Web-Key'] = webApiKey;
            }

            setText('assistantMeta', 'Собираю preflight-план...');
            document.getElementById('assistantOutput').textContent = '⏳ Анализ маршрута...';

            try {
                const response = await fetch('/api/model/preflight', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({
                        prompt,
                        task_type: taskType || 'chat',
                        preferred_model: selectedModel || null,
                        confirm_expensive: confirmExpensive
                    })
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload?.detail || `HTTP ${response.status}`);
                }
                if (!payload?.ok) {
                    throw new Error(payload?.error || 'preflight_failed');
                }

                const plan = payload.preflight || {};
                const execution = plan.execution || {};
                const warnings = plan.warnings || [];
                const reasons = plan.reasons || [];
                const warningText = warnings.length ? warnings.map((item) => `- ${item}`).join('\n') : '- нет';
                const reasonText = reasons.length ? reasons.map((item) => `- ${item}`).join('\n') : '- нет';

                document.getElementById('assistantOutput').textContent =
`Preflight:
task_type=${plan.task_type || '-'}
profile=${plan.profile || '-'}
critical=${plan.critical ? 'yes' : 'no'}
channel=${execution.channel || '-'}
model=${execution.model || '-'}
can_run_now=${execution.can_run_now ? 'yes' : 'no'}
requires_confirm=${execution.requires_confirm_expensive ? 'yes' : 'no'}
marginal_cost_usd=${plan.cost_hint?.marginal_call_cost_usd ?? 0}

reasons:
${reasonText}

warnings:
${warningText}

next_step: ${plan.next_step || 'Можно запускать задачу.'}`;
                setText(
                    'assistantMeta',
                    `Preflight OK • профиль: ${plan.profile || '-'} • канал: ${execution.channel || '-'} • depth: ${depthRaw}`
                );
            } catch (error) {
                document.getElementById('assistantOutput').textContent = `Ошибка preflight: ${error.message}`;
                setText('assistantMeta', 'Preflight завершился с ошибкой');
            }
        }

        async function submitModelFeedback() {
            const scoreRaw = (document.getElementById('feedbackScore').value || '').trim();
            const profileRaw = (document.getElementById('feedbackProfile').value || '').trim();
            const noteRaw = (document.getElementById('feedbackNote').value || '').trim();
            const score = Number.parseInt(scoreRaw, 10);

            if (!Number.isInteger(score) || score < 1 || score > 5) {
                setText('feedbackMeta', 'Ошибка: score должен быть от 1 до 5');
                return;
            }

            setText('feedbackMeta', 'Сохраняю feedback...');
            try {
                const payload = { score, note: noteRaw };
                if (profileRaw) {
                    payload.profile = profileRaw.toLowerCase();
                }
                const response = await postJson('/api/model/feedback', payload, true, 'POST');
                if (!response?.ok) {
                    throw new Error(response?.error || 'feedback_submit_failed');
                }

                const result = response.result || {};
                setText(
                    'feedbackMeta',
                    `OK: ${result.model || '-'} (${result.profile || '-'}) → ${result.profile_model_stats?.avg ?? 0}/5`
                );
            } catch (error) {
                setText('feedbackMeta', `Ошибка feedback: ${error.message}`);
            }
        }

        async function loadModelFeedbackStats() {
            const profileRaw = (document.getElementById('feedbackProfile').value || '').trim();
            const query = profileRaw ? `?profile=${encodeURIComponent(profileRaw.toLowerCase())}&top=5` : '?top=5';
            setText('feedbackMeta', 'Загружаю feedback-статистику...');

            try {
                const payload = await fetchJson(`/api/model/feedback${query}`);
                if (!payload?.ok) {
                    throw new Error(payload?.error || 'feedback_summary_failed');
                }
                const feedback = payload.feedback || {};
                const models = feedback.top_models || [];
                const channels = feedback.top_channels || [];
                const modelsText = models.length
                    ? models.map((item) => `- ${item.model} (${item.profile}): ${item.avg_score}/5, n=${item.count}`).join('\n')
                    : '- нет данных';
                const channelsText = channels.length
                    ? channels.map((item) => `- ${item.channel}: ${item.avg_score}/5, n=${item.count}`).join('\n')
                    : '- нет данных';
                document.getElementById('assistantOutput').textContent =
`Feedback summary:
profile=${feedback.profile || 'all'}
total_feedback=${feedback.total_feedback || 0}

top models:
${modelsText}

top channels:
${channelsText}`;
                setText('feedbackMeta', `Feedback loaded • total=${feedback.total_feedback || 0}`);
            } catch (error) {
                setText('feedbackMeta', `Ошибка feedback stats: ${error.message}`);
            }
        }

        async function updateStats() {
            try {
                const [stats, health, links, recommend, feedbackPayload, usagePayload, alertsPayload, historyPayload] = await Promise.all([
                    fetchJson('/api/stats'),
                    fetchJson('/api/health'),
                    fetchJson('/api/links'),
                    fetchJson('/api/model/recommend?profile=chat'),
                    fetchJson('/api/model/feedback?profile=chat&top=1'),
                    fetchJson('/api/ops/usage'),
                    fetchJson('/api/ops/alerts'),
                    fetchJson('/api/ops/history?limit=8')
                ]);

                const bb = stats.black_box || {};
                const rag = stats.rag || {};
                const router = stats.router || {};

                setText('bb_total', String(bb.total ?? bb.total_messages ?? 0));
                setText('rag_total', String(rag.count ?? 0));
                setText('local_model', router.local_model || 'Offline');
                setText('degradation', health.degradation || 'unknown');

                const checks = health.checks || {};
                document.getElementById('checks').innerHTML =
                    badge(!!checks.openclaw, 'OpenClaw') +
                    badge(!!checks.local_lm, 'Local LM') +
                    badge(!!checks.voice_gateway, 'Voice Gateway') +
                    badge(!!checks.krab_ear, 'Krab Ear');

                const linksBlock = [
                    ['Dashboard', links.dashboard],
                    ['Stats API', links.stats_api],
                    ['Health API', links.health_api],
                    ['Ecosystem Health API', links.ecosystem_health_api],
                    ['Model Preflight API', `${links.dashboard}/api/model/preflight`],
                    ['Model Feedback API', `${links.dashboard}/api/model/feedback`],
                    ['OpenClaw Deep Check', `${links.dashboard}/api/openclaw/deep-check`],
                    ['OpenClaw Remediation Plan', `${links.dashboard}/api/openclaw/remediation-plan`],
                    ['OpenClaw Browser Smoke', `${links.dashboard}/api/openclaw/browser-smoke`],
                    ['Assistant Capabilities', `${links.dashboard}/api/assistant/capabilities`],
                    ['Ops Cost Report', `${links.dashboard}/api/ops/cost-report`],
                    ['Ops Executive Summary', `${links.dashboard}/api/ops/executive-summary`],
                    ['Ops Full Report', `${links.dashboard}/api/ops/report`],
                    ['Ops Bundle Export', `${links.dashboard}/api/ops/bundle/export`],
                    ['OpenClaw', links.openclaw],
                    ['Voice Gateway', links.voice_gateway],
                ].filter(([, url]) => !!url)
                 .map(([name, url]) => `<a href="${url}" target="_blank" rel="noreferrer">${name}: ${url}</a>`)
                 .join('');
                document.getElementById('links').innerHTML = linksBlock || '—';

                setText('recommended_model', recommend.model || '—');
                const bestFeedback = feedbackPayload?.feedback?.top_models?.[0] || null;
                if (bestFeedback) {
                    setText(
                        'recommended_meta',
                        `Профиль: ${recommend.profile || 'chat'} • Канал: ${recommend.channel || 'auto'} • Quality: ${bestFeedback.avg_score}/5 (n=${bestFeedback.count})`
                    );
                } else {
                    setText('recommended_meta', `Профиль: ${recommend.profile || 'chat'} • Канал: ${recommend.channel || 'auto'}`);
                }

                const usage = usagePayload.usage || {};
                const totals = usage.totals || {};
                const ratios = usage.ratios || {};
                const softCap = usage.soft_cap || {};
                setText(
                    'opsUsageMeta',
                    `Calls: total=${totals.all_calls ?? 0}, cloud=${totals.cloud_calls ?? 0}, local=${totals.local_calls ?? 0} • cloud_share=${ratios.cloud_share ?? 0} • cap_left=${softCap.cloud_remaining_calls ?? 0}`
                );

                const alertsRoot = alertsPayload.alerts || {};
                const alertsList = alertsRoot.alerts || [];
                if (!alertsList.length) {
                    document.getElementById('opsAlerts').innerHTML = '<span class="badge ok">OK no active alerts</span>';
                } else {
                    const html = alertsList
                        .map((item) => {
                            const ack = item.acknowledged ? ` <span class="badge ok">ACK ${item.ack?.actor || ''}</span>` : '';
                            return `<div><span class="badge ${item.severity === 'high' ? 'bad' : 'warn'}">${item.severity || 'info'}</span> ${item.code || ''} — ${item.message || ''}${ack}</div>`;
                        })
                        .join('');
                    document.getElementById('opsAlerts').innerHTML = html;
                }

                const history = historyPayload.history?.items || [];
                if (!history.length) {
                    setText('opsHistory', 'История ops: пусто');
                } else {
                    document.getElementById('opsHistory').innerHTML = history
                        .map((item) => `${item.ts || '-'} • ${item.status || '-'} • alerts=${item.alerts_count ?? 0} • codes=${(item.codes || []).join(',') || '-'}`)
                        .join('<br>');
                }
                setText('updated_at', `Обновлено: ${new Date().toLocaleTimeString()}`);
            } catch (error) {
                console.error('Stats update failed', error);
                setText('updated_at', `Ошибка обновления: ${error.message}`);
            }
        }

        async function refreshAll() {
            await updateStats();
            await loadModelCatalog(false);
        }

        document.getElementById('refreshBtn').addEventListener('click', refreshAll);
        document.getElementById('assistantRunBtn').addEventListener('click', assistantQuery);
        document.getElementById('assistantPlanBtn').addEventListener('click', assistantPreflight);
        document.getElementById('feedbackSendBtn').addEventListener('click', submitModelFeedback);
        document.getElementById('feedbackStatsBtn').addEventListener('click', loadModelFeedbackStats);
        document.getElementById('opsAckBtn').addEventListener('click', () => opsAckAction(true));
        document.getElementById('opsUnackBtn').addEventListener('click', () => opsAckAction(false));
        document.getElementById('modelCatalogReloadBtn').addEventListener('click', () => loadModelCatalog(true));
        document.getElementById('modelApplyModeBtn').addEventListener('click', async () => {
            const mode = (document.getElementById('modelModeSelect').value || 'auto').trim();
            await applyModelAction('set_mode', { mode });
        });
        document.getElementById('modelApplyPresetBtn').addEventListener('click', async () => {
            const preset = (document.getElementById('modelPresetSelect').value || '').trim();
            const localModel = (document.getElementById('modelChoiceSelect').value || '').trim();
            await applyModelAction('apply_preset', { preset, local_model: localModel });
        });
        document.getElementById('modelApplySlotBtn').addEventListener('click', async () => {
            const slot = (document.getElementById('modelSlotSelect').value || '').trim();
            const model = (document.getElementById('modelChoiceSelect').value || '').trim();
            await applyModelAction('set_slot_model', { slot, model });
        });
        document.getElementById('modelSlotSelect').addEventListener('change', syncSlotModelSelection);
        document.getElementById('assistantAttachBtn').addEventListener('click', attachSelectedFileToPrompt);
        document.getElementById('quickWebBtn').addEventListener('click', runQuickWebSearch);
        document.getElementById('quickDeepBtn').addEventListener('click', runQuickDeepResearch);
        document.getElementById('quickUrlBtn').addEventListener('click', runQuickUrlReview);
        setInterval(updateStats, 8000);
        refreshAll();
    