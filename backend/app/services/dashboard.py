"""Live paper-trading performance metrics for the profit dashboard.

Reads closed positions, open positions and the paper account straight from the
database and reuses the battle-tested metrics engine in
``app.services.backtest_metrics.compute_metrics`` so the live dashboard and the
backtester report the exact same numbers (Sharpe, profit factor, drawdown…).

The equity curve is reconstructed from realised P&L: one point per close date
(starting balance + cumulative realised P&L), plus a final mark-to-market point
at the current account value (cash + open position market value). This mirrors
how the backtester builds its curve, so live metrics stay comparable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper_account import PaperAccount
from app.models.position import Position, PositionStatus
from app.services.backtest_metrics import _holding_days, compute_metrics

#: Number of most-recent closed positions included in the response.
RECENT_TRADES_LIMIT = 20

#: Default starting balance when no paper account row exists yet.
DEFAULT_BALANCE = 100_000.0


async def _load_account(db: AsyncSession, user_id: UUID) -> PaperAccount | None:
    """Load the user's paper account (``None`` when it doesn't exist yet)."""
    result = await db.execute(
        select(PaperAccount).where(PaperAccount.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _load_closed_positions(db: AsyncSession, user_id: UUID) -> list[Position]:
    """Load all closed positions, oldest close first."""
    result = await db.execute(
        select(Position)
        .where(
            Position.user_id == user_id,
            Position.status == PositionStatus.CLOSED,
        )
        .order_by(Position.exit_date.asc(), Position.created_at.asc())
    )
    return list(result.scalars().all())


async def _load_open_positions(db: AsyncSession, user_id: UUID) -> list[Position]:
    """Load all open positions."""
    result = await db.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    return list(result.scalars().all())


def _side_str(p: Position) -> str:
    """Normalise the side enum to a plain lowercase string."""
    return p.side.value if hasattr(p.side, "value") else str(p.side)


def _trade_adapter(p: Position) -> SimpleNamespace:
    """Adapt a Position ORM row to the duck-typed shape ``compute_metrics`` wants."""
    return SimpleNamespace(
        symbol=p.symbol,
        side=_side_str(p),
        qty=p.quantity,
        entry_price=float(p.entry_price),
        entry_date=p.entry_date,
        exit_price=float(p.exit_price) if p.exit_price is not None else float(p.current_price),
        exit_date=p.exit_date or p.created_at,
        pnl=float(p.pnl or 0.0),
        r_multiple=None,
    )


def build_equity_curve(
    initial_balance: float,
    closed_positions: list[Position],
    current_equity: float,
    start_date: datetime,
    now: datetime | None = None,
) -> list[SimpleNamespace]:
    """Reconstruct the live equity curve from realised P&L.

    Points are produced per close *day* (P&L aggregated by date, so the chart
    has one point per day instead of one per same-day round trip). The first
    point is the starting balance at the account's creation, the last point is
    the current mark-to-market equity.

    ``now`` is injectable for deterministic tests (defaults to the current
    UTC time). Returns a list of ``SimpleNamespace(date=..., equity=...)``
    objects — the same duck-typed shape ``compute_metrics`` accepts.
    """
    points: list[SimpleNamespace] = [
        SimpleNamespace(date=start_date, equity=float(initial_balance))
    ]
    daily_pnl: dict[str, float] = {}
    for p in closed_positions:
        exit_date = p.exit_date or p.created_at
        day = exit_date.date().isoformat()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + float(p.pnl or 0.0)
    equity = float(initial_balance)
    for day in sorted(daily_pnl):
        equity += daily_pnl[day]
        points.append(
            SimpleNamespace(
                date=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
                equity=equity,
            )
        )
    # Final mark-to-market point (skipped when it falls on the last close day).
    now = now or datetime.now(timezone.utc)
    if now.date() != points[-1].date.date():
        points.append(SimpleNamespace(date=now, equity=float(current_equity)))
    return points


def _recent_trade_item(p: Position) -> dict:
    """One row for the position-history table on the dashboard."""
    return {
        "symbol": p.symbol.upper(),
        "side": _side_str(p),
        "quantity": float(p.quantity),
        "entry_price": float(p.entry_price),
        "exit_price": float(p.exit_price) if p.exit_price is not None else None,
        "entry_date": p.entry_date.isoformat(),
        "exit_date": (p.exit_date or p.created_at).isoformat(),
        "pnl": float(p.pnl or 0.0),
        "holding_days": _holding_days(
            SimpleNamespace(entry_date=p.entry_date, exit_date=p.exit_date or p.created_at)
        ),
    }


def build_dashboard_performance(
    account: PaperAccount | None,
    closed_positions: list[Position],
    open_positions_count: int,
    current_equity: float,
) -> dict:
    """Compute the full dashboard payload from DB rows (pure, no I/O).

    ``current_equity`` is the mark-to-market account value (cash + open
    position market value), computed by the caller.
    """
    initial_balance = float(account.initial_balance) if account else DEFAULT_BALANCE
    start_date = account.created_at if account else datetime.now(timezone.utc)

    curve = build_equity_curve(initial_balance, closed_positions, current_equity, start_date)
    trades = [_trade_adapter(p) for p in closed_positions]
    metrics = compute_metrics(curve, trades, initial_balance)

    per_symbol = [
        {
            "symbol": symbol,
            "trade_count": entry["trade_count"],
            "total_pnl": round(entry["total_pnl"], 2),
            "win_rate_pct": round(entry["win_rate_pct"], 2),
            "avg_r_multiple": round(entry["avg_r_multiple"], 2),
        }
        for symbol, entry in sorted(
            metrics["per_symbol"].items(), key=lambda kv: kv[1]["total_pnl"], reverse=True
        )
    ]
    recent_trades = [
        _recent_trade_item(p) for p in closed_positions[-RECENT_TRADES_LIMIT:]
    ][::-1]

    return {
        "current_equity": round(current_equity, 2),
        "starting_balance": round(initial_balance, 2),
        "total_return_pct": round(
            (current_equity - initial_balance) / initial_balance * 100.0
            if initial_balance > 0
            else 0.0,
            2,
        ),
        "total_pnl": round(current_equity - initial_balance, 2),
        "open_positions": open_positions_count,
        "total_trades_closed": metrics["trade_count"],
        "win_rate_pct": round(metrics["win_rate_pct"], 2),
        "profit_factor": (
            round(metrics["profit_factor"], 2)
            if metrics["profit_factor"] is not None
            else None
        ),
        "sharpe_ratio": round(metrics["sharpe_ratio"], 2),
        "max_drawdown_pct": round(metrics["max_drawdown_pct"], 2),
        "avg_holding_days": round(metrics["avg_holding_days"], 1),
        "per_symbol": per_symbol,
        "equity_curve": [
            {"date": point.date.isoformat(), "equity": round(float(point.equity), 2)}
            for point in curve
        ],
        "recent_trades": recent_trades,
    }


async def get_dashboard_performance(db: AsyncSession, user_id: UUID) -> dict:
    """Load the user's paper-trading rows and compute live performance metrics."""
    account = await _load_account(db, user_id)
    closed_positions = await _load_closed_positions(db, user_id)
    open_positions = await _load_open_positions(db, user_id)

    initial_balance = float(account.initial_balance) if account else DEFAULT_BALANCE
    current_balance = float(account.current_balance) if account else DEFAULT_BALANCE
    open_value = sum(float(p.quantity * p.current_price) for p in open_positions)
    current_equity = current_balance + open_value

    return build_dashboard_performance(
        account,
        closed_positions,
        len(open_positions),
        current_equity,
    )
