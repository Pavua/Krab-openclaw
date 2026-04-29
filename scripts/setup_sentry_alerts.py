#!/usr/bin/env python3
"""
Настраивает Sentry Alert Rules через API → webhook на локальную панель Krab.

Требует:
- env SENTRY_AUTH_TOKEN — Personal Auth Token (scope: alerts:write, project:write)
- env SENTRY_ORG_SLUG — slug организации (default: из Sentry UI)
- env SENTRY_PROJECTS — comma-separated slugs (default: "krab-main,krab-ear")
- env WEBHOOK_URL — публичный URL webhook endpoint.
                    Для localhost тестов: http://127.0.0.1:8080/api/hooks/sentry
                    Для production: через Cloudflare Tunnel / reverse-proxy.

Создаёт 3 alert rules на каждый project:
1. Any new issue                        — первое появление error.
2. Regression (issue reopens)           — фикс упал.
3. High event rate (>10 events in 5m)   — спайк ошибок.

Все правила шлют POST на WEBHOOK_URL. Secret (SENTRY_WEBHOOK_SECRET)
ОБЯЗАТЕЛЕН — Krab проверяет HMAC подпись и без secret'а отказывает
с 503 (endpoint отключён). Генерируется автоматически при первом
старте web_app и пишется в .env. Передаётся в Internal Integration
через `webhookSecret` при создании.

Запуск:
    python scripts/setup_sentry_alerts.py
    python scripts/setup_sentry_alerts.py --dry-run
    python scripts/setup_sentry_alerts.py --org my-org --project krab-main
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "name": "Krab: any new issue",
        "actionMatch": "all",
        "filterMatch": "all",
        "frequency": 5,  # min
        "conditions": [
            {"id": "sentry.rules.conditions.first_seen_event.FirstSeenEventCondition"},
        ],
        "filters": [],
        "actions": [
            {
                "id": "sentry.integrations.slack.notify_action.SlackNotifyServiceAction",
                "channel": "webhook",
            }
        ],
    },
    {
        "name": "Krab: issue regressed",
        "actionMatch": "all",
        "filterMatch": "all",
        "frequency": 5,
        "conditions": [
            {"id": "sentry.rules.conditions.regression_event.RegressionEventCondition"},
        ],
        "filters": [],
        "actions": [],
    },
    {
        "name": "Krab: high event rate (>10 / 5m)",
        "actionMatch": "all",
        "filterMatch": "all",
        "frequency": 5,
        "conditions": [
            {
                "id": "sentry.rules.conditions.event_frequency.EventFrequencyCondition",
                "value": 10,
                "interval": "5m",
            },
        ],
        "filters": [],
        "actions": [],
    },
]


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    token: str,
    **kwargs: Any,
) -> Any:
    """httpx wrapper с auth header."""
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token}"
    headers["Content-Type"] = "application/json"
    resp = client.request(method, path, headers=headers, **kwargs)
    if resp.status_code >= 400:
        sys.stderr.write(
            f"ERR {method} {path} → {resp.status_code}: {resp.text[:400]}\n"
        )
        resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def ensure_webhook_integration(
    client: httpx.Client,
    token: str,
    org: str,
    webhook_url: str,
    webhook_secret: str | None = None,
) -> str | None:
    """Создаёт Internal Integration в organization с webhook URL.

    Возвращает slug integration'а (для ссылки из rules), или None.
    Если передан `webhook_secret` — прокидывается в Sentry для HMAC-подписи.
    """
    # Поиск существующего
    existing = _request(
        client,
        "GET",
        f"/api/0/organizations/{org}/sentry-app-installations/",
        token,
    )
    for item in existing or []:
        app = item.get("sentryApp") or {}
        if app.get("name") == "Krab Telegram Webhook":
            return app.get("slug")

    # Создание
    body: dict[str, Any] = {
        "name": "Krab Telegram Webhook",
        "organization": org,
        "isAlertable": True,
        "webhookUrl": webhook_url,
        "scopes": ["event:read", "project:read"],
        "schema": {"elements": []},
    }
    if webhook_secret:
        # Sentry хранит secret на своей стороне и подписывает webhook body
        body["webhookSecret"] = webhook_secret
    created = _request(
        client,
        "POST",
        f"/api/0/organizations/{org}/sentry-apps/",
        token,
        content=json.dumps(body),
    )
    return created.get("slug") if created else None


def create_alert_rule(
    client: httpx.Client,
    token: str,
    org: str,
    project: str,
    rule: dict[str, Any],
    integration_slug: str | None,
    webhook_url: str,
) -> None:
    """Создаёт одно alert rule в project."""
    body = dict(rule)
    # Настраиваем action = webhook на нашу панель
    if integration_slug:
        body["actions"] = [
            {
                "id": "sentry.rules.actions.notify_event_sentry_app.NotifyEventSentryAppAction",
                "settings": [],
                "sentryAppInstallationUuid": integration_slug,
            }
        ]
    else:
        # Fallback: generic webhook через service hook (устаревший API, но работает)
        body["actions"] = [
            {
                "id": "sentry.rules.actions.notify_event_service.NotifyEventServiceAction",
                "service": "webhooks",
            }
        ]

    _request(
        client,
        "POST",
        f"/api/0/projects/{org}/{project}/rules/",
        token,
        content=json.dumps(body),
    )
    print(f"  ✓ {project}: created rule «{rule['name']}»")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default=os.getenv("SENTRY_ORG_SLUG"))
    parser.add_argument(
        "--project",
        action="append",
        help="Repeatable. Default: $SENTRY_PROJECTS or krab-main,krab-ear",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.getenv(
            "WEBHOOK_URL", "http://127.0.0.1:8080/api/hooks/sentry"
        ),
    )
    parser.add_argument(
        "--secret",
        default=os.getenv("SENTRY_WEBHOOK_SECRET", "").strip() or None,
        help="HMAC secret (default: $SENTRY_WEBHOOK_SECRET). Required in prod.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.secret:
        sys.stderr.write(
            "WARN: SENTRY_WEBHOOK_SECRET не задан. Krab отклонит webhook с 503. "
            "Запусти web_app хотя бы раз (он сгенерит secret), затем перечитай .env.\n"
        )

    token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if not token:
        sys.stderr.write(
            "ERR: SENTRY_AUTH_TOKEN not set. "
            "Put it in .env: echo 'SENTRY_AUTH_TOKEN=sntryu_XXX' >> .env\n"
        )
        return 2

    org = args.org
    if not org:
        sys.stderr.write(
            "ERR: --org or $SENTRY_ORG_SLUG required (find in Sentry URL: sentry.io/organizations/<slug>/)\n"
        )
        return 2

    projects = args.project or (
        os.getenv("SENTRY_PROJECTS", "krab-main,krab-ear").split(",")
    )
    projects = [p.strip() for p in projects if p.strip()]

    print(f"Sentry setup: org={org}, projects={projects}, webhook={args.webhook_url}")
    if args.dry_run:
        print("(dry-run — skipping API calls)")
        return 0

    with httpx.Client(base_url="https://sentry.io", timeout=30.0) as client:
        # 1. Ensure Internal Integration (чтобы rules могли слать на webhook)
        print("Step 1/2: ensuring Internal Integration…")
        integration_slug = ensure_webhook_integration(
            client, token, org, args.webhook_url, webhook_secret=args.secret
        )
        print(f"  → integration slug: {integration_slug}")

        # 2. Create rules per project
        print(f"Step 2/2: creating rules in {len(projects)} project(s)…")
        for project in projects:
            for rule in DEFAULT_RULES:
                try:
                    create_alert_rule(
                        client, token, org, project, rule, integration_slug,
                        args.webhook_url,
                    )
                except httpx.HTTPStatusError as exc:
                    # 400 могут быть если rule с таким именем уже есть — игнор
                    sys.stderr.write(
                        f"  ! {project}: {rule['name']} — {exc.response.status_code}\n"
                    )

    print("\n✅ Done. Тестовая цепочка:")
    print("  1. В Sentry UI → Project → Issues → [trigger test event]")
    print("  2. Ожидай сообщение в Telegram Saved Messages от userbot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
