# Risk Guard

Risk-guard — обязательный шлюз перед исполнением ордера. Стратегия передаёт
`OrderIntent`, модуль возвращает `RiskDecision`: `allow`, `reduce`, `block` или
`kill_switch`.

## Что закрывает

- запрет новых long, если BTC ниже `$80.1k`;
- дневной kill-switch при просадке портфеля ниже `-2.5%`;
- автоматическое снижение плеча SOL при внутридневной амплитуде выше `3%`;
- динамический max position size по equity, stop distance, волатильности и bucket exposure;
- append-only журнал причин в JSONL и SQLite.

## Запуск демо

```bash
python -m src.trading.risk_guard --demo
```

На macOS можно запустить двойным кликом:

```bash
scripts/run_risk_guard_demo.command
```

## Интеграция

Executor должен принимать только результат `RiskGuard.validate()`:

```python
decision = await risk_guard.validate(intent)
if decision.blocked:
    return OrderResult(blocked=True, reason=decision.reasons)

return await exchange.place_order(decision)
```

Это намеренно не advisory-модуль. Он должен быть последней обязательной проверкой
перед exchange adapter.
