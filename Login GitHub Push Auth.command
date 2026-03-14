#!/bin/bash
# Назначение: one-click настройка GitHub auth для `git push` на этой учётке через GitHub CLI.
# Связь с проектом: нужен, чтобы на временной/новой macOS-учётке можно было продолжать работу
# в этом репозитории без ручной пляски с credential helper после каждого переключения аккаунта.

set -u

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🔐 Настройка GitHub auth для push..."
echo "📂 Репозиторий: $DIR"

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ GitHub CLI (gh) не найден."
  echo "Установи gh и запусти этот файл снова."
  read -p "Нажми Enter для закрытия окна..."
  exit 1
fi

if gh auth status >/dev/null 2>&1; then
  echo "✅ GitHub CLI уже авторизован."
else
  echo "🌐 Запускаю device-flow авторизацию GitHub..."
  echo "Если браузер не откроется автоматически — открой https://github.com/login/device вручную."
  open "https://github.com/login/device" >/dev/null 2>&1 || true
  if ! gh auth login --hostname github.com --git-protocol https --device; then
    echo "❌ Авторизация GitHub не завершилась."
    read -p "Нажми Enter для закрытия окна..."
    exit 1
  fi
fi

if ! gh auth setup-git; then
  echo "❌ Не удалось включить gh как helper для git."
  read -p "Нажми Enter для закрытия окна..."
  exit 1
fi

GH_LOGIN="$(gh api user --jq '.login' 2>/dev/null || true)"
GH_NAME="$(gh api user --jq '.name // .login' 2>/dev/null || true)"
GH_EMAIL="$(gh api user --jq '.email // ""' 2>/dev/null || true)"
GH_ID="$(gh api user --jq '.id' 2>/dev/null || true)"

if [ -n "$GH_NAME" ] && [ -z "$(git config --global --get user.name || true)" ]; then
  git config --global user.name "$GH_NAME"
  echo "✅ Установлен git user.name = $GH_NAME"
fi

if [ -z "$(git config --global --get user.email || true)" ]; then
  if [ -n "$GH_EMAIL" ]; then
    git config --global user.email "$GH_EMAIL"
    echo "✅ Установлен git user.email = $GH_EMAIL"
  elif [ -n "$GH_LOGIN" ] && [ -n "$GH_ID" ]; then
    NOREPLY_EMAIL="${GH_ID}+${GH_LOGIN}@users.noreply.github.com"
    git config --global user.email "$NOREPLY_EMAIL"
    echo "✅ Установлен git user.email = $NOREPLY_EMAIL"
  fi
fi

echo
echo "📌 Итоговый статус:"
gh auth status || true
echo
echo "📌 Git config:"
echo "user.name=$(git config --global --get user.name || true)"
echo "user.email=$(git config --global --get user.email || true)"
echo
echo "✅ Git push auth настроен."
read -p "Нажми Enter для закрытия окна..."
