FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HOME=/home/user \
    FREEAI_DATA_DIR=/app/data

WORKDIR /app

RUN useradd -m -u 1000 user

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY backend ./backend
COPY frontend ./frontend

RUN mkdir -p /app/data /home/user && \
    chown -R 1000:1000 /app /home/user && \
    chmod -R 777 /app/data /home/user

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
