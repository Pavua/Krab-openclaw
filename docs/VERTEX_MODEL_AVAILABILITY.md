# Vertex AI / Google AI — Доступность моделей Gemini

Wave 60-B-vertex-activation. Проверено 2026-05-10.
Проект: `caramel-anvil-492816-t5`, регион: `us-central1`.

## Итог: причина 404

Gemini 3 (`gemini-3-*`, `gemini-3.1-*`) — статус `PUBLIC_PREVIEW` в Vertex AI.
У них только действие `openGenerationAiStudio`, но **нет** `generateContent` через
Vertex AI `aiplatform.googleapis.com` endpoint. Это не ошибка конфигурации проекта —
модели просто ещё не доступны через Vertex REST API ни в одном регионе.

**Решение**: использовать `generativelanguage.googleapis.com` (Google AI Studio API)
с существующим `GOOGLE_API_KEY` из `.env`.

---

## Матрица доступности

| Модель | Vertex AI (aiplatform) | Google AI Studio (generativelanguage) | Примечание |
|--------|------------------------|---------------------------------------|------------|
| `gemini-2.5-pro` | ✅ OK | ✅ OK | Основная рабочая |
| `gemini-2.5-flash` | ✅ OK | ✅ OK | Рабочая |
| `gemini-2.5-flash-lite` | ✅ OK | ✅ OK | Рабочая |
| `gemini-3-pro-preview` | 404 (PUBLIC_PREVIEW) | ✅ OK | Только через AI Studio |
| `gemini-3-flash-preview` | 404 (PUBLIC_PREVIEW) | ✅ OK | Только через AI Studio |
| `gemini-3.1-pro-preview` | 404 (PUBLIC_PREVIEW) | ✅ OK | Только через AI Studio |
| `gemini-3.1-flash-lite` | 404 (PUBLIC_PREVIEW) | ✅ OK | Только через AI Studio |
| `gemini-3.1-flash-lite-preview` | 404 (PUBLIC_PREVIEW) | ✅ OK | Только через AI Studio |
| `gemini-pro-latest` | 404 | ✅ OK | Alias → текущий Gemini Pro |
| `gemini-flash-latest` | 404 | ✅ OK | Alias → текущий Gemini Flash |
| `gemini-2.5-pro-002` | 404 | 404 | Не существует |
| `gemini-2.5-flash-002` | 404 | 404 | Не существует |
| `gemini-2.0-flash` | 404 | ✅ OK | Через AI Studio |
| `gemini-2.0-flash-lite` | 404 | 404 | Не существует |
| `gemini-1.5-pro` | 404 | 404 | Не существует (deprecated) |
| `gemini-1.5-flash` | 404 | 404 | Не существует (deprecated) |
| `gemini-2.0-flash-001` | 404 | — | GA в SDK list, но 404 через API |
| `gemini-2.0-flash-lite-001` | 404 | — | GA в SDK list, но 404 через API |

### Регионы (тест gemini-3-pro-preview)
Все регионы возвращают 404 через Vertex:
`us-central1`, `us-east4`, `us-east5`, `europe-west1`, `europe-west4`, `asia-southeast1`

---

## Включённые API в проекте

```
agentregistry.googleapis.com   — включён
aiplatform.googleapis.com      — включён
generativelanguage.googleapis.com — включён
cloudaicompanion.googleapis.com   — включён
```

**Нет** `agentplatform.googleapis.com` (Agent Platform — новый брендинг, отдельный API).
Страница Cloud Console для Gemini 3 моделей ведёт в "Agent Platform" раздел,
но фактически для Vertex AI `generateContent` endpoint эти модели недоступны.

---

## Как использовать Gemini 3 сейчас

### Через google.genai SDK (Google AI Studio route)

```python
from google import genai
from google.genai.types import GenerateContentConfig

# НЕ vertexai=True — используем Google AI Studio route с API key
client = genai.Client(api_key="GOOGLE_API_KEY из .env")

resp = client.models.generate_content(
    model='gemini-3-pro-preview',
    contents='Hello',
    config=GenerateContentConfig(max_output_tokens=100)
)
```

### Через curl (для тестирования)

```bash
MODEL="gemini-3-pro-preview"
API_KEY="$(grep GOOGLE_API_KEY .env | cut -d= -f2)"
curl -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"Say OK"}],"role":"user"}],"generationConfig":{"maxOutputTokens":5}}'
```

---

## Рекомендованная fallback-цепочка (Wave 60-B)

OpenClaw использует провайдер `google-vertex` для Vertex AI endpoint
и `google-gemini-cli` для CLI route (который идёт через AI Studio API key).

**Рабочие модели для fallback chain:**

```
Primary:   google/gemini-3-pro-preview      (через OpenClaw — уточнить routing)
Fallback1: google/gemini-3-flash-preview
Fallback2: google/gemini-2.5-pro           (Vertex — стабильно)
Fallback3: google/gemini-2.5-flash         (Vertex — стабильно)
Fallback4: google/gemini-2.5-flash-lite    (Vertex — стабильно)
Fallback5: google/gemini-3.1-pro-preview   (AI Studio route)
```

**Не использовать в chain:**
- `gemini-2.5-pro-002`, `gemini-2.5-flash-002` — не существуют
- `gemini-pro-latest`, `gemini-flash-latest` — 404 в Vertex (только AI Studio)
- `gemini-1.5-pro`, `gemini-1.5-flash` — deprecated, 404 везде

---

## Шаги активации Gemini 3 через Vertex (когда станет GA)

Сейчас (2026-05-10) Gemini 3 Pro Preview и Flash Preview имеют статус `PUBLIC_PREVIEW`
в Vertex AI Model Garden — API endpoint (`generateContent`) через `aiplatform.googleapis.com`
недоступен. Когда Google переведёт эти модели в GA:

1. Статус сменится с `PUBLIC_PREVIEW` на `GA` в Publisher Models API
2. Endpoint `us-central1-aiplatform.googleapis.com/v1/projects/.../models/gemini-3-pro-preview:generateContent`
   начнёт возвращать ответы
3. `google-vertex/gemini-3-pro-preview` в OpenClaw заработает автоматически

**Проверить статус:**
```bash
cd Краб && venv/bin/python3 -c "
import google.auth, google.auth.transport.requests, urllib.request, json
creds, _ = google.auth.default(quota_project_id='caramel-anvil-492816-t5')
creds.refresh(google.auth.transport.requests.Request())
url = 'https://us-central1-aiplatform.googleapis.com/v1beta1/publishers/google/models/gemini-3-pro-preview'
req = urllib.request.Request(url, headers={'Authorization': f'Bearer {creds.token}', 'x-goog-user-project': 'caramel-anvil-492816-t5'})
with urllib.request.urlopen(req) as r:
    d = json.loads(r.read())
    print('Stage:', d.get('launchStage'))
"
```
