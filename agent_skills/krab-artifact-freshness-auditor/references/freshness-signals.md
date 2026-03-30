# Freshness Signals

## Fresh

- Артефакт новее последнего code-change цикла
- Учётка и runtime owner совпадают с текущим вопросом
- Есть `latest` и timestamped версия одного и того же прогона

## Stale but informative

- Артефакт старый, но показывает исторический failure mode
- Артефакт собран на helper-учётке и не выдаётся за final verdict

## Unsafe to trust

- Артефакт конфликтует с новым runtime snapshot
- Артефакт старый, а рядом уже есть более новый прогон того же типа
- Нет понимания, на какой ветке и учётке он собран
