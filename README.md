# TradeWind AI

AI-powered swing trading platform — market scanning, paper trading, and strategy backtesting.

The platform pairs a React/Next.js dashboard with a Python/FastAPI backend. An AI-driven
market scanner scores candidate symbols with explainable reasoning, a paper trading engine
simulates fills against live Alpaca market data, and a strategy framework makes it easy to
plug in new trading strategies. Live trading is **not** enabled — everything runs in paper
mode by default.

## Quick Start

### Prerequisites

- Docker and Docker Compose (v2 recommended)
- Alpaca API credentials (for live market data — optional for basic use)

### Setup

1. Clone the repo:

   ```bash
   git clone git@github.com:khartz103-crypto/Tradewinds.git
   cd Tradewinds
   ```

2. Copy `.env.example` to `.env` and fill in your Alpaca API keys (optional for basic use):

   ```bash
   cp .env.example .env
   # edit .env — ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY can stay empty
   # if you only want to explore the UI; live quotes require them.
   ```

3. Build and start all services:

   ```bash
   docker compose up --build
   ```

   The backend container runs database migrations (`alembic upgrade head`) and seeds
   default data (admin user, trend-following strategy, risk settings, paper account)
   before starting the API.

4. Open the dashboard at <http://localhost:3000>.

5. Log in with `admin@tradewind.ai` / `admin` (default seed credentials — change
   `SEED_ADMIN_PASSWORD` in `.env` before any real deployment).

### Services

| Service  | Port | Description              |
|----------|------|--------------------------|
| Frontend | 3000 | Next.js 14 dashboard     |
| Backend  | 8000 | FastAPI REST API         |
| Postgres | 5432 | Database                 |
| Redis    | 6379 | Cache / message broker   |

Interactive API docs are available at <http://localhost:8000/docs> (Swagger UI).

## Architecture

```
┌──────────────────────────┐        ┌──────────────────────────┐
│      Next.js (3000)      │  HTTP  │      FastAPI (8000)      │
│  login / dashboard /     │ ─────► │  /api/auth  (JWT login)  │
│  positions / scanner     │  JSON  │  /api/market  (data)     │
└──────────────────────────┘        │  /api/strategies (scan)  │
                                    │  /api/paper (trading)    │
                                    └───────────┬──────────────┘
                                                │ SQLAlchemy (async)
                                    ┌───────────▼──────────────┐
                                    │      PostgreSQL (5432)   │
                                    └───────────┬──────────────┘
                                                │
                                    ┌───────────▼──────────────┐
                                    │      Redis (6379)        │
                                    └──────────────────────────┘
```

### Backend services (`backend/`)

| Module | Responsibility |
|--------|----------------|
| `app/main.py` | FastAPI app — CORS, JWT error handling, `/api/health`, 4 routers |
| `app/config.py` | Pydantic settings loaded from environment variables |
| `app/auth.py` | JWT token creation (`HS256`), user authentication, `get_current_user` dependency |
| `app/database.py` | Async SQLAlchemy engine + session factory |
| `app/models/` | ORM models: User, Strategy, Position, Trade, Watchlist, MarketDataCache, AIScanResult, RiskSettings, PaperAccount |
| `app/providers/` | `MarketDataProvider` ABC + `AlpacaProvider` (pluggable market data) |
| `app/routers/` | auth, market_data, strategies, paper_trading |
| `app/schemas/` | Pydantic request/response models |
| `app/services/` | market_data (caching), strategy_engine, paper_trading (fills, risk, SL/TP) |
| `app/strategies/` | `BaseStrategy` + indicator library + `trend_following` strategy (registry-based) |
| `migrations/` | Alembic async migrations |
| `tests/` | 25 tests: 16 indicator tests + 9 strategy tests |
| `seed.py` | Idempotent seed: admin user, strategy, risk settings, $100k paper account |

### Frontend pages (`frontend/src/app/`)

