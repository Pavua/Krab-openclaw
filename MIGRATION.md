# 🦀 Krab Migration Blueprint (v8.0)

Этот документ предназначен для мгновенного введения нового ИИ-агента в контекст проекта.

## 🛠 Project Vitals
- **Цель**: Создание премиального автономного Userbot (MTProto) с гибридным интеллектом.
- **Стек**: Python, Pyrogram, OpenClaw (Gateway), Streamlit (Dashboard), SQLite/ChromaDB.
- **Главные директивы**:
    - **RALPH MODE**: Автономность (пиши -> запускай -> исправляй).
    - **Язык**: Строго РУССКИЙ (комментарии и доки).
    - **macOS Native**: Использование `.command` файлов.
    - **Thin Client**: Краб — это оболочка, OpenClaw — мозг.

## 📊 Current Roadmap Status (from task.md)

- [x] **Phase 1: Foundation & Voice Gateway**
- [x] **Phase 2: Group Moderation v2**
- [x] **Phase 3: Model Routing (Phase D)**
- [x] **Phase 4: Thin Client Pivot (OpenClaw Integration)**
- [x] **Phase 5: Self-Configuration commands**
- [x] **Phase 6: Web Dashboard Integration**
- [x] **Phase 7: Autonomous Project Agent (Loop)**
- [x] **Phase 8: Project Provisioning (Phase E)**
- [x] **Phase 9: Krab Ear IPC Integration**
- [x] **Phase 10: AI Guardian Moderation**
- [x] **Phase 11: Final Document Polish**
- [x] **Phase 12: Project Handover Engine (16.2)**

---

## 🚀 Next Strategic Steps

- [ ] **Phase 13: Swarm & MCP Singularity (Phase 10 Roadmap)**
  - Интеграция MCP Manager для использования внешних инструментов.
  - Swarm Orchestrator для параллельного выполнения задач.
- [ ] **Phase 14: Dockerization & Cloud Deployment**
- [ ] **Phase 15: Monero Wallet UI Integration**

---

## 🚨 Critical Context for New Agent
1. **Model Router**: Используй `src/core/model_manager.py` для выбора моделей. Не вызывай API напрямую без менеджера.
2. **OpenClaw**: Всегда проверяй `http://localhost:8000/health` перед работой с инструментами.
3. **Dashboard**: Запуск через `streamlit run src/utils/dashboard_app.py`.
4. **Verification**: У нас есть `verify_project.command` и `update_docs.command`. Используй их.

## 📂 Key Files
- `HANDOVER.md`: Полная история спринтов.
- `ROADMAP.md`: Стратегическое видение.
- `src/core/agent_loop.py`: Сердце автономности Краба.
- `src/core/handover.py`: Движок автоматических отчетов.

---
*Migration prepared by Antigravity on 13.02.2026*
