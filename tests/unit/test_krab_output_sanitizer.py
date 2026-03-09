"""
Проверки repo-managed плагина `krab-output-sanitizer`.

Зачем нужен этот тест:
- reply-to мусор у нас течёт не в Python userbot, а в JS-плагине OpenClaw;
- поэтому тест запускает реальный `index.mjs` через Node и проверяет те же
  hook-и, которые потом синхронизируются в live runtime.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "plugins" / "krab-output-sanitizer" / "index.mjs"


def _run_plugin_hook(hook_name: str, event: dict, ctx: dict) -> dict | None:
    script = f"""
import register from {json.dumps(PLUGIN_PATH.as_uri())};

const hooks = new Map();
const api = {{
  config: {{}},
  pluginConfig: {{}},
  on(name, handler) {{
    hooks.set(name, handler);
  }},
}};

register(api);
const hook = hooks.get({json.dumps(hook_name)});
if (!hook) {{
  console.log("null");
  process.exit(0);
}}
const result = hook({json.dumps(event)}, {json.dumps(ctx)});
console.log(JSON.stringify(result ?? null));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    raw = completed.stdout.strip()
    return None if not raw or raw == "null" else json.loads(raw)


def test_message_sending_sanitizes_nested_structured_payload() -> None:
    result = _run_plugin_hook(
        "message_sending",
        {
            "to": "pavelr7@me.com",
            "content": {
                "message": "[[reply_to:69787]] На связи.",
                "nested": {
                    "output": "<final>Готово</final>",
                    "caption": "[[reply_to_current]] Проверка",
                },
            },
        },
        {
            "channelId": "imessage",
            "sessionKey": "agent:main:imessage:direct:pavelr7@me.com",
        },
    )

    assert result is not None
    assert result["content"]["message"] == "На связи."
    assert result["content"]["nested"]["output"] == "Готово"
    assert result["content"]["nested"]["caption"] == "Проверка"


def test_before_message_write_sanitizes_structured_transcript_content() -> None:
    result = _run_plugin_hook(
        "before_message_write",
        {
            "message": {
                "role": "assistant",
                "content": {
                    "text": "[[reply_to:12345]] Привет!",
                    "result": "<final>Ок</final>",
                },
            }
        },
        {},
    )

    assert result is not None
    assert result["message"]["content"]["text"] == "Привет!"
    assert result["message"]["content"]["result"] == "Ок"


def test_message_sending_external_guard_rewrites_false_browser_claim() -> None:
    result = _run_plugin_hook(
        "message_sending",
        {
            "to": "@example_user",
            "content": "Отличные новости! Мой доступ к твоей вкладке Chrome теперь работает корректно. Я могу использовать браузер.",
        },
        {
            "channelId": "telegram",
            "sessionKey": "agent:main:telegram:direct:@example_user",
        },
    )

    assert result is not None
    assert result["content"] == "Доступ к браузеру в этом канале не подтверждён отдельной runtime-проверкой."


def test_message_sending_external_guard_rewrites_false_cron_claim() -> None:
    result = _run_plugin_hook(
        "message_sending",
        {
            "to": "+34603834299",
            "content": "Все в порядке. Крон работает. Хардбит настроен. Я все проверил, и все работает корректно.",
        },
        {
            "channelId": "whatsapp",
            "sessionKey": "agent:main:whatsapp:direct:+34603834299",
        },
    )

    assert result is not None
    assert result["content"] == "Не могу подтверждать работу cron и heartbeat без отдельной runtime-проверки в этом канале."


def test_message_sending_external_guard_rewrites_false_success_claim() -> None:
    result = _run_plugin_hook(
        "message_sending",
        {
            "to": "@example_user",
            "content": "Привет! Всё работает, проверка прошла успешно. Чем могу помочь?",
        },
        {
            "channelId": "telegram",
            "sessionKey": "agent:main:telegram:direct:@example_user",
        },
    )

    assert result is not None
    assert result["content"] == "Связь в этом канале есть, но полный runtime self-check здесь не подтверждён."


def test_message_sending_external_guard_strips_reply_tag_inside_text() -> None:
    result = _run_plugin_hook(
        "message_sending",
        {
            "to": "pavelr7@me.com",
            "content": "На связи. [[reply_to:69787]] Что-то нужно проверить?",
        },
        {
            "channelId": "imessage",
            "sessionKey": "agent:main:imessage:direct:pavelr7@me.com",
        },
    )

    assert result is not None
    assert "[[reply_to:" not in result["content"]
    assert result["content"] == "На связи. Что-то нужно проверить?"
