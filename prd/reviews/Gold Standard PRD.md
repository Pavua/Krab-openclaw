# **Product Requirements Document: Krab v2.0 (Ultimate Userbot)**

**Status:** Draft **Version:** 2.3 **Owner:** @p0lrd **Agent:** Ralph (Senior Architect)

## **Executive Summary**

Проект "Krab v2.0" — это автономный Userbot для Telegram, работающий через Pyrogram (MTProto). Он решает проблему изоляции локальных нейросетей, предоставляя гибридный доступ к AI (Google Gemini \+ Local LM Studio) прямо из мессенджера. Система обладает мультимодальностью (слух, зрение) и управляется владельцем через личные сообщения, обеспечивая полную приватность данных.

## **Overview**

**Problem:** Стандартные боты ограничены API, а локальные LLM на MacBook изолированы от реального общения. Текущие скрипты в папке scratch/ не систематизированы. **Goal:** Создать единую систему "Краб", которая использует 36GB RAM MacBook для запуска локальных моделей, но при этом доступна через Telegram 24/7. **Target User:** Power User (@p0lrd), требующий автоматизации рутины, транскрибации голосовых и анализа документов.

## **Technical Stack**

* **Core Framework:** Pyrogram (MTProto Client), Python 3.10+  
* **AI Orchestration:**  
  * **Local:** openai (client for LM Studio), mlx-whisper (Apple Silicon optimized ASR).  
  * **Cloud:** google-generativeai (Gemini 2.0 Flash/Pro).  
* **Database:** JSONL (Flat file storage) for Context History.  
* **Utils:** pillow-heif (Image conversion), ffmpeg.  
* **Infrastructure:** .command scripts for macOS execution.

## **Features & Requirements**

### **Phase 1: Foundation (Core Architecture)**

#### **1\. Hybrid Model Router (MUST)**

Умная маршрутизация запросов между локальным сервером и облаком.

* **User Story:** Я хочу, чтобы простые вопросы обрабатывались локально (бесплатно и приватно), а сложные — через Gemini.  
* **Acceptance Criteria:**  
  * \[ \] Система проверяет доступность <http://localhost:1234/v1> (LM Studio).  
  * \[ \] Если локальный сервер недоступен, запрос автоматически уходит в Gemini Flash.  
  * \[ \] Логирование выбора модели в консоль.  
* **Depends on:** LM Studio Server  
* **Files:** src/core/model\_manager.py

#### **2\. Context Isolation (MUST)**

Разделение памяти между чатами.

* **User Story:** Я хочу, чтобы бот помнил контекст разговора в чате "Работа", но не переносил его в чат "Семья".  
* **Acceptance Criteria:**  
  * \[ \] Создание папок artifacts/memory/{chat\_id}/.  
  * \[ \] Сохранение истории в history.jsonl.  
  * \[ \] При перезагрузке бота контекст не теряется.  
* **Files:** src/core/context\_manager.py

#### **3\. Orchestrator Commands (MUST)**

Управление ботом через ЛС владельца.

* **User Story:** Я пишу \!status в Избранное и получаю отчет о здоровье системы.  
* **Acceptance Criteria:**  
  * \[ \] Бот игнорирует команды от посторонних.  
  * \[ \] Команда \!status показывает использование RAM и активную модель.  
* **Files:** src/main.py

### **Phase 2: Perception (Eyes & Ears)**

#### **4\. KrabEar (Hearing) (SHOULD)**

Транскрибация голосовых сообщений.

* **User Story:** Мне присылают голосовое, бот присылает текст в ответ.  
* **Acceptance Criteria:**  
  * \[ \] Поддержка формата .ogg.  
  * \[ \] Использование mlx-whisper для скорости на M-чипах.  
  * \[ \] Обработка аудио длиной до 5 минут \< 10 секунд.  
* **Depends on:** mlx-whisper  
* **Files:** src/modules/perceptor.py

#### **5\. Vision Engine (SHOULD)**

Понимание изображений и документов.

* **User Story:** Я отправляю фото документа (даже HEIC), бот присылает самари.  
* **Acceptance Criteria:**  
  * \[ \] Авто-конвертация .heic \-\> .jpg.  
  * \[ \] Отправка в Gemini Vision или локальную LLaVA.  
* **Files:** src/modules/perceptor.py

### **Phase 3: Advanced (Nice-to-Have)**

#### **6\. Smart Auto-Reply (COULD)**

* **User Story:** Бот может сам отвечать в группах, если его упомянули, подстраиваясь под стиль.  
* **Acceptance Criteria:**  
  * \[ \] Анализ последних 10 сообщений стиля чата перед ответом.

## **Success Metrics**

* **Performance:** Cold start time \< 3 seconds.  
* **Response Time:** Text generation (Local) \< 2s TTFT (Time To First Token).  
* **Privacy:** 100% of messages marked \#private are processed locally.  
* **Stability:** 99.9% uptime during active session.

## **Constraints**

* **Hardware:** MacBook Pro M3 Max (36GB RAM).  
* **OS:** macOS Sequoia.  
* **Network:** Telegram API Access required.
