"""TradeWind AI — FastAPI backend."""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import InvalidTokenError
from app.config import settings
from app.routers import auth, market_data, paper_trading, strategies

app = FastAPI(title=settings.app_name)

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
