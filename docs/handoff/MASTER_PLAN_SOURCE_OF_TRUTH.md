# Master Plan Source Of Truth

Этот файл фиксирует, по какому именно master-plan считаются проценты готовности проекта,
приоритеты фаз и смысл текущих блоков работ.

## Канонический план

- Основной master-plan:
  [docs/MASTER_PLAN_VNEXT_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md)
- Исторический стратегический источник:
  [docs/PLAN-Краб+переводчик 12.03.2026.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/PLAN-Краб+переводчик%2012.03.2026.md)
- Базовый checkpoint предыдущего цикла:
  [docs/18.03.2026/CHECKPOINT_18032026.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/18.03.2026/CHECKPOINT_18032026.md)

## Дополнительные аналитические входы

Эти документы не заменяют master-plan, а используются как research inputs при обновлении
архитектуры, FinOps-стратегии и routing/policy решений:

- [/Users/Shared/Antigravity_AGENTS/Оптимизация OpenClaw_ Стратегии и Архитектуры.md](/Users/Shared/Antigravity_AGENTS/Оптимизация%20OpenClaw_%20Стратегии%20и%20Архитектуры.md)
- [/Users/USER3/Downloads/deep-research-report.md](/Users/USER3/Downloads/deep-research-report.md)

## Правило расчёта процентов

С этого файла проценты считаются не по локальному handoff-инциденту и не по узкому
техническому списку фиксов, а по каноническим execution-фазам:

1. `Truth Reset`
2. `OpenClaw Stability Kernel`
3. `Channel Reliability / Proactive Core`
4. `System / Browser / Capability Expansion`
5. `Multimodal + Voice Foundation`
6. `Ordinary Call Translator MVP`
7. `Translator Daily-Use Hardening`
8. `Monetization Layer`
9. `Product Teams / Swarm / Controlled Autonomy`

## Текущий baseline на 20.03.2026

- Общий проект по master-plan: `31%`
- Текущий приоритетный блок `OpenClaw Stability Kernel`: `в активной реализации`
- Translator-блок считается вторым по приоритету после stability kernel

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
- затем читать `docs/MASTER_PLAN_VNEXT_RU.md`;
- затем сверяться с актуальным runtime truth и handoff;
- и только после этого обновлять проценты.

Если master-plan будет пересмотрен, обновлять нужно сначала этот файл, а уже потом
`SESSION_HANDOFF.md` и ответы пользователю.
