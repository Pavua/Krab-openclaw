/* Krab v4 panel i18n
 *
 * Pattern (adopted from theme-toggle.js):
 *   - localStorage('krab_lang') persists choice across reloads.
 *   - <html lang="ru|en"> set BEFORE DOMContentLoaded → no flash of wrong locale.
 *   - Static HTML: <element data-i18n="key">RU default text</element>
 *                  <button data-i18n-title="key.tip">RU title attr</button>
 *                  <input data-i18n-placeholder="key.placeholder">
 *   - Dynamic JS: window.t('namespace.key')  → returns translated string,
 *                 falls back to key path if missing.
 *
 * Add new keys to TRANSLATIONS below. Two-language structure: { en: {...}, ru: {...} }.
 * If key missing in current lang → fallback to ru (project default), then to key path.
 *
 * Toggle button auto-injected into .nav-bell-wrapper next to theme toggle on
 * DOMContentLoaded. Click cycles ru ↔ en.
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'krab_lang';
  const DEFAULT_LANG = 'ru';
  const SUPPORTED = ['ru', 'en'];

  // ─────────────────────────────────────────────────────────────────────
  // Translation table — extend per-page as needed.
  // Convention: section.subsection.key (dot-namespaced).
  // ─────────────────────────────────────────────────────────────────────
  const TRANSLATIONS = {
    en: {
      nav: {
        hub: 'Hub', chat: 'Chat', costs: 'Costs', inbox: 'Inbox',
        swarm: 'Swarm', translator: 'Translator', trans_short: 'Trans',
        ops: 'Ops', research: 'Research', settings: 'Settings', commands: 'Commands',
        notifications_title: 'Notifications'
      },
      common: {
        connecting: 'Connecting…', updated: 'Updated', loading: 'Loading…',
        empty: 'No data', error: 'Error', success: 'Success',
        cancel: 'Cancel', confirm: 'Confirm', save: 'Save', close: 'Close'
      },
      time: { sec_ago: 'sec ago', min_ago: 'min ago', h_ago: 'h ago', d_ago: 'd ago' },
      lang: { switch_tip: 'Switch to Russian', label: 'EN' },
      theme: { switch_to_dark: 'Switch to dark theme', switch_to_light: 'Switch to light theme' },

      inbox: {
        title: 'Inbox', empty_for: 'No items found in category',
        body_no_details: 'No further details available.',
        action_remediate_stale: 'Remediate Stale',
        action_remediate_stale_tip: 'Run auto-cleanup of stale pending events',
        action_ack_all: 'Ack All Open',
        action_ack_all_tip: 'Acknowledge all open events at once (bulk operation)',
        tab_all: 'All', tab_all_tip: 'All events (including acknowledged)',
        tab_open: 'Open', tab_open_tip: 'Open — awaiting owner reaction',
        tab_acked: 'Acked', tab_acked_tip: 'Acknowledged — viewed but not closed',
        tab_processing: 'Processing', tab_processing_tip: 'Processing — Krab/swarm currently handling',
        stat_open: 'Open', stat_attention: 'Attention', stat_escalations: 'Escalations', stat_stale: 'Stale',
        btn_ack: 'Ack', btn_ack_tip: 'Mark as viewed (no action required)',
        btn_done: 'Done', btn_done_tip: 'Task completed / resolved',
        btn_dismiss: 'Dismiss', btn_dismiss_tip: 'Event irrelevant / false positive',
        badge_acked: 'Acked', badge_acked_tip: 'Event acknowledged by owner',
        sev_critical: 'Critical — needs immediate attention',
        sev_warning: 'Warning — worth checking when convenient',
        sev_info: 'Informational — for context',
        kind_tip: 'Event type', source_tip: 'Source',
        toast_sse_error: 'SSE Error', toast_sse_disconnected: 'SSE disconnected',
        toast_executed: 'Executed', toast_error_executing: 'Error executing',
        toast_no_id: 'No item ID for action', toast_action_success: 'success', toast_action_error: 'Error'
      }
    },
    ru: {
      nav: {
        hub: 'Главная', chat: 'Чат', costs: 'Расходы', inbox: 'Входящие',
        swarm: 'Свёрм', translator: 'Переводчик', trans_short: 'Перев',
        ops: 'Операции', research: 'Поиск', settings: 'Настройки', commands: 'Команды',
        notifications_title: 'Уведомления'
      },
      common: {
        connecting: 'Подключение…', updated: 'Обновлено', loading: 'Загрузка…',
        empty: 'Нет данных', error: 'Ошибка', success: 'Готово',
        cancel: 'Отмена', confirm: 'Подтвердить', save: 'Сохранить', close: 'Закрыть'
      },
      time: { sec_ago: 'сек назад', min_ago: 'мин назад', h_ago: 'ч назад', d_ago: 'д назад' },
      lang: { switch_tip: 'Переключить на английский', label: 'РУ' },
      theme: { switch_to_dark: 'Переключить на тёмную тему', switch_to_light: 'Переключить на светлую тему' },

      inbox: {
        title: 'Входящие', empty_for: 'Ничего не найдено в категории',
        body_no_details: 'Дополнительной информации нет.',
        action_remediate_stale: 'Обработать устаревшие',
        action_remediate_stale_tip: 'Запустить авто-чистку устаревших pending событий',
        action_ack_all: 'Подтвердить открытые',
        action_ack_all_tip: 'Подтвердить все открытые события одним кликом (массовая операция)',
        tab_all: 'Все', tab_all_tip: 'Все события (включая принятые)',
        tab_open: 'Открытые', tab_open_tip: 'Открытые — ждут реакции владельца',
        tab_acked: 'Принятые', tab_acked_tip: 'Принятые — просмотрены, но не закрыты',
        tab_processing: 'В работе', tab_processing_tip: 'В работе — Krab/swarm сейчас обрабатывает',
        stat_open: 'Открытых', stat_attention: 'Внимание', stat_escalations: 'Эскалации', stat_stale: 'Устаревшие',
        btn_ack: 'Принять', btn_ack_tip: 'Отметить как просмотренное (не требует действия)',
        btn_done: 'Готово', btn_done_tip: 'Задача выполнена / решена',
        btn_dismiss: 'Отклонить', btn_dismiss_tip: 'Событие неактуально / ложное',
        badge_acked: 'Принято', badge_acked_tip: 'Событие подтверждено владельцем',
        sev_critical: 'Критично — требует немедленного внимания',
        sev_warning: 'Предупреждение — стоит проверить когда удобно',
        sev_info: 'Информационное — для контекста',
        kind_tip: 'Тип события', source_tip: 'Источник',
        toast_sse_error: 'Ошибка SSE', toast_sse_disconnected: 'SSE отключён',
        toast_executed: 'Выполнено', toast_error_executing: 'Ошибка выполнения',
        toast_no_id: 'Не указан ID для действия', toast_action_success: 'успешно', toast_action_error: 'Ошибка'
      }
    }
  };

  // ─────────────────────────────────────────────────────────────────────
  // Initialize current language BEFORE DOMContentLoaded (avoid FOUC).
  // ─────────────────────────────────────────────────────────────────────
  function getStoredLang() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED.includes(stored)) return stored;
    return DEFAULT_LANG;
  }

  let currentLang = getStoredLang();
  document.documentElement.setAttribute('lang', currentLang);

  // ─────────────────────────────────────────────────────────────────────
  // Lookup with fallback chain: current → ru → key-as-string.
  // ─────────────────────────────────────────────────────────────────────
  function lookup(key, lang) {
    const parts = key.split('.');
    let node = TRANSLATIONS[lang];
    for (const p of parts) {
      if (node && typeof node === 'object' && p in node) {
        node = node[p];
      } else {
        return null;
      }
    }
    return typeof node === 'string' ? node : null;
  }

  function t(key) {
    if (!key) return '';
    return lookup(key, currentLang) || lookup(key, DEFAULT_LANG) || key;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Apply translations to all data-i18n* attributes in DOM subtree.
  // ─────────────────────────────────────────────────────────────────────
  function applyTranslations(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const value = t(key);
      if (value) el.textContent = value;
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      const value = t(key);
      if (value) el.setAttribute('title', value);
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      const value = t(key);
      if (value) el.setAttribute('placeholder', value);
    });
    scope.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
      const key = el.getAttribute('data-i18n-aria-label');
      const value = t(key);
      if (value) el.setAttribute('aria-label', value);
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // Locale-aware time formatters (used by pages directly).
  // ─────────────────────────────────────────────────────────────────────
  function formatTimeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso);
    if (isNaN(then)) return '';
    const diff = (Date.now() - then) / 1000;
    if (diff < 60) return Math.floor(diff) + ' ' + t('time.sec_ago');
    if (diff < 3600) return Math.floor(diff / 60) + ' ' + t('time.min_ago');
    if (diff < 86400) {
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      return m > 0 ? `${h} ${t('time.h_ago').replace(' назад','').replace(' ago','')} ${m} ${t('time.min_ago')}`
                   : `${h} ${t('time.h_ago')}`;
    }
    const d = Math.floor(diff / 86400);
    const h = Math.floor((diff % 86400) / 3600);
    return h > 0 ? `${d} ${t('time.d_ago').replace(' назад','').replace(' ago','')} ${h} ${t('time.h_ago')}`
                 : `${d} ${t('time.d_ago')}`;
  }

  function formatAbsTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const locale = currentLang === 'ru' ? 'ru-RU' : 'en-US';
    return d.toLocaleString(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // Toggle.
  // ─────────────────────────────────────────────────────────────────────
  function setLang(lang) {
    if (!SUPPORTED.includes(lang)) lang = DEFAULT_LANG;
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.setAttribute('lang', lang);
    applyTranslations();
    updateToggleButton();
    // Notify pages that have JS-rendered content to re-render
    window.dispatchEvent(new CustomEvent('krab-lang-changed', { detail: { lang } }));
  }

  function toggleLang() {
    setLang(currentLang === 'ru' ? 'en' : 'ru');
  }

  function updateToggleButton() {
    const btn = document.getElementById('lang-toggle-btn');
    if (!btn) return;
    btn.textContent = t('lang.label');
    btn.title = t('lang.switch_tip');
    btn.setAttribute('aria-label', btn.title);
  }

  // ─────────────────────────────────────────────────────────────────────
  // Inject toggle button into nav (next to theme toggle).
  // ─────────────────────────────────────────────────────────────────────
  function injectToggle() {
    const wrapper = document.querySelector('.nav-bell-wrapper');
    if (!wrapper || document.getElementById('lang-toggle-btn')) {
      updateToggleButton();
      return;
    }
    const btn = document.createElement('button');
    btn.id = 'lang-toggle-btn';
    btn.className = 'lang-toggle theme-toggle'; /* reuse theme-toggle styles */
    btn.type = 'button';
    btn.onclick = toggleLang;
    btn.style.cssText = 'background:transparent; border:none; cursor:pointer; padding:0.5rem; color:inherit; font-size:0.85rem; font-weight:600; margin-right:0.3rem; opacity:0.7; transition:opacity 0.15s;';
    btn.onmouseenter = function () { btn.style.opacity = '1'; };
    btn.onmouseleave = function () { btn.style.opacity = '0.7'; };
    // Insert BEFORE theme button if present, else as first child of wrapper
    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
      wrapper.insertBefore(btn, themeBtn);
    } else {
      wrapper.insertBefore(btn, wrapper.firstChild);
    }
    updateToggleButton();
  }

  // ─────────────────────────────────────────────────────────────────────
  // Bootstrap on DOMContentLoaded.
  // ─────────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    applyTranslations();
    injectToggle();
  });

  // Public API
  window.krabI18n = {
    t: t,
    setLang: setLang,
    toggleLang: toggleLang,
    apply: applyTranslations,
    getLang: function () { return currentLang; },
    formatTimeAgo: formatTimeAgo,
    formatAbsTime: formatAbsTime
  };
  // Convenience alias
  window.t = t;
})();
