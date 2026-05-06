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