| Page | Path | Description |
|------|------|-------------|
| Landing | `/` | Marketing splash — links to login (redirects to dashboard if authenticated) |
| Login | `/login` | Email/password login → stores JWT in localStorage |
| Dashboard | `/dashboard` | Portfolio overview — equity curve, account value, buying power, P&L (TradingView Lightweight Charts) |
| Positions | `/dashboard/positions` | Open + closed positions, close position action |
| Scanner | `/dashboard/scanner` | AI market scanner — pick symbols + strategy, view BUY/SELL signal cards with confidence, price levels, and reasoning |

Frontend API access goes through `frontend/src/lib/api.ts`, which reads the base URL from
`NEXT_PUBLIC_BACKEND_URL` (fallback `http://localhost:8000`), attaches the JWT bearer token,
and redirects to `/login` on `401`. Auth state is managed by `frontend/src/lib/auth-context.tsx`.

## API Endpoints

All endpoints except `/api/health` require `Authorization: Bearer <token>`
(obtain a token via `POST /api/auth/login`).

### Auth (`/api/auth`)

| Method | Path            | Description                          |
|--------|-----------------|--------------------------------------|
| POST   | `/api/auth/login` | Authenticate with email/password → JWT |
| GET    | `/api/auth/me`    | Current user info                     |

### Market data (`/api/market`)

| Method | Path                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| GET    | `/api/market/bars/{symbol}`   | Daily OHLCV bars for a date range         |
| GET    | `/api/market/quotes/{symbol}` | Latest quote (bid/ask/last)              |
| POST   | `/api/market/snapshots`       | Batch snapshots (quote + daily bar + change) |

### Strategies (`/api/strategies`)

| Method | Path                        | Description                              |
|--------|-----------------------------|------------------------------------------|
| GET    | `/api/strategies`           | List enabled strategies                  |
| GET    | `/api/strategies/{name}`    | Strategy details                         |
| POST   | `/api/strategies/{name}/scan` | Run a strategy against a list of symbols → signals |

### Paper trading (`/api/paper`)

| Method | Path                              | Description                                |
|--------|-----------------------------------|--------------------------------------------|
| POST   | `/api/paper/positions`            | Open a paper position (risk checks)        |
| POST   | `/api/paper/positions/{id}/close` | Close a position at market price           |
| GET    | `/api/paper/positions`            | List open positions                        |
| GET    | `/api/paper/positions/closed`     | List closed positions (newest first)       |
| GET    | `/api/paper/portfolio`            | Portfolio summary (equity, P&L, circuit breaker) |
| GET    | `/api/paper/trades`               | Trade history (newest first)               |
| POST   | `/api/paper/update`               | Refresh prices + enforce stop-loss/take-profit |

### Health

| Method | Path           | Description          |
|--------|----------------|----------------------|
| GET    | `/api/health`  | Liveness check → `{"status":"ok"}` |

## Strategy: Trend Following

The seeded strategy (`trend_following`) is a momentum strategy that scans daily bars and
emits a **BUY** when all six conditions are bullish, a **SELL** when all six are bearish,
and no signal otherwise.

The six conditions:

| # | Condition | Rule |
|---|-----------|------|
| 1 | `ema_alignment`     | EMA(20) > EMA(50) |
| 2 | `sma_alignment`     | SMA(20) > SMA(50) |
| 3 | `adx_trending`      | ADX(14) > 25 (trending market) |
| 4 | `macd_momentum`     | MACD line > MACD signal |
| 5 | `rsi_zone`          | 40 < RSI(14) < 70 (not overbought/oversold) |
| 6 | `volume_confirmation` | Volume > 1.5 × SMA of volume(20) |

Signals include entry price (last close), stop-loss / take-profit derived from ATR
(`stop = close ∓ 2.0 × ATR`, `target = close ± 3.0 × ATR`), a weighted confidence score
(each condition contributes ~16.7 points), and human-readable reasoning listing each
condition as PASS/FAIL.

