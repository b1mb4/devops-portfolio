# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /build

# Встановлюємо системні залежності для компіляції
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Копіюємо requirements.txt
COPY app/requirements.txt .

# Встановлюємо Python залежності
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Створюємо непривілейованого користувача
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Копіюємо встановлені пакети з builder stage
COPY --from=builder /root/.local /home/appuser/.local

# Копіюємо код застосунку
COPY --chown=appuser:appuser app/ .

# Додаємо .local/bin до PATH
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Перемикаємося на непривілейованого користувача
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Відкриваємо порт
EXPOSE 5000

# Запускаємо застосунок через gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]