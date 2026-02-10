План миграции и запуска (Краб v2.0):

1. ОБЗОР ФАЙЛОВ:  
   * docs/PRD\_Krab\_Ultimate.md: Это теперь наша "Библия". Изучи требования.  
   * src/core/model\_manager.py: Это "мозг". Нужно дописать методы API вызовов (я оставил заглушки).  
   * src/core/context\_manager.py: Это "память". Гарантирует, что чаты не смешиваются.  
2. ДЕЙСТВИЯ (В ТЕРМИНАЛЕ):  
   * Создай виртуальное окружение, если нет: python3 \-m venv .venv  
   * Установи зависимости для HEIC и MLX (если Apple Silicon):  
     pip install pyrogram tgcrypto pillow-heif google-generativeai openai aiohttp  
     (Для Whisper MLX смотри отдельную инструкцию Apple MLX).  
3. НАСТРОЙКА ПЕРЕМЕННЫХ (.env):  
   * Добавь:  
     API\_ID=... (от my.telegram.org)  
     API\_HASH=...  
     GEMINI\_API\_KEY=...  
     LM\_STUDIO\_URL="http://localhost:1234/v1"  
4. ИНТЕГРАЦИЯ SCRATCH:  
   * Перенеси файлы из scratch/KrabEar/ в src/modules/hearing/  
   * Адаптируй их под новый класс ModelRouter (чтобы транскрибация шла через локальную модель).  
5. ЗАПУСК:  
   * Начни с создания сессии Pyrogram (login).  
   * Запусти LM Studio в режиме Server.  
   * Запусти бота.

По, система готова к сборке. Жду команды на реализацию конкретного модуля (например, Audio или Vision).