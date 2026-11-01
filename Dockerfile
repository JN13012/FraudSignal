FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.13.1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home app
USER app

ENV PORT=8080
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn fraudsignal.api:app --host 0.0.0.0 --port $PORT"]
