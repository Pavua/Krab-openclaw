<!--
Этот документ — анти-413 playbook для безопасного перехода в новый диалог Codex.
Нужен, чтобы не терять контекст при ошибке "413 Payload Too Large" и быстро продолжать работу.
Связан с HANDOVER.md, ROADMAP.md и скриптом prepare_next_chat_context.command.
-->

# Переход в новый диалог без потери контекста (anti-413)

## Что это

Короткий протокол, как переносить рабочий контекст между диалогами, чтобы не упираться в `413 Payload Too Large`.

## Когда использовать

- диалог стал длинным и начались автокомпакты;
- перед большим блоком изменений;
- перед передачей задачи в новый чат/агента.

## Быстрый сценарий (рекомендуется)

1. Запусти:
   - `./prepare_next_chat_context.command`
   - `./build_transition_pack.command` (соберет полный anti-413 пакет в `artifacts/context_transition/`)
2. Скопируй в новый диалог:
   - содержимое сгенерированного `artifacts/context/next_chat_context_*.md`
   - или `TRANSFER_PROMPT_RU.md` из свежего `pack_*`
3. Добавь одну строку с приоритетом:
   - `Продолжаем с приоритетом: Signal link/pairing + стабилизация ответов каналов.`

## Через Web Panel (кнопкой)

Если работа идёт из web-панели, можно собрать anti-413 артефакты без терминала:

1. Открой `http://127.0.0.1:8080`.
2. В блоке **Anti-413 Recovery** нажми:
   - `Create Checkpoint` — создаст свежий `checkpoint_*.md`;
   - `Build Transition Pack` — соберёт `pack_*` с `TRANSFER_PROMPT_RU.md`.
3. Нажми `Refresh Context Links`, чтобы увидеть актуальные пути:
   - `artifacts/context_checkpoints/checkpoint_*.md`
   - `artifacts/context_transition/pack_*/TRANSFER_PROMPT_RU.md`
   - `artifacts/context_transition/pack_*/FILES_TO_ATTACH.txt`
4. В новый диалог передай `TRANSFER_PROMPT_RU.md` и свежий checkpoint.

## Что должно быть в контексте (минимум)

- ветка и commit;
- список изменённых файлов;
- какие тесты уже прошли;
- текущий статус каналов;
- что делать следующим шагом;
- что **не** трогать, чтобы не ломать рабочее.

## Быстрый pre-release smoke (перед handoff/релизом)

1. Запусти `./pre_release_smoke.command`.
2. Проверь итог:
   - `artifacts/ops/pre_release_smoke_latest.json`
3. Если нужно падать и по runtime-диагностике (не только по обязательным gate):
   - `./pre_release_smoke.command --strict-runtime`

## Ограничение размера (практика)

- держи стартовый контекст нового диалога в пределах ~150-300 строк;
- не вставляй длинные логи целиком, только суть + 5-20 ключевых строк;
- вместо полного `git diff` передавай `git diff --stat` и список рисков.

## Текущий фокус (на 2026-02-20)

- Signal: завершить рабочую линковку/регистрацию (учитывая `429 Rate Limited` окна);
- Runtime: удерживать local-first + cloud fallback;
- Ответы каналов: без мусорных служебных блоков и без "простыней";
- Документация: поддерживать HANDOVER/ROADMAP в компактном актуальном виде.
