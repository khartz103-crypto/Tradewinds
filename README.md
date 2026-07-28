# TradeWind AI

AI-powered swing trading platform — monorepo with FastAPI backend and Next.js frontend.

## Quick Start

```bash
# 1. Clone and start all services
docker compose up --build

# 2. Verify backend health
curl http://localhost:8000/api/health
# → {"status":"ok"}

# 3. Open the dashboard
open http://localhost:3000
```

## Services

| Service  | Port | Description              |
| -------- | ---- | ------------------------ |
| frontend | 3000 | Next.js 14 dashboard     |
| backend  | 8000 | FastAPI REST API         |
| db       | 5432 | PostgreSQL 15            |
| redis    | 6379 | Redis 7                  |

## Development

- Backend auto-reloads on code changes (uvicorn `--reload`)
- Frontend auto-reloads on code changes (Next.js dev server)
- All secrets via environment variables — see `.env.example`
