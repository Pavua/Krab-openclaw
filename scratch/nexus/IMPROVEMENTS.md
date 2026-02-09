# Nexus Project: Improvements & Future Roadmap

This document outlines 30+ potential improvements, features, and optimizations for the Nexus Multi-Agent System.

## 🚀 Performance & Scalability (System)
1.  **Async/Queue System**: Implement `Celery` or `Redis` queues to handle high volumes of analysis requests without blocking the bot.
2.  **Rate Limiting**: Add strict rate limiting on the Manager agent to prevent Telegram API bans.
3.  **Local LLM Support**: Integrate **Ollama** or **LM Studio** (via `openai` compatible local server) to save tokens and run privately on your M4 Max.
4.  **Caching Layer**: Use `Redis` to cache search results (Scout) and analysis (Analyst) for identically requested tokens (e.g., "ETH" cache valid for 5 mins).
5.  **Database Integration**: Replace transient memory with **PostgreSQL** or **MongoDB** to store user history, alerts, and past reports.
6.  **Dockerization**: Create a `Dockerfile` and `docker-compose.yml` to containerize the agents, Redis, and DB for easy deployment.
7.  **PM2 Process Management**: Use `PM2` instead of raw python scripts for auto-restart on crash and log management.
8.  **Parallel Scraping**: Enhance Scout to use `asyncio.gather` to scrape multiple news sources simultaneously.
9.  **Load Balancing**: If the user base grows, split Agents into separate microservices (Scout Service, Analyst Service).
10. **Token Usage Tracking**: Implement middleware to track and log Gemini API token usage per request/user.

## 🧠 Intelligence & Analysis (Analyst)
11. **Technical Analysis (TA)**: Integrate `pandas_ta` or `ta-lib` to calculate RSI, MACD, and Moving Averages from price data (not just news).
12. **Sentiment Scoring**: Use a dedicated NLP library (like `VADER` or fine-tuned BERT) specifically for extracting numeric sentiment scores from headlines before passing to Gemini.
13. **Multi-Model Consensus**: Query multiple models (Gemini, GPT-4, Claude) and have a "Judge" agent synthesize the final verdict.
14. **Prompt Engineering**: Refine Analyst prompts with "Chain of Thought" (CoT) to force the model to explain its reasoning step-by-step.
15. **Chart Vision**: Allow users to upload a screenshot of a chart, and use Gemini Vision to analyze the technical pattern.
16. **Historical Context**: Feed the Analyst the last 24h of "market mood" (stored in DB) to detect trend shifts (e.g., "Sentiment flipping from Fear to Greed").
17. **Whale Alert Integration**: Connect to Whale Alert API to track large transactions and warn of potential dumps.

## 🕵️ Data Gathering (Scout)
18. **Twitter/X API**: Get a developer key or use a robust scraper (like `twint` fork) to monitor specific influencers/tickers in real-time.
19. **RSS Feeds**: Subscribe to RSS feeds of major crypto news sites (CoinDesk, Cointelegraph) for lower latency than scraping.
20. **On-Chain Data**: Integrate **Etherscan** or **Solscan** APIs to check contract safety/liquidity for new tokens.
21. **Auto-Discovery**: Background task that autonomously scans "New Pairs" on DEX Screener and alerts the Manager if a high-potential token is found.
22. **Anti-Detect Browsing**: Integrate `playwright` with stealth plugins for Scout to bypass Cloudflare on strict sites.

## 🤖 User Experience & Interface (Manager)
23. **Interactive Menus**: Use Telegram `InlineKeyboardMarkup` (buttons) for actions like "Refresh", "Deep Dive", "Show Chart".
24. **Daily Briefing**: Auto-schedule a morning report (cron job) sent to the user with an overview of the market.
25. **Custom Alerts**: Allow users to set price/sentiment alerts (e.g., "/alert ETH sentiment > bullish").
26. **Voice Notes**: Allow the bot to send voice summaries (using TTS) for hands-free updates.
27. **Admin Dashboard**: Create a simple Streamlit or React dashboard to view system health, active agents, and logs.
28. **Multi-User Support**: Add an authorization middleware (whitelist of Telegram User IDs) so only approved users can access the bot.
29. **PDF Reports**: Generate nice PDF reports for "Deep Dives" instead of just text messages.

## 🛡️ Security & Reliability
30. **Secret Management**: Use a secure vault (or at least encrypted env vars) for API keys.
31. **Input Sanitization**: Ensure all user inputs are sanitized to prevent prompt injection attacks against the Analyst.
32. **Circuit Breakers**: If Gemini API fails X times, auto-switch to a backup model or local model logic.
33. **Unit Tests**: Write `pytest` tests for each agent to ensure parsing and logic doesn't break on API changes.
34. **Logging & Monitoring**: Integrate **Sentry** for real-time error tracking and alerting.

## 🛠️ Immediate "Must-Haves" for M4 Max
- **Local RAG (Retrieval Augmented Generation)**: Index crypto whitepapers locally using `ChromaDB` and use the M4 Max to run an embedding model. This allows the Analyst to answer specific questions about a project's tech stack purely from documents.
- **Ollama Integration**: Install Ollama (`brew install ollama`) and run `llama3` or `mistral`. Update `agents.yaml` to allow switching model provider from `gemini` to `local`.
