# Используем легкий официальный образ Python на базе Debian (slim)
FROM python:3.11-slim

# Устанавливаем системные зависимости, необходимые для компиляции и работы curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл зависимостей в первую очередь для кэширования слоев Docker
COPY requirements.txt .

# Устанавливаем зависимости Python без кэширования pip для уменьшения веса образа
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь исходный код проекта в контейнер
COPY . .

# Создаем папку для артефактов и логов, чтобы гарантировать наличие прав на запись
RUN mkdir -p artifacts logs

# Отключаем буферизацию логов для мгновенного отображения в консоли (удобно для отладки)
ENV PYTHONUNBUFFERED=1

# Команда запуска бота через модульный вызов
CMD ["python", "-m", "src.main"]