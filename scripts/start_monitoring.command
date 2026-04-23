#!/bin/bash
# Запуск локального monitoring stack: Prometheus + Grafana + Alertmanager
# Двойной клик из Finder или из терминала.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/deploy/monitoring"

echo "=== Krab Monitoring Stack ==="
echo "Compose dir: $COMPOSE_DIR"

# Проверка Docker
if ! docker info &>/dev/null; then
    echo "ERROR: Docker не запущен. Запустите Docker Desktop."
    open -a "Docker"
    echo "Подождите запуска Docker и запустите этот скрипт снова."
    exit 1
fi

cd "$COMPOSE_DIR"

echo "Запуск stack..."
docker compose up -d

echo ""
echo "Stack запущен:"
echo "  Prometheus  -> http://localhost:9090"
echo "  Grafana     -> http://localhost:3000  (admin / krab_local)"
echo "  Alertmanager-> http://localhost:9093"
echo ""
echo "Для остановки: docker compose -f deploy/monitoring/docker-compose.yml down"
