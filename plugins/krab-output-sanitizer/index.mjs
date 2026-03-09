/**
 * Плагин OpenClaw для стабильного и правдивого поведения внешних каналов.
 *
 * Зачем нужен:
 * - удаляет служебные артефакты перед outbound и перед записью в transcript;
 * - не даёт внешним каналам обещать browser/cron/voice/shell без подтверждённого runtime;
 * - режет tool-path для внешних каналов до безопасного минимума, чтобы transport не
 *   залипал на `toolUse -> NO_REPLY`.
 *
 * Как связан с проектом:
 * - эта repo-managed копия синхронизируется в `~/.openclaw/extensions/...` скриптом
 *   `scripts/openclaw_runtime_repair.py`;
 * - правки здесь должны быть воспроизводимыми и покрыты тестами repair-скрипта.
 */

const BOX_TOKEN_RE = /<\|(?:begin_of_box|end_of_box|im_start|im_end|image_pad|vision_start|vision_end|endoftext)\|>?/gi;
const TOOL_RESPONSE_TAG_RE = /<\/?tool_response>/gi;
const TOOL_RESPONSE_BLOCK_RE = /<tool_response>[\s\S]*?(?:<\/tool_response>|<\|im_end\|>|$)/gi;
const THINK_BLOCK_RE = /<think>[\s\S]*?<\/think>/gi;
const THINK_TAG_RE = /<\/?think>/gi;
const FINAL_BLOCK_RE = /<final>([\s\S]*?)<\/final>/gi;
const FINAL_TAG_RE = /<\/?final>/gi;
const BARE_TOKEN_LINE_RE = /^\s*<\|.*\|>\s*$/;
const TOOL_PACKET_LINE_RE = /^\s*\{.*\}\s*(?:not found)?\s*$/i;
const ESCAPED_NEWLINES_LINE_RE = /^\s*(?:\\n)+\s*$/;
const REPLY_TO_TOKEN_RE = "(?:reply_to:(?:\\d+|current)|reply_to_(?:\\d+|current))";
const REPLY_TO_PREFIX_RE = new RegExp(`^\\s*\\[+\\s*${REPLY_TO_TOKEN_RE}\\s*\\]+`, "i");
const REPLY_TO_INLINE_PREFIX_RE = new RegExp(`^\\s*(?:\\[{1,2}\\s*${REPLY_TO_TOKEN_RE}\\s*\\]{1,2}\\s*)+`, "i");
const REPLY_TO_ANYWHERE_RE = new RegExp(`\\[{1,2}\\s*${REPLY_TO_TOKEN_RE}\\s*\\]{1,2}`, "gi");

const META_LINE_PATTERNS = [
  /^the user is asking\b/i,
  /^i need to respond\b/i,
  /^i will now call\b/i,
  /^with the path parameter set to\b/i,
  /^calling\b.*\btool\b/i,
  /^no reply needed\.?$/i,
  /^no_reply$/i,
  /^stopiteration:/i,
  /^received request:\s*post\s+to\s+\/v1\/chat\/completions/i,
  /^the model has crashed without additional information/i,
  /^400\s+no models loaded\b/i,
];

const DEFAULT_GUEST_INSTRUCTION = [
  "Режим внешнего (не-owner) контакта:",
  "1) Общайся нейтрально, не называй собеседника владельцем и не используй персональные обращения владельца.",
  "2) Не раскрывай личные данные владельца, отчеты, планы, контакты, историю, секреты, ключи, конфиги, пути и внутреннее состояние системы.",
  "3) Разрешены только безопасные лёгкие задачи: общие ответы, перевод, публичный веб-поиск/резюме.",
  "4) На команды с риском (доступ к устройству, аккаунтам, файлам, автоматизациям, финансам) давай вежливый отказ.",
  "5) Если запрос двусмысленный по приватности, выбирай безопасный отказ без деталей.",
].join("\n");

const DEFAULT_EXTERNAL_INSTRUCTION = [
  "Режим внешнего messaging-канала:",
  "1) Отвечай только по фактически подтверждённым возможностям текущего канала и runtime.",
  "2) Не утверждай, что cron, браузер, shell, голос, файловая система, заметки, почта, умный дом или другие owner-интеграции уже доступны, если это не подтверждено отдельным успешным runtime/tool-вызовом в этом же канале.",
  "3) Если функция доступна только в отдельном Python userbot-контуре, прямо так и говори: 'это доступно в userbot, но не подтверждено для этого внешнего канала'.",
  "4) Для внешних каналов приоритет: текстовый truthful-ответ лучше, чем выдуманный self-check или неподтверждённое обещание.",
  "5) Если не уверен в доступности интернета, браузера или автодействий, явно скажи, что доступ не подтверждён.",
].join("\n");

