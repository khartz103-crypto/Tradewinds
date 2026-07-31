"""TradeWind AI — FastAPI backend."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ensure 401 responses include a JSON body with ``detail``."""
    return JSONResponse(
        status_code=401,
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
