FROM python:3.12-slim

WORKDIR /app

# asyncpg is pure-Python — no system build deps needed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Shell-form CMD so $PORT (injected by Render) is expanded and && chains work.
# The --reload flag is dropped in production — Render handles restarts.
CMD alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
