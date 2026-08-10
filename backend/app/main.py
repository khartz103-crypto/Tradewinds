"""TradeWind AI — FastAPI backend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import InvalidTokenError
from app.config import settings
from app.routers import auth, backtest, dashboard, market_data, paper_trading, strategies


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the auto-trade scheduler loop on boot, stop it on shutdown.

    The scheduler loop itself is always running in the background; whether it
    actually scans depends on its enabled flag in Redis (so the on/off state
    survives restarts).
    """
    from app.services.scheduler import start_loop, stop_loop

    start_loop()
    try:
        yield
    finally:
        await stop_loop()
        from app.services.redis_client import close_redis

        await close_redis()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS — allow the Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── JWT error handling ────────────────────────────────────────────────


@app.exception_handler(InvalidTokenError)
async def invalid_token_handler(request: Request, exc: InvalidTokenError) -> JSONResponse:
    """Ensure invalid-token responses include a JSON body with ``detail``.

    Only handles token failures (``InvalidTokenError``), so login failures
    keep their own ``detail`` message instead of being overwritten.
    """
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid or expired token"},
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── health ─────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health_check():
    """Basic health-check endpoint."""
    return {"status": "ok"}


# ── routers ────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(market_data.router)
app.include_router(strategies.router)
app.include_router(paper_trading.router)
app.include_router(backtest.router)
app.include_router(dashboard.router)
