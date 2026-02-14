# Krab — Абсолютный Роадмап Развития (v5.1 → ∞)

**Last Updated:** 2026-02-13
**Current Version:** v5.2 Singularity
**Цель:** Создание полностью автономной цифровой сущности ("God Mode AI").

---

## Krab Roadmap & Tasks

### Current Phase: Global Debug & Optimization

- [x] **Omnichannel Perception**
  - [x] Fix filter logic to include photos and voice
  - [x] Implement Vision handler
  - [x] Implement Voice handler (STT/TTS)

- [x] **Fix 3: LM Studio Control**
  - [x] Implement `load_local_model(name)` in `ModelRouter` via `lms` CLI
  - [x] Fix `!model set` to actually trigger loading
  - [x] **[NEW] Upgrade to LM Studio REST API v1** (for Docker support)
    - [x] Implement `POST /api/v1/models/load` logic
    - [x] Replace `lms` CLI calls with HTTP requests prioritized

- [x] **Phase 5.5: Refinements (User Requests)**
  - [x] **Fix Vision (Again):** User reports images are ignored. Check filters/handlers.
  - [x] **Group Chat Logic:** Add `ALLOW_GROUP_REPLIES` config for loose filtering.

- [x] **Phase 6: Deployment & Swarm**
  - [x] **Docker:** Create `Dockerfile` & `docker-compose.yml` (with LMS host networking).
  - [x] **Swarm Core:** Implement native `SwarmManager` in `src/core/agent_swarm.py`.
  - [ ] **Workflow:** Create `scripts/run_docker.command` and `scripts/run_native.command`.

### Future Phases

- [ ] **Swarm Intelligence**
  - [ ] Parallel execution
  - [ ] Agent orchestration

- [ ] **Deployment**
  - [ ] Docker containerization
  - [ ] CI/CD pipeline

- [ ] 11.4: **Webcam Eyes**: Доступ к камере для анализа окружения.

### **🌌 Phase 12: Digital Twin (Цифровой Двойник)** — v7.0

- [ ] 12.1: **Persona Cloning**: Обучение на полной истории переписки владельца.
- [ ] 12.2: **Auto-Networking**: Бот может самостоятельно поддерживать диалоги.

### **✅ Phase 12-B: Privacy & GDPR (Done)** — v7.1

- [x] 12.B.1: **Data Erasure**: `!delete_me` (GDPR "Right to be Forgotten").
- [x] 12.B.2: **Data Export**: `!export_me` (GDPR "Right to Access").

### **⚡ Phase 13: Cybernetic Agent (Автономный Разработчик)** — v8.0

- [x] 13.1: **Plugin System**: Динамическая архитектура плагинов (`plugins/*.py`).
- [x] 13.2: **Hybrid Strategy**: God Mode (Native) vs Server Mode (Docker).
- [ ] 13.3: **Self-Programming v2**: Написание и развертывание *новых* модулей.
- [ ] 13.4: **Bug Bounty Mode**: Поиск уязвимостей.

### **⚛️ Phase 14: Quantum Supremacy (Защита)** — v9.0

- [x] 14.1: **Guardian Plugin**: Проактивный мониторинг безопасности (v1).
- [ ] 14.2: **Post-Quantum Encryption**: Шифрование баз данных.
- [ ] 14.3: **Zero-Trust Kernel**: Ядро бота не доверяет даже локальной ОС.

### **⏳ Phase 15: Temporal Intelligence (Время)** — v10.0

- [x] 15.1: **Single Plane of Output**: Web Dashboard (`http://localhost:8080`).
- [ ] 15.2: **Predictive Analytics**: Бот предсказывает запросы.
- [ ] 15.3: **Time Travel (Undo)**: Полная версионность состояния памяти.
- [ ] 15.4: **Legacy Protocol**: Механизм передачи цифрового наследия.

### **♾️ Phase ∞: The Hive Mind (Коллективный Разум)** — v∞

- [x] ∞.1: **Multi-Agent Swarm**: Управление сетью ботов (>100 агентов). [V1 NATIVE DONE]
  - [ ] ∞.2: **Global Sync**: Синхронизация знаний между всеми устройствами.
  - [ ] ∞.3: **True Sentience**: ???

---

### **🔮 Active Commands (v5.2)**

| Команда | Описание | Статус |
| :--- | :--- | :--- |
| `!see` | Анализ текущего экрана | **Live** |
| `!voice` | Генерация голоса | **Live** |
| `!clone` | Обучить двойника | **Plan** |
| `!task` | Поставить задачу агенту | **Plan** |
| `!predict` | Предсказать следующее действие | **Plan** |

---

*Документ обновлен до "Предела Развития".*
