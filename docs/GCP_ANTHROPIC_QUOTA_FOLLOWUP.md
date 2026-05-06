# GCP Anthropic Claude Quota — Follow-up Checklist

Создано: 2026-05-07 (Session 39)

## Контекст

Открыты 2 Google Cloud support cases по поводу Anthropic Claude model
quotas на Vertex AI:

1. **Case 1 — Cy** (Quota Support Team): запрашивает Sales POC для активации
   квот, без него отказ через 3-5 дней
2. **Case 2 — Elle** (Quota Team): просит сначала connect with Sales, потом
   filed for new quota request specifically для
   `AnthropicMaasConcurrentBatchPredictionJobs`

## Шаги

- [ ] **1. Submit contact form** на https://cloud.google.com/contact/form
      (07.05.2026 — done через computer-use, заполнено: Павел Ребец /
      AI Solutions Engineer / pavelr7@rongfa.biz / +34 603 83 42 99 /
      Rongfa / AI - Generative / Spain / описание про Anthropic quota)
- [ ] **2. Получить email от Google Sales POC** (1-3 дня обычно)
- [ ] **3. Reply ОБОИМ Cy и Elle** с указанием:
      - Имя POC'а
      - Email POC'а
      - Желательно дата first contact
- [ ] **4. Filed new quota request** для `AnthropicMaasConcurrentBatchPredictionJobs`
      в Cloud Console → IAM & Admin → Quotas → Edit quotas → подать new request
      (по совету Elle, текущий case у неё про другую квоту)
- [ ] **5. Ждать одобрения** quota увеличения

## Risks

- **Cases auto-close через 3-5 дней без response** — критично уложиться в timing
- **Personal use case** — Google рекомендовал Find a Partner program; если Sales
  POC не ответит за 3-5 дней, может потребоваться партнёрский путь

## Контакты

- Cy (Google Cloud Platform Team) — case 1
- Elle (Google Cloud Support Quota Team) — case 2

## Заполненный текст в contact form (для reference)

> Following up on two open Google Cloud support cases (re: Anthropic Claude
> model quota increases on Vertex AI, including
> AnthropicMaasConcurrentBatchPredictionJobs). The Quota Support team asked me
> to obtain a Sales POC for our project. We are integrating Anthropic Claude
> (Sonnet, Opus) into internal AI assistant tooling for technical workflows
> and require higher concurrent prediction job quotas. Please connect me with
> a Sales contact who can help approve the Anthropic Claude model quotas.
> Thank you.

---

## 🔍 Cloud Console Quota Survey (07.05.2026)

**Project**: `caramel-anvil-492816-t5` (display name: Claude)

### Текущее состояние

- **40 pending batch prediction job requests** (Increase Requests tab) —
  именно это Elle называла "wrong quota". Эти запросы **не дадут** RPM
  для real-time Claude через Krab. Их можно либо игнорировать, либо
  запросить Sales POC отозвать.
- **0 RPM allocated** для online prediction — по умолчанию у нового project'а.

### Правильная quota для Krab use-case

**Service**: Agent Platform API (`aiplatform.googleapis.com`)

**Quota Name**: `Regional online prediction requests per base model per minute per region per base_model`

**Dimensions**:
- `region` — 5 доступно: `us-east5`, `us-central1`, `europe-west4`,
  `europe-west1`, `asia-southeast1`
- `base_model` — все Claude variants (3.5/3.7/4.5/4.6/4.7 Haiku/Sonnet/Opus)

### Стратегия после Sales POC connect

**Регионы для request'а**:
- ✅ `us-east5` — primary (default для Anthropic on Vertex, всегда поддерживает свежие модели)
- ✅ `europe-west4` — secondary (Spain proximity → меньше latency для Krab,
  fallback если us-east5 недоступен)
- ❌ asia-southeast1, europe-west1, us-central1 — пропускаем (не нужны)

**Model + RPM targets** (per region):

| Model family | Recommended RPM |
|---|---|
| Haiku 3, 3.5, 4.5 | **30** RPM каждая |
| Sonnet 3.5, 3.7, 4.5, 4.6 | **20** RPM каждая |
| Opus 4.5, 4.6, 4.7 | **10** RPM каждая |

Логика: Haiku — cheap & fast, можно запросить больше; Opus — premium,
короткие, scarce. Numbers выбраны как "modest with headroom" — Sales
POC одобрит без push-back. Через 2-4 недели actual usage можно re-file
с 2x bumps (60-100 RPM).

**Total**: ~10 моделей × 2 региона = **~20 requests** в одном пакете.

### Action items когда Sales POC появится

1. [ ] Reply Cy + Elle с POC именем (NOT файлим quota пока)
2. [ ] Спросить у POC: можно ли отозвать batch prediction request чтобы не
      путать с online RPM запросом? (Elle намекнула что они wrong)
3. [ ] Filed bulk online prediction requests согласно таблице выше
4. [ ] Через 2-4 недели → re-file с 2x bump (если usage показал need)
5. [ ] Также посмотреть quotas от других partners на Vertex (Mistral,
      Llama, Codestral) — но это отдельный round, после Anthropic
      одобрения

### Key URLs

- Quotas page: https://console.cloud.google.com/iam-admin/quotas?project=caramel-anvil-492816-t5
- Increase Requests: https://console.cloud.google.com/iam-admin/quotas/qirs?project=caramel-anvil-492816-t5
