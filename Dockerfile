FROM python:3.11-slim

# Установить ffmpeg и зависимости
RUN apt-get update \
    && apt-get install -y ffmpeg libavcodec-extra \
    && rm -rf /var/lib/apt/lists/*

# Рабочий каталог
WORKDIR /app

# Копировать requirements.txt и установить зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копировать все файлы проекта
COPY . .

# Запуск бота
CMD ["python3", "start.py"]
