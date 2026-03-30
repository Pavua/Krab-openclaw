# Provider Login Matrix

## OpenAI Codex

- Использовать account-local login flow
- Не копировать auth state между учётками

## Gemini CLI

- Предпочитать штатные `.command` и `sync_gemini_cli_oauth.py`
- Проверять итог через runtime status, а не только по сообщению CLI

## Qwen / другие провайдеры

- Сначала определить, это OAuth, API key или portal session
- Не смешивать provider recovery с model registry edits без нужды

## Telegram

- Использовать `telegram_relogin.command`, когда session invalid
- Не объявлять recovery завершённым, пока session state не подтверждён runtime snapshot
