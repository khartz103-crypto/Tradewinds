"""TradeWind AI — FastAPI backend."""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import InvalidTokenError
from app.config import settings
from app.routers import auth, backtest, dashboard, market_data, paper_trading, strategies

logger = logging.getLogger(__name__)


async def _apply_migrations_and_seed() -> None:
    """Apply pending Alembic migrations and seed defaults on boot.

    Mirrors the Dockerfile start command (``alembic upgrade head && python -m
    app.seed``) so every deploy self-heals: the schema is up to date and any
    backfills (e.g. the ``regime_filter`` column from PR #34) are applied
    before the app serves requests — no manual ``alembic upgrade head`` step,
    regardless of how the process is launched (Dockerfile CMD vs. a platform
    start-command override such as Render's dashboard).

    Failures are logged, never fatal: the app may still work without the
    latest column, and a database that is down at boot must not prevent the
    API from starting.
    """
    # Alembic reads DATABASE_URL from the app settings (same env as the app),
    # but needs alembic.ini on disk — run from the backend directory, resolved
    # relative to this file so it works from any launch cwd (/app in Docker).
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            cwd=backend_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("Alembic upgrade timed out after 120s; continuing without it")
            return
        text = output.decode(errors="replace").strip()
        if proc.returncode == 0:
            logger.info("Alembic migrations applied: %s", text or "no pending migrations")
        else:
            logger.error(
                "Alembic upgrade failed (rc=%s); continuing without it: %s",
                proc.returncode,
                text,
            )
    except Exception:
        logger.exception("Alembic upgrade could not run; continuing without it")

    # Seed is idempotent (checks for existing rows) and backfills the
    # regime_filter on pre-existing strategy rows.
    try:
        from app.seed import seed

        await seed()
        logger.info("Seed data ensured (admin user, strategies, risk settings)")
    except Exception:
        logger.exception("Seed could not run; continuing without it")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the auto-trade scheduler loop on boot, stop it on shutdown.
    The scheduler loop itself is always running in the background; whether it
    actually scans depends on its enabled flag in Redis (so the on/off state
    survives restarts).
    """
    await _apply_migrations_and_seed()
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
