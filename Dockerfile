FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY review/ review/
COPY scripts/ scripts/

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "scripts/health_server.py"]