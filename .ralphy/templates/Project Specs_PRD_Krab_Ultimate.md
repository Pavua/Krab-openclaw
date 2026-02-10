# **Feature: Krab v2.0 (Ultimate Userbot)**

Интеллектуальный автономный Userbot с гибридным AI-ядром (Cloud \+ Local), мультимодальным восприятием и контекстной памятью.

## **1\. Overview (Обзор)**

**Цель:** Создать персонального цифрового ассистента, работающего от имени аккаунта @yung\_nagato, который управляется владельцем @p0lrd.  
**Проблема:** Обычные боты ограничены API и не имеют доступа к личным чатам. Локальные модели изолированы от мессенджеров. Старые скрипты (scratch/) разрознены.  
**Решение:** Единая система на базе Pyrogram (Userbot), интегрированная с Google Gemini (для сложных задач) и локальным сервером LM Studio/Flux (для приватности, экономии и скорости), умеющая "слышать" через KrabEar и "видеть" любые файлы.

## **2\. Requirements (Требования)**

### **2.1 Core Features (MUST)**

* **Userbot Engine:** Работа через Pyrogram (MTProto). Авторизация через сессию, не Bot API.  
* **Hybrid Intelligence (Model Router):**  
  * Система должна автоматически выбирать модель:  
    * *Сложная логика/Критика:* Google Gemini Pro.  
    * *Быстрые ответы/Код:* Gemini Flash или Local LLM (через LM Studio/Ollama).  
    * *Приватные данные:* Строго Local LLM.  
* **Context Isolation:**  
  * Бот должен понимать, в какой группе находится.  
  * Память (History/RAG) должна быть изолирована: контекст из чата "Dev" не должен попадать в чат "Family".  
* **KrabEar Integration:**  
  * Транскрибация голосовых сообщений (Whisper Local/Groq).  
  * Вставка текста в поле ввода или автоматический ответ.  
* **Admin Control:**  
  * Прием команд только от @p0lrd.  
  * Управление через ЛС (Orchestrator Mode).

### **2.2 Important Features (SHOULD)**

* **Multimodality:**  
  * Распознавание фото (включая .HEIC с iPhone без ручной конвертации).  
  * Генерация картинок: Flux (Local via ComfyUI) или Imagen (Cloud).  
* **Resource Management:**  
  * Мониторинг RAM (MacBook 36GB). Выгрузка тяжелых моделей, если они не нужны.  
  * Приоритет MLX формата для Apple Silicon.

### **2.3 Nice-to-Have (COULD)**

* **Video Analysis:** Понимание контекста коротких видео (рилсов/кружков).  
* **Auto-Reply Modes:** Настройка "персоны" для разных чатов (в одном — токсичный, в другом — официальный).

## **3\. User Stories**

### **Story 1: Оркестрация через ЛС**

*Как @p0lrd, я хочу написать в ЛС @yung\_nagato задачу "Проанализируй последние 50 сообщений в чате X и дай самари", чтобы получить отчет, не спамя в общий чат.*

### **Story 2: Голосовой ввод (KrabEar)**

*Как пользователь, я хочу надиктовать сообщение, чтобы Краб мгновенно перевел его в текст и вставил в поле ввода (или отправил), используя локальный Whisper для скорости.*

### **Story 3: Работа с изображениями**

*Я скидываю .HEIC фото документа. Краб должен молча конвертировать его в памяти, распознать текст (OCR) и выдать мне суть документа.*

## **4\. Technical Notes (Architecture & Stack)**

* **Core:** Python 3.10+, Pyrogram.  
* **AI Backend:**  
  * google-generativeai (Gemini SDK).  
  * openai (для совместимости с LM Studio local server).  
* **Audio:** whisper-mlx или faster-whisper (оптимизация под Apple Silicon).  
* **Image:** pillow-heif (для HEIC), ComfyUI API (для генерации).  
* **Database:**  
  * Vector DB: ChromaDB (локально) или Firestore (облако) — *выбрать локально для скорости.*  
  * Structure: db/{chat\_id}/history.json.

## **5\. Migration from scratch/**

* **KrabEar:** Перенести логику audio\_transcriber.py в модуль src/modules/hearing.py.  
* **Nexus:** Использовать наработки по RAG из Nexus для модуля памяти src/memory/vector\_store.py.  
* **OpenClaw:** Взять туловый функционал (поиск, браузер) и адаптировать под команды юзербота.