Config is stored per-strategy in the `strategies` table (JSON) and is fully overridable.
Defaults (`backend/app/strategies/trend_following.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `short_window` | `20` | EMA/SMA short period |
| `long_window` | `50` | EMA/SMA long period |
| `adx_threshold` | `25` | Minimum ADX to confirm a trend |
| `adx_period` | `14` | ADX period |
| `volume_factor` | `1.5` | Min volume / average-volume ratio |
| `macd_fast` / `macd_slow` / `macd_signal` | `12` / `26` / `9` | MACD periods |
| `rsi_period` | `14` | RSI period |
| `rsi_low` / `rsi_high` | `40.0` / `70.0` | RSI bounds |
| `atr_period` | `14` | ATR period |
| `atr_stop_mult` / `atr_target_mult` | `2.0` / `3.0` | Stop-loss / take-profit ATR multiples |

New strategies are pluggable: subclass `BaseStrategy`, decorate with `@register_strategy`,
and the engine (`app/services/strategy_engine.py`) will pick it up from the registry.

## Risk Management Defaults

The paper trading engine enforces per-user risk settings (`risk_settings` table,
created with defaults on seed):

| Setting | Default | Description |
|---------|---------|-------------|
| `max_risk_per_trade_pct` | `1.00%` | Max risk per trade (reserved for future sizing) |
| `max_open_positions` | `5` | Max simultaneously open positions |
| `max_daily_loss_pct` | `3.00%` | Daily loss threshold for the circuit breaker |
| `max_portfolio_exposure_pct` | `80.00%` | Max % of portfolio value in open positions |
| `circuit_breaker_enabled` | `true` | Halt new trading when daily loss is hit |
| `circuit_breaker_loss_pct` | `10.00%` | Circuit breaker loss threshold |

Opening a position is rejected (`422`) if it would exceed `max_open_positions` or
`max_portfolio_exposure_pct`. Every fill is recorded as a `Trade` (all `is_paper=true`)
at the latest market mid-price. `POST /api/paper/update` refreshes prices and auto-closes
positions when stop-loss (`≤` for longs, `≥` for shorts) or take-profit triggers. The
paper account starts at **$100,000** (`PaperAccount.initial_balance`).

## Development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Local Postgres + Redis, or point DATABASE_URL/REDIS_URL at your own instances
# (defaults assume the docker-compose service names db/redis)

alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000
```

Run the test suite:

```bash
cd backend
pytest -v                        # 25 tests
# or inside Docker:
docker compose run backend pytest
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

### Docker Compose overrides

For local development tweaks (debug logging, test runner service, extra CORS origins),
copy `docker-compose.override.yml.example` to `docker-compose.override.yml` — Docker
Compose picks it up automatically. See the file itself for details.

## Environment Variables

All configuration flows through environment variables (see `.env.example`). The table
below lists every variable the project reads:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://tradewind:tradewind@db:5432/tradewind` | Async SQLAlchemy Postgres DSN |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed CORS origins |
| `DEBUG` | `false` | Enable debug logging |
| `JWT_SECRET` | `change-me-in-production` | HS256 signing secret — **change in production** |
| `JWT_EXPIRY_HOURS` | `24` | JWT lifetime in hours |
| `SEED_ADMIN_EMAIL` | `admin@tradewind.ai` | Admin user created by the seed |
| `SEED_ADMIN_PASSWORD` | `admin` | Admin password — **change in production** |
| `ALPACA_API_KEY_ID` | *(empty)* | Alpaca API key (paper keys for paper data) |
| `ALPACA_API_SECRET_KEY` | *(empty)* | Alpaca secret key |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | Alpaca trading/account API base |
| `ALPACA_DATA_URL` | `https://data.alpaca.markets` | Alpaca market data API base |
| `MARKET_DATA_CACHE_TTL_MINUTES` | `15` | TTL for cached bars/snapshots |
| `NEXT_PUBLIC_BACKEND_URL` | `http://localhost:8000` | Backend base URL used by the frontend (must be reachable from the browser) |

## Project Status

Milestone 4 (integration & polish) in progress — the MVP scope (two-service app, trend
following strategy, paper trading engine, dashboard + scanner) is functional. Live trading
is deliberately **not** implemented; everything is paper by default and any future live
broker integration must be gated behind explicit configuration.

## License

Proprietary — internal team project.
