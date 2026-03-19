# Master Plan Source Of Truth

Этот файл фиксирует, по какому именно master-plan считаются проценты готовности проекта,
приоритеты фаз и смысл текущих блоков работ.

## Канонический план

- Основной master-plan:
  [/Users/USER3/PLAN-Краб+переводчик 12.03.2026.md](/Users/USER3/PLAN-Краб+переводчик%2012.03.2026.md)
- Базовый checkpoint перед переходом в Claude:
  [docs/18.03.2026/CHECKPOINT_18032026.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/18.03.2026/CHECKPOINT_18032026.md)

## Дополнительные аналитические входы

Эти документы не заменяют master-plan, а используются как research inputs при обновлении
архитектуры, FinOps-стратегии и routing/policy решений:

- [/Users/Shared/Antigravity_AGENTS/Оптимизация OpenClaw_ Стратегии и Архитектуры.md](/Users/Shared/Antigravity_AGENTS/Оптимизация%20OpenClaw_%20Стратегии%20и%20Архитектуры.md)
- [/Users/USER3/Downloads/deep-research-report.md](/Users/USER3/Downloads/deep-research-report.md)

## Правило расчёта процентов

С этого файла проценты считаются не по локальному handoff-инциденту и не по узкому
техническому списку фиксов, а по фазам из master-plan:

1. `Foundation Hardening`
2. `Channel + Inbox Reliability`
3. `System / Browser Agency`
4. `Multimodal + Voice Foundation`
5. `Realtime Call Translator v1`
6. `Internet Call Translation`
7. `Swarm v2 + Inter-Team Bus`
8. `Trading Lab`
9. `Product Teams`
10. `Controlled Real Autonomy`

## Текущий baseline на 19.03.2026

- Общий проект по master-plan: `31%`
- Текущая фаза `Foundation Hardening`: `68%`
- Текущий инцидентный блок `provider/routing/panel stabilization`: `27%`

## Что считать устаревшим

Если в старых handoff-доках, checkpoint-файлах или устных ответах встречаются проценты
вроде `68%`, `76%` или `77%`, они могли относиться не ко всему master-plan, а к более
узкому operational-срезу:

- truthful runtime;
- owner panel;
- routing и fallback chain;
- userbot timeout/streaming;
- restart/recovery discipline.

Такие проценты теперь считаются вспомогательными и не должны использоваться как главный
project progress.

## Обязательное правило для следующих сессий

При каждом новом диалоге:

- сначала опираться на этот файл;
- затем сверяться с актуальным runtime truth и handoff;
- и только после этого обновлять проценты.

Если master-plan будет пересмотрен, обновлять нужно сначала этот файл, а уже потом
`SESSION_HANDOFF.md` и ответы пользователю.