const DEFAULT_GUEST_ALLOWED_TOOLS = ["web_search", "web_fetch", "weather", "time"];
const DEFAULT_EXTERNAL_ALLOWED_TOOLS = ["web_search", "web_fetch", "weather", "time"];
const DEFAULT_OWNER_ALIASES = ["По", "Павел", "Pavel", "Pablo"];
const EXTERNAL_CHANNELS = new Set(["telegram", "imessage", "whatsapp", "signal", "discord", "slack"]);
const STRUCTURED_TEXT_FIELDS = new Set([
  "text",
  "content",
  "output",
  "result",
  "summary",
  "caption",
  "message",
  "body",
  "title",
  "error",
  "description",
]);

function isObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeChannelId(raw) {
  return String(raw || "").trim().toLowerCase();
}

function normalizePeerId(channel, raw) {
  let value = String(raw || "").trim();
  if (!value) {
    return "";
  }

  const ch = normalizeChannelId(channel);
  if (ch && value.toLowerCase().startsWith(`${ch}:`)) {
    value = value.slice(ch.length + 1).trim();
  }

  value = value.replace(/^direct:/i, "").replace(/^dm:/i, "").trim();

  if (ch === "telegram") {
    value = value.replace(/^(telegram:|tg:)/i, "").replace(/^@/, "").trim();
  } else if (ch === "imessage") {
    value = value.replace(/^(imessage:|sms:)/i, "").trim();
  } else if (ch === "whatsapp") {
    value = value.replace(/^whatsapp:/i, "").trim();
  } else if (ch === "signal") {
    value = value.replace(/^signal:/i, "").trim();
  } else if (ch === "discord") {
    value = value.replace(/^discord:/i, "").trim();
  } else if (ch === "slack") {
    value = value.replace(/^slack:/i, "").trim();
  }

  return value.toLowerCase();
}

function parseDirectIdentityFromSessionKey(sessionKey) {
  const raw = String(sessionKey || "").trim();
  if (!raw) {
    return null;
  }

  const parts = raw.split(":");
  for (let i = 0; i < parts.length - 1; i += 1) {
    const marker = String(parts[i] || "").toLowerCase();
    if (marker !== "direct" && marker !== "dm") {
      continue;
    }
    const channel = normalizeChannelId(parts[i - 1] || "");
    const peerRaw = parts.slice(i + 1).join(":");
    const peer = normalizePeerId(channel, peerRaw);
    if (!channel || !peer) {
      return null;
    }
    return { channel, peer, marker };
  }

  return null;
}

function buildTrustedPeersMap(config, pluginConfig) {
  const map = new Map();

  const upsert = (channel, peerRaw) => {
    const ch = normalizeChannelId(channel);
    const peer = normalizePeerId(ch, peerRaw);
    if (!ch || !peer || peer === "*") {
      return;
    }
    if (!map.has(ch)) {
      map.set(ch, new Set());
    }
    map.get(ch).add(peer);
  };

  const channels = isObject(config?.channels) ? config.channels : {};
  for (const [channel, cfg] of Object.entries(channels)) {
    const allowFrom = toArray(cfg?.allowFrom);
    for (const peer of allowFrom) {
      upsert(channel, peer);
    }
  }

  const trustedPeers = isObject(pluginConfig?.trustedPeers) ? pluginConfig.trustedPeers : {};
  for (const [channel, peers] of Object.entries(trustedPeers)) {
    for (const peer of toArray(peers)) {
      upsert(channel, peer);
    }
  }

  return map;
}

function isTrustedDirectSession(sessionKey, trustedPeersMap) {
  const identity = parseDirectIdentityFromSessionKey(sessionKey);
  if (!identity) {
    return true;
  }
  const trusted = trustedPeersMap.get(identity.channel);
  if (!trusted || trusted.size === 0) {
    return true;
  }
  return trusted.has(identity.peer);
}

