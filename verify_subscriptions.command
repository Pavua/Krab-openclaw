#!/bin/bash
# -*- coding: utf-8 -*-

# =================================================================
# Krab AI Bot — Subscription Verification Script
# =================================================================
# Этот скрипт проверяет работоспособность связки Krab + OpenClaw
# при использовании платных подписок (ChatGPT Plus / Gemini Adv).
# =================================================================

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🦀 Запуск верификации подписок...${NC}"

# Переходим в директорию проекта
cd "$(dirname "$0")"

# Проверяем наличие venv
if [ ! -d ".venv" ]; then
    echo -e "${RED}❌ Виртуальное окружение .venv не найдено!${NC}"
    exit 1
fi

# Проверяем, запущен ли OpenClaw Gateway
PORT=18789
if ! lsof -i :$PORT > /dev/null; then
    echo -e "${RED}❌ OpenClaw Gateway не запущен на порту $PORT!${NC}"
    echo "Пожалуйста, запустите 'start_openclaw.command' первым."
    exit 1
fi

echo -e "${GREEN}✅ OpenClaw Gateway найден на порту $PORT${NC}"

# Запуск Python скрипта для теста роутинга
echo "--- Тест ChatGPT Plus через Gateway ---"
.venv/bin/python3 -c "
import asyncio
import os
from src.core.model_manager import ModelRouter

async def test():
    config = os.environ.copy()
    config['OPENCLAW_URL'] = 'http://localhost:18789'
    config['OPENCLAW_TOKEN'] = 'sk-nexus-bridge'
    config['OPENCLAW_MODEL'] = 'openai/gpt-4o' # ChatGPT Plus session
    
    router = ModelRouter(config)
    print('📡 Отправка запроса в ChatGPT Plus...')
    resp = await router.route_query('Привет! Кто ты? Ответь коротко.', task_type='chat', use_rag=False)
    print(f'🤖 Ответ: {resp}')
    
    if resp and '❌' not in resp:
        print('✅ Тест ChatGPT Plus: УСПЕШНО')
    else:
        print('❌ Тест ChatGPT Plus: ОШИБКА')

asyncio.run(test())
"

echo "--- Тест Gemini Advanced через Gateway ---"
.venv/bin/python3 -c "
import asyncio
import os
from src.core.model_manager import ModelRouter

async def test():
    config = os.environ.copy()
    config['OPENCLAW_URL'] = 'http://localhost:18789'
    config['OPENCLAW_TOKEN'] = 'sk-nexus-bridge'
    config['OPENCLAW_MODEL'] = 'google/gemini-2.0-flash' # Gemini session
    
    router = ModelRouter(config)
    print('📡 Отправка запроса в Gemini Advanced...')
    resp = await router.route_query('Привет! Какой сегодня день? Ответь коротко.', task_type='chat', use_rag=False)
    print(f'🤖 Ответ: {resp}')
    
    if resp and '❌' not in resp:
        print('✅ Тест Gemini Advanced: УСПЕШНО')
    else:
        print('❌ Тест Gemini Advanced: ОШИБКА')

asyncio.run(test())
"

echo -e "${GREEN}🏁 Верификация завершена.${NC}"
