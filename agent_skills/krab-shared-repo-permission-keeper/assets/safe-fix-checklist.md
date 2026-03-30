# Safe Fix Checklist

## До исправления

- Известен точный проблемный путь
- Понятно, это shared repo или account-local state
- Снят `git status --short --branch`
- Проверен runtime ownership

## После исправления

- Исходная операция больше не падает
- Другая учётка может читать или писать нужный path
- Не затронуты посторонние каталоги
- Есть короткая truthful note о фиксe