function isTrustedRecipient(channelId, to, trustedPeersMap) {
  const channel = normalizeChannelId(channelId);
  const peer = normalizePeerId(channel, to);
  if (!channel || !peer) {
    return true;
  }
  const trusted = trustedPeersMap.get(channel);
  if (!trusted || trusted.size === 0) {
    return true;
  }
  return trusted.has(peer);
}

function isExternalChannelId(channelId) {
  return EXTERNAL_CHANNELS.has(normalizeChannelId(channelId));
}

function isExternalSessionKey(sessionKey) {
  const identity = parseDirectIdentityFromSessionKey(sessionKey);
  if (identity && identity.channel) {
    return isExternalChannelId(identity.channel);
  }
  const raw = String(sessionKey || "").toLowerCase();
  return Array.from(EXTERNAL_CHANNELS).some((channel) => raw.includes(`:${channel}:`) || raw.includes(`${channel}:`));
}

function isExternalContext(sessionKey, channelId) {
  const normalizedChannelId = String(channelId || "").trim();
  if (normalizedChannelId && isExternalChannelId(normalizedChannelId)) {
    return true;
  }
  return isExternalSessionKey(sessionKey);
}

function looksLikeToolPacketLine(line) {
  const normalized = line.toLowerCase();
  const compact = normalized.replace(REPLY_TO_PREFIX_RE, "").trim();

  if (!TOOL_PACKET_LINE_RE.test(compact)) {
    if (compact.includes("heartbeat-state.json") && compact.includes('"action"') && compact.includes('"read"')) {
      return true;
    }
    return false;
  }

  const hasActionKey =
    compact.includes('"action"') ||
    compact.includes('"function_name"') ||
    compact.includes('"functionname"');
  const hasArgsKey =
    compact.includes('"path"') ||
    compact.includes('"offset"') ||
    compact.includes('"limit"') ||
    compact.includes('"arguments"') ||
    compact.includes('"tool_name"');

  if (compact.includes("heartbeat-state.json")) {
    return true;
  }
  if (!hasActionKey && compact.includes('"path"') && (compact.includes("/skills/") || compact.includes("skill.md"))) {
    return true;
  }
  return hasActionKey && hasArgsKey;
}

function isMetaNoiseLine(line) {
  if (BARE_TOKEN_LINE_RE.test(line)) {
    return true;
  }
  if (ESCAPED_NEWLINES_LINE_RE.test(line)) {
    return true;
  }
  if (REPLY_TO_PREFIX_RE.test(line) && !/[a-zа-яё0-9]/i.test(line.replace(REPLY_TO_PREFIX_RE, ""))) {
    return true;
  }
  return META_LINE_PATTERNS.some((pattern) => pattern.test(line));
}

function looksLikeRuntimeFailureText(text) {
  const normalized = String(text || "").toLowerCase();
  if (!normalized) {
    return false;
  }
  return (
    normalized.includes("model has crashed without additional information") ||
    normalized.includes("exit code: null") ||
    normalized.includes("400 no models loaded") ||
    normalized.includes("no models loaded") ||
    normalized.includes("please load a model")
  );
}

function normalizeAssistantEnvelope(raw) {
  return String(raw || "")
    .replace(THINK_BLOCK_RE, "")
    .replace(FINAL_BLOCK_RE, "$1")
    .replace(THINK_TAG_RE, "")
    .replace(FINAL_TAG_RE, "");
}

