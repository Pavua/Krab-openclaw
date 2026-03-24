# Call Translator Audit RU

## Что уже есть в коде

- owner-facing endpoints для readiness, control-plane, session inspector, mobile readiness и delivery matrix;
- iPhone companion onboarding / trial-prep / bind / remove flows через web backend;
- translator runtime profile и session state;
- prepare/generate scripts для iPhone companion skeleton/Xcode project;
- Voice Gateway и Krab Ear интеграционные клиенты.

## Что ещё не считается честно закрытым

- `Voice Gateway control-plane` ещё нужно дожать до полного production truth на живом backend, а не только на fake-клиентах;
- ordinary-call acceptance пока не закрыт личным daily-use gate;
- internet-call adapters ещё не являются live delivery surface;
- voice-first профиль intentionally не считается ready-for-daily-use.

## Ближайший продуктовый путь

1. ordinary-call companion;
2. subtitles-first onboarding;
3. `ru-es` и `es-ru` как первый ежедневный сценарий;
4. repeated-call stability;
5. потом internet-call prep.

## Что считать blocker-ами

- gateway unavailable;
- mobile registry не настроен;
- device не bound к active session;
- restart ломает session truth;
- fallback рвёт звонок или теряет timeline.
