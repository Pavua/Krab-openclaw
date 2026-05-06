# 🔔 PENDING: GCP Anthropic Quota — ждём Sales POC email

**Status**: contact form submitted 06.05.2026, ожидаем ответ 1-3 дня от
Google Cloud Sales (likely from `cloudsupport@google.com`).

## Когда придёт письмо от Google Cloud Sales POC

Пользователь пришлёт скриншот / forward письма. Тогда **выполнить в одном
session**:

### Шаг 1: Reply Cy + Elle

Найти оригинальные support cases в Gmail (Subject: "Google Cloud Support
70886393" или похожее), reply BOTH с одинаковым текстом:

```
Hello [Cy/Elle],

Following up on this case — Google Cloud Sales reached out and
assigned us [POC NAME] (<[POC EMAIL]>) as our point of contact.

Project: caramel-anvil-492816-t5
Sales POC: [name + email из письма]

Could you please proceed with the Anthropic Claude model quota review
now that the Sales POC is in place? We are filing the proper online
prediction quota requests separately as you advised.

Thank you,
Pavel R
```

### Шаг 2: Файлить ~20 quota requests

URL: https://console.cloud.google.com/iam-admin/quotas?project=caramel-anvil-492816-t5

Quota name: **`Regional online prediction requests per base model per minute per region per base_model`**
Service: **Agent Platform API**

#### Targets (per region)

| Model | RPM | Justification (если попросят) |
|---|---|---|
| anthropic-claude-3-haiku | 150 | High-volume cheap model для batch tasks |
| anthropic-claude-3-5-haiku | 150 | High-volume cheap model |
| anthropic-claude-haiku-4-5 | 150 | High-volume cheap model |
| anthropic-claude-3-5-sonnet | 75 | Mid-tier для quality replies |
| anthropic-claude-3-7-sonnet | 75 | Mid-tier |
| anthropic-claude-sonnet-4-5 | 75 | Mid-tier |
| anthropic-claude-sonnet-4-6 | 75 | Mid-tier |
| anthropic-claude-opus-4-5 | 40 | Premium для complex reasoning |
| anthropic-claude-opus-4-6 | 40 | Premium |
| anthropic-claude-opus-4-7 | 40 | Premium |

#### Regions (по 2 на каждую модель = ~20 total requests)

- **us-east5** (primary, default Anthropic Vertex region)
- **europe-west4** (secondary, Spain proximity для лучшего latency)

#### Justification text (пишем во всех request descriptions)

> Internal team productivity automation tool — workflow assistant for
> summarization, translation, content drafting in private team setting.
> Not a consumer-facing service. Sales POC: [POC name from email].
> Modest enterprise-tier RPM, expected steady usage.

### Шаг 3: Других партнёров (отдельным раундом, после Anthropic approve)

| Provider | Model | RPM target | Region |
|---|---|---|---|
| Mistral | mistral-large-latest | 60 | us-central1 |
| Mistral | codestral-latest | 60 | us-central1 |
| Meta | llama-4-maverick | 60 | us-east5 |
| Meta | llama-4-scout | 60 | us-east5 |
| AI21 | jamba-large | 30 | us-central1 |

## Project context

- **Project ID**: `caramel-anvil-492816-t5`
- **Display name**: Claude
- **Owner**: pavelr7@gmail.com
- **Business email**: pavelr7@rongfa.biz
- **Country**: Spain
- **Industry**: AI - Generative
- **Sales contact form**: SUBMITTED 06.05.2026 (Thank you confirmed)

## Что НЕ делать

- ❌ Файлить quota requests до получения Sales POC (Elle прямо сказала
  "Once you have successfully connected with the Google Sales Team")
- ❌ Просить >150 Haiku / >100 Sonnet / >50 Opus — triggers enterprise
  review с questions about MAU / business scale
- ❌ Файлить single mixed request на всё подряд — Mistral/Llama после
  Anthropic round

## Gmail monitor

Setup: `scripts/gcp_quota_poc_watcher.py` + LaunchAgent (см.
`scripts/launchagents/ai.krab.gcp-quota-poc-watcher.plist`).

User должен установить Gmail App Password один раз:
1. https://myaccount.google.com/apppasswords
2. .env: `EMAIL_USER=pavelr7@gmail.com`, `EMAIL_APP_PASSWORD=xxx`
3. Load LaunchAgent: `launchctl load ~/Library/LaunchAgents/ai.krab.gcp-quota-poc-watcher.plist`

При совпадении → Telegram alert от Krab с превью письма.