function normalizeGuardedWhitespace(text) {
  return String(text || "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([,.!?;:])/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function applyExternalTruthGuard(text) {
  /**
   * Внешние каналы не должны фантазировать про runtime self-check.
   *
   * Почему:
   * - userbot уже живёт по truthful-contract;
   * - OpenClaw transport иногда приносит "всё проверил / cron работает / браузер доступен",
   *   хотя в реальности это не подтверждалось в этом канале.
   *
   * Здесь не строим общий NLU-фильтр, а точечно переписываем самые вредные
   * боевые формулировки, которые уже были замечены пользователем.
   */
  let out = String(text || "");

  const replacements = [
    {
      pattern:
        /(?:привет!?\s*)?(?:все|всё) работает,?\s*проверка (?:прошла|связи прошла) успешно\.?\s*чем могу помочь\??/giu,
      value: "Связь в этом канале есть, но полный runtime self-check здесь не подтверждён.",
    },
    {
      pattern:
        /(?:на связи\.?\s*)?(?:все|всё) работает\.?\s*чем могу помочь\??/giu,
      value: "Связь в этом канале есть. Полную runtime-проверку без отдельного self-check не подтверждаю.",
    },
    {
      pattern:
        /(?:отличные новости!?\s*)?мой доступ к (?:твоей|вашей) вкладке chrome[\s\S]{0,160}?я могу использовать браузер\.?/giu,
      value: "Доступ к браузеру в этом канале не подтверждён отдельной runtime-проверкой.",
    },
    {
      pattern:
        /(?:все|всё) в порядке\.?\s*крон работает\.?\s*хардбит настроен\.?\s*я (?:все|всё) проверил,? и (?:все|всё) работает корректно\.?/giu,
      value: "Не могу подтверждать работу cron и heartbeat без отдельной runtime-проверки в этом канале.",
    },
    {
      pattern:
        /(?:я|мы) (?:все|всё) проверил(?:и)?(?:[^.!?\n]*)?(?:все|всё) работает корректно\.?/giu,
      value: "Полная runtime-проверка в этом канале не подтверждена.",
    },
    {
      pattern:
        /у меня есть доступ к интернету(?:[^.!?\n]*)?\.?/giu,
      value: "Доступ к интернету в этом канале не подтверждён.",
    },
    {
      pattern:
        /я могу искать информацию в интернете(?:[^.!?\n]*)?\.?/giu,
      value: "Поиск в интернете в этом канале не подтверждён.",
    },
    {
      pattern:
        /(?:проверка связи прошла успешно\.?\s*)?(?:я сейчас работаю на модели|сейчас я работаю на модели)\s+`?google\/gemini-2\.5-flash`?\.?/giu,
      value: "Маршрут ответа в этом канале нужно подтверждать по фактическому runtime, а не по шаблону.",
    },
  ];

  for (const { pattern, value } of replacements) {
    out = out.replace(pattern, value);
  }

  return normalizeGuardedWhitespace(out);
}

function sanitizeText(raw, options = {}) {
  if (typeof raw !== "string" || raw.length === 0) {
    return { text: raw, changed: false, removedOnlyNoise: false };
  }

  const strippedTokens = normalizeAssistantEnvelope(
    raw
      .replace(TOOL_RESPONSE_BLOCK_RE, "")
      .replace(TOOL_RESPONSE_TAG_RE, "")
      .replace(BOX_TOKEN_RE, "")
      .replace(/\r\n?/g, "\n"),
  );
  const lines = strippedTokens.split("\n");

  const kept = [];
  let removedSomething = false;
  let previousWasToolPacket = false;

  for (const line of lines) {
    const lineWithoutReplyPrefix = line.replace(REPLY_TO_INLINE_PREFIX_RE, "");
    const trimmed = lineWithoutReplyPrefix.trim();

    if (!trimmed) {
      if (kept.length > 0 && kept[kept.length - 1] !== "") {
        kept.push("");
      }
      previousWasToolPacket = false;
      continue;
    }

    const isToolPacket = looksLikeToolPacketLine(trimmed);
    const isTrailingToolNotFound = previousWasToolPacket && /^not found\.?$/i.test(trimmed);

    if (isToolPacket || isTrailingToolNotFound || isMetaNoiseLine(trimmed)) {
      removedSomething = true;
      previousWasToolPacket = isToolPacket;
      continue;
    }

    kept.push(lineWithoutReplyPrefix);
    previousWasToolPacket = false;
  }

  let cleaned = kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  cleaned = normalizeAssistantEnvelope(cleaned).replace(REPLY_TO_INLINE_PREFIX_RE, "").replace(REPLY_TO_ANYWHERE_RE, "").trim();

  if (!cleaned) {
    if (removedSomething && typeof options.emptyFallback === "string") {
      cleaned = options.emptyFallback;
    } else {
      cleaned = normalizeAssistantEnvelope(strippedTokens).replace(REPLY_TO_INLINE_PREFIX_RE, "").replace(REPLY_TO_ANYWHERE_RE, "").trim();
    }
  }

  if (looksLikeRuntimeFailureText(cleaned)) {
    const runtimeFallback =
      typeof options.runtimeFailureFallback === "string" && options.runtimeFailureFallback.trim()
        ? options.runtimeFailureFallback.trim()
        : (typeof options.emptyFallback === "string" && options.emptyFallback.trim() ? options.emptyFallback.trim() : "Секунду, локальная модель перезапускается.");
    cleaned = runtimeFallback;
    removedSomething = true;
  }

  if (options.externalTruthGuard === true) {
    const guarded = applyExternalTruthGuard(cleaned);
    if (guarded !== cleaned) {
      cleaned = guarded;
      removedSomething = true;
    }
  }

  return {
    text: cleaned,
    changed: cleaned !== raw,
    removedOnlyNoise: removedSomething && cleaned.length > 0,
  };
}

function sanitizeTextBlocks(content, options = {}) {
  if (!Array.isArray(content)) {
    return { content, changed: false };
  }

  let changed = false;
  const hiddenBlockTypes = new Set(["thinking", "reasoning", "redacted_thinking", "analysis"]);
  const internalBlockTypes = new Set(["toolcall", "tooluse", "toolresult", "functioncall", "functionresult", "custom"]);
  const next = [];
  let hasVisibleText = false;

  for (const block of content) {
    if (!block || typeof block !== "object") {
      next.push(block);
      continue;
    }
    const blockType = String(block.type || "").trim().toLowerCase();
    if (hiddenBlockTypes.has(blockType) || internalBlockTypes.has(blockType)) {
      changed = true;
      continue;
    }
    if (blockType !== "text" || typeof block.text !== "string") {
      next.push(block);
      continue;
    }

    const sanitized = sanitizeText(block.text, options);
    if (typeof sanitized.text !== "string" || !sanitized.text.trim()) {
      changed = true;
      continue;
    }
    if (sanitized.changed) {
      changed = true;
      next.push({ ...block, type: "text", text: sanitized.text });
      hasVisibleText = true;
      continue;
    }

    next.push(block);
    hasVisibleText = true;
  }

  if (!hasVisibleText && typeof options.emptyFallback === "string" && options.emptyFallback.trim()) {
    return {
      content: [{ type: "text", text: options.emptyFallback.trim() }],
      changed: true,
    };
  }

  return { content: changed ? next : content, changed };
}

function collapseTextBlocksToString(content, options = {}) {
  if (!Array.isArray(content) || content.length === 0) {
    return { content, changed: false };
  }

  const chunks = [];
  for (const block of content) {
    if (!block || typeof block !== "object") {
      return { content, changed: false };
    }
    if (String(block.type || "").trim().toLowerCase() !== "text" || typeof block.text !== "string") {
      return { content, changed: false };
    }
    if (block.text.trim()) {
      chunks.push(block.text.trim());
    }
  }

  if (chunks.length === 0) {
    const fallback = typeof options.emptyFallback === "string" ? options.emptyFallback.trim() : "";
    return { content: fallback || content, changed: Boolean(fallback) };
  }

  const collapsed = sanitizeText(chunks.join("\n\n"), options);
  return {
    content: collapsed.text,
    changed: true,
  };
}

function sanitizeStructuredContent(value, options = {}) {
  /**
   * Жёсткая рекурсивная зачистка нужна для внешних transport-каналов.
   *
   * Почему:
   * - часть ответов приходит не строкой и не массивом text-блоков, а вложенным
   *   объектом с полями `message/content/output/result`;
   * - старый sanitizer пропускал такие payload и служебный `[[reply_to:*]]`
   *   мог дожить до реальной отправки, особенно в iMessage.
   */
  if (typeof value === "string") {
    const sanitized = sanitizeText(value, options);
    return {
      value: sanitized.text,
      changed: sanitized.changed,
    };
  }

  if (Array.isArray(value)) {
    let changed = false;
    const next = value.map((item) => {
      const sanitized = sanitizeStructuredContent(item, options);
      if (sanitized.changed) {
        changed = true;
      }
      return sanitized.value;
    });
    return { value: changed ? next : value, changed };
  }

  if (!value || typeof value !== "object") {
    return { value, changed: false };
  }

  let changed = false;
  const next = Array.isArray(value) ? [] : {};
  for (const [key, nested] of Object.entries(value)) {
    if (typeof nested === "string" && STRUCTURED_TEXT_FIELDS.has(String(key || "").trim().toLowerCase())) {
      const sanitized = sanitizeText(nested, options);
      next[key] = sanitized.text;
      if (sanitized.changed) {
        changed = true;
      }
      continue;
    }

    const sanitized = sanitizeStructuredContent(nested, options);
    next[key] = sanitized.value;
    if (sanitized.changed) {
      changed = true;
    }
  }

  return { value: changed ? next : value, changed };
}

function sanitizeGuestStructuredContent(value, ownerAliases) {
  if (typeof value === "string") {
    const sanitized = sanitizeGuestText(value, ownerAliases);
    return { value: sanitized, changed: sanitized !== value };
  }

  if (Array.isArray(value)) {
    let changed = false;
    const next = value.map((item) => {
      const sanitized = sanitizeGuestStructuredContent(item, ownerAliases);
      if (sanitized.changed) {
        changed = true;
      }
      return sanitized.value;
    });
    return { value: changed ? next : value, changed };
  }

  if (!value || typeof value !== "object") {
    return { value, changed: false };
  }

  let changed = false;
  const next = {};
  for (const [key, nested] of Object.entries(value)) {
    if (typeof nested === "string" && STRUCTURED_TEXT_FIELDS.has(String(key || "").trim().toLowerCase())) {
      const sanitized = sanitizeGuestText(nested, ownerAliases);
      next[key] = sanitized;
      if (sanitized !== nested) {
        changed = true;
      }
      continue;
    }

    const sanitized = sanitizeGuestStructuredContent(nested, ownerAliases);
    next[key] = sanitized.value;
    if (sanitized.changed) {
      changed = true;
    }
  }

  return { value: changed ? next : value, changed };
}

function sanitizeMessageForTranscript(message) {
  if (!message || typeof message !== "object") {
    return message;
  }

  if (message.role === "user") {
    if (typeof message.content === "string") {
      const sanitized = sanitizeText(message.content);
      if (!sanitized.changed) {
        return message;
      }
      return { ...message, content: sanitized.text };
    }

    const sanitizedArray = sanitizeTextBlocks(message.content);
    if (!sanitizedArray.changed) {
      const sanitizedStructured = sanitizeStructuredContent(message.content);
      if (!sanitizedStructured.changed) {
        return message;
      }
      return { ...message, content: sanitizedStructured.value };
    }
    return { ...message, content: sanitizedArray.content };
  }

  if (message.role === "assistant" || message.role === "toolResult") {
    const sanitizedArray = sanitizeTextBlocks(message.content);
    if (!sanitizedArray.changed) {
      const sanitizedStructured = sanitizeStructuredContent(message.content);
      if (!sanitizedStructured.changed) {
        return message;
      }
      return { ...message, content: sanitizedStructured.value };
    }
    return { ...message, content: sanitizedArray.content };
  }

  return message;
}

function stripOwnerAddressing(text, ownerAliases) {
  let out = String(text || "");

  for (const aliasRaw of ownerAliases) {
    const alias = String(aliasRaw || "").trim();
    if (!alias) {
      continue;
    }
    const escapedAlias = escapeRegExp(alias);

    out = out.replace(
      new RegExp(`(привет|здравствуйте|извините|слушай|слушайте|добрый\\s+день|hello|hi)\\s*,?\\s*${escapedAlias}(?=$|[\\s:!,.-])\\s*[:!,.-]*`, "giu"),
      "$1 ",
    );
    out = out.replace(new RegExp(`(^|\\n)\\s*${escapedAlias}(?=$|[\\s:!,.-])\\s*[:!,.-]+\\s*`, "giu"), "$1");
  }

  return out.replace(/[ \t]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

function stripSensitiveGuestPhrases(text) {
  let out = String(text || "");

  const sentencePatterns = [
    /[^.!?\n]*(?:утренн(?:ий|его)[^.!?\n]*отч[её]т)[^.!?\n]*[.!?]?/giu,
    /[^.!?\n]*(?:ежедневн(?:ый|ого)[^.!?\n]*отч[её]т)[^.!?\n]*[.!?]?/giu,
    /[^.!?\n]*(?:daily\s+report)[^.!?\n]*[.!?]?/giu,
    /[^.!?\n]*(?:ваш\s+отч[её]т\s+готов)[^.!?\n]*[.!?]?/giu,
    /[^.!?\n]*(?:твой\s+отч[её]т\s+готов)[^.!?\n]*[.!?]?/giu,
  ];

  for (const pattern of sentencePatterns) {
    out = out.replace(pattern, " ");
  }

  return out.replace(/[ \t]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

function sanitizeGuestText(text, ownerAliases) {
  let out = String(text || "");
  out = stripOwnerAddressing(out, ownerAliases);
  out = stripSensitiveGuestPhrases(out);
  if (!out) {
    out = "Готов помочь с общими вопросами и переводом.";
  }
  return out;
}

function sanitizeGuestBlocks(content, ownerAliases) {
  if (!Array.isArray(content)) {
    return { content, changed: false };
  }

  let changed = false;
  const next = content.map((block) => {
    if (!block || typeof block !== "object") {
      return block;
    }
    if (block.type !== "text" || typeof block.text !== "string") {
      return block;
    }

    const sanitizedText = sanitizeGuestText(block.text, ownerAliases);
    if (sanitizedText === block.text) {
      return block;
    }

    changed = true;
    return { ...block, text: sanitizedText };
  });

  return { content: changed ? next : content, changed };
}

export default function register(api) {
  const pluginConfig = api?.pluginConfig && typeof api.pluginConfig === "object" ? api.pluginConfig : {};
  const enabled = pluginConfig.enabled !== false;
  const fallbackText =
    typeof pluginConfig.fallbackText === "string" && pluginConfig.fallbackText.trim()
      ? pluginConfig.fallbackText.trim()
      : "На связи. Сформулируй запрос ещё раз, если нужен подробный ответ.";
  const runtimeFailureFallback =
    typeof pluginConfig.runtimeFailureFallback === "string" && pluginConfig.runtimeFailureFallback.trim()
      ? pluginConfig.runtimeFailureFallback.trim()
      : "Секунду, локальная модель перезапускается.";
  const guestModeEnabled = pluginConfig.guestModeEnabled !== false;
  const guestInstruction =
    typeof pluginConfig.guestInstruction === "string" && pluginConfig.guestInstruction.trim()
      ? pluginConfig.guestInstruction.trim()
      : DEFAULT_GUEST_INSTRUCTION;
  const externalChannelGuardEnabled = pluginConfig.externalChannelGuardEnabled !== false;
  const externalChannelToolGuardEnabled = pluginConfig.externalChannelToolGuardEnabled !== false;
  const externalInstruction =
    typeof pluginConfig.externalInstruction === "string" && pluginConfig.externalInstruction.trim()
      ? pluginConfig.externalInstruction.trim()
      : DEFAULT_EXTERNAL_INSTRUCTION;

  const guestAllowedTools = new Set(
    (Array.isArray(pluginConfig.guestAllowedTools) && pluginConfig.guestAllowedTools.length > 0
      ? pluginConfig.guestAllowedTools
      : DEFAULT_GUEST_ALLOWED_TOOLS
    )
      .map((item) => String(item || "").trim())
      .filter(Boolean),
  );

  const externalAllowedTools = new Set(
    (Array.isArray(pluginConfig.externalChannelAllowedTools) && pluginConfig.externalChannelAllowedTools.length > 0
      ? pluginConfig.externalChannelAllowedTools
      : DEFAULT_EXTERNAL_ALLOWED_TOOLS
    )
      .map((item) => String(item || "").trim())
      .filter(Boolean),
  );

  const ownerAliases =
    Array.isArray(pluginConfig.ownerAliases) && pluginConfig.ownerAliases.length > 0
      ? pluginConfig.ownerAliases.map((item) => String(item || "").trim()).filter(Boolean)
      : DEFAULT_OWNER_ALIASES;

  const guestToolGuardEnabled = pluginConfig.guestToolGuardEnabled !== false;
  const trustedPeersMap = buildTrustedPeersMap(api?.config || {}, pluginConfig);

  if (!enabled) {
    return;
  }

  api.on("before_prompt_build", (event, ctx) => {
    const sessionKey = String(ctx?.sessionKey || "");
    const channelId = String(ctx?.channelId || "");
    if (!sessionKey && !channelId) {
      return;
    }

    const prepend = [];
    if (externalChannelGuardEnabled && isExternalContext(sessionKey, channelId)) {
      prepend.push(externalInstruction);
    }

    if (guestModeEnabled) {
      const isTrusted = isTrustedDirectSession(sessionKey, trustedPeersMap);
      if (!isTrusted) {
        prepend.push(guestInstruction);
      }
    }

    if (prepend.length === 0) {
      return;
    }

    return { prependContext: prepend.join("\n\n") };
  });

  api.on("before_tool_call", (event, ctx) => {
    const sessionKey = String(ctx?.sessionKey || "");
    const channelId = String(ctx?.channelId || "");
    const toolName = String(ctx?.toolName || event?.toolName || "").trim();
    if ((!sessionKey && !channelId) || !toolName) {
      return;
    }

    if (externalChannelGuardEnabled && externalChannelToolGuardEnabled && isExternalContext(sessionKey, channelId)) {
      if (!externalAllowedTools.has(toolName)) {
        return {
          block: true,
          blockReason: `external_channel_tool_restricted:${toolName}`,
        };
      }
    }

    if (!guestModeEnabled || !guestToolGuardEnabled) {
      return;
    }

    const isTrusted = isTrustedDirectSession(sessionKey, trustedPeersMap);
    if (!isTrusted && !guestAllowedTools.has(toolName)) {
      return {
        block: true,
        blockReason: `guest_tool_restricted:${toolName}`,
      };
    }
  });

  api.on("message_sending", (event, ctx) => {
    const content = event?.content;
    const channelId = String(ctx?.channelId || "").trim();
    const isTrustedRecipientMsg = !guestModeEnabled || isTrustedRecipient(channelId, event?.to, trustedPeersMap);

    if (typeof content === "string") {
      let nextText = content;
      const isExternalMsg = externalChannelGuardEnabled && isExternalContext(String(ctx?.sessionKey || ""), channelId);

      const sanitized = sanitizeText(nextText, {
        emptyFallback: fallbackText,
        runtimeFailureFallback,
        externalTruthGuard: isExternalMsg,
      });
      if (sanitized.changed) {
        nextText = sanitized.text;
      }

      if (!isTrustedRecipientMsg) {
        nextText = sanitizeGuestText(nextText, ownerAliases);
      }

      if (nextText !== content) {
        return { content: nextText };
      }
      return;
    }

    if (Array.isArray(content)) {
      let nextContent = content;
      let changed = false;
      const isExternalMsg = externalChannelGuardEnabled && isExternalContext(String(ctx?.sessionKey || ""), channelId);

      const sanitizedArray = sanitizeTextBlocks(nextContent, {
        emptyFallback: fallbackText,
        runtimeFailureFallback,
        externalTruthGuard: isExternalMsg,
      });
      if (sanitizedArray.changed) {
        nextContent = sanitizedArray.content;
        changed = true;
      }

      if (Array.isArray(nextContent)) {
        const collapsed = collapseTextBlocksToString(nextContent, {
          emptyFallback: fallbackText,
          runtimeFailureFallback,
          externalTruthGuard: isExternalMsg,
        });
        if (collapsed.changed) {
          nextContent = collapsed.content;
          changed = true;
        }
      }

      if (!isTrustedRecipientMsg) {
        if (typeof nextContent === "string") {
          const guestText = sanitizeGuestText(nextContent, ownerAliases);
          if (guestText !== nextContent) {
            nextContent = guestText;
            changed = true;
          }
        } else {
          const guestSanitized = sanitizeGuestBlocks(nextContent, ownerAliases);
          if (guestSanitized.changed) {
            nextContent = guestSanitized.content;
            changed = true;
          }
        }
      }

      if (changed) {
        return { content: nextContent };
      }
    }

    if (content && typeof content === "object") {
      let nextContent = content;
      let changed = false;
      const isExternalMsg = externalChannelGuardEnabled && isExternalContext(String(ctx?.sessionKey || ""), channelId);

      const sanitizedStructured = sanitizeStructuredContent(nextContent, {
        emptyFallback: fallbackText,
        runtimeFailureFallback,
        externalTruthGuard: isExternalMsg,
      });
      if (sanitizedStructured.changed) {
        nextContent = sanitizedStructured.value;
        changed = true;
      }

      if (!isTrustedRecipientMsg) {
        const guestStructured = sanitizeGuestStructuredContent(nextContent, ownerAliases);
        if (guestStructured.changed) {
          nextContent = guestStructured.value;
          changed = true;
        }
      }

      if (changed) {
        return { content: nextContent };
      }
    }
  });

  api.on("before_message_write", (event) => {
    const original = event?.message;
    const next = sanitizeMessageForTranscript(original);
    if (next === original) {
      return;
    }
    return { message: next };
  });
}
