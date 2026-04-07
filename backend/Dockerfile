FROM debian:trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-fastapi \
    python3-uvicorn \
    python3-httpx \
    python3-pydantic \
    python3-dotenv \
    python3-pymysql \
    python3-docker \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY . .
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
