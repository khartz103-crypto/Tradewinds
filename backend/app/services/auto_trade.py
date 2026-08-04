"""Auto-trade service — turn scanner signals into paper positions.

This module holds the shared logic used by both the scanner's ``auto_trade``
flag and the scheduled scanner. It enforces the core rules of the
auto-execution pipeline:

* Only trades signals with no error and an action of ``"buy"`` or ``"sell"``.
* Never opens a second position on a symbol that already has one open
  (no doubling up).
* Default position size is 10% of the paper account cash balance unless an
  explicit ``position_size`` (in dollars) is provided.
* Every open goes through :func:`app.services.paper_trading.open_position`,
  which enforces risk limits (max open positions, portfolio exposure,
  buying power).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper_account import PaperAccount
from app.models.position import Position, PositionStatus
from app.services.paper_trading import open_position

logger = logging.getLogger(__name__)

#: Fraction of the paper account cash balance used per trade by default.
DEFAULT_POSITION_PCT = Decimal("0.10")

#: Round fractional quantities to 4 decimals (Numeric(18,8) in the DB).
QTY_PRECISION = Decimal("0.0001")

TRADEABLE_ACTIONS = ("buy", "sell")


def _side_for_action(action: str) -> str:
    """Map a scanner action to a position side string."""
    return "long" if action == "buy" else "short"


async def get_open_symbols(db: AsyncSession, user_id: UUID) -> set[str]:
    """Return the set of symbols that currently have an open position."""
    result = await db.execute(
        select(Position.symbol).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    return {row[0].upper() for row in result.all()}


async def get_cash_balance(db: AsyncSession, user_id: UUID) -> Decimal:
    """Return the user's paper account cash balance (0 if none exists)."""
    result = await db.execute(
        select(PaperAccount).where(PaperAccount.user_id == user_id)
    )
    pa = result.scalar_one_or_none()
    if pa is None:
        return Decimal("0")
    return pa.current_balance


def _compute_quantity(
    entry_price: Decimal, balance: Decimal, position_size: float | Decimal | None
) -> Decimal:
    """Compute the number of shares for a trade.

    Uses ``position_size`` (in dollars) when provided, otherwise the default
    percentage of the cash balance. Never exceeds available cash.
    """
    if position_size is not None:
        dollars = Decimal(str(position_size))
    else:
        dollars = (balance * DEFAULT_POSITION_PCT).quantize(QTY_PRECISION)

    dollars = min(dollars, max(balance, Decimal("0")))

    qty = (dollars / entry_price).quantize(QTY_PRECISION)
    return qty


async def auto_trade_signals(
    db: AsyncSession,
    user_id: UUID,
    signals,
    *,
    strategy_id: UUID | None = None,
    position_size: float | None = None,
    strategy_name: str = "strategy",
) -> list[dict]:
    """Open paper positions for tradeable signals.

    Args:
        db: Active async DB session (commit is left to the caller).
        user_id: The user whose paper account trades.
        signals: Iterable of ``StrategySignal`` dataclasses from a scan.
        strategy_id: Optional strategy UUID to tag positions/trades.
        position_size: Optional fixed dollar amount per trade. When omitted,
            defaults to 10% of the paper account cash balance.
        strategy_name: Human-readable strategy name for position notes.

    Returns:
        A list of result dicts (one per tradeable signal), each with at least
        ``symbol`` and ``error`` (``None`` on success). Successful results also
        carry ``side``, ``quantity``, ``entry_price``, ``stop_loss``,
        ``take_profit``, and ``position_id``.
    """
    results: list[dict] = []
    if not signals:
        return results

    open_symbols = await get_open_symbols(db, user_id)
    balance = await get_cash_balance(db, user_id)

    for signal in signals:
        symbol = signal.symbol.upper()

        # Only trade signals with a clean, directional action.
        if signal.error is not None or signal.action not in TRADEABLE_ACTIONS:
            continue

        # No doubling up — skip symbols that already have an open position.
        if symbol in open_symbols:
            results.append(
                {
                    "symbol": symbol,
                    "side": _side_for_action(signal.action),
                    "error": "Already has an open position",
                }
            )
            continue

        if signal.entry_price is None or signal.entry_price <= 0:
            results.append(
                {
                    "symbol": symbol,
                    "side": _side_for_action(signal.action),
                    "error": "No valid entry price in signal",
                }
            )
            continue

        entry_price = Decimal(str(signal.entry_price))
        qty = _compute_quantity(entry_price, balance, position_size)
        if qty <= 0:
            results.append(
                {
                    "symbol": symbol,
                    "side": _side_for_action(signal.action),
                    "error": "Position size is too small to trade",
                }
            )
            continue

        try:
            position = await open_position(
                db=db,
                user_id=user_id,
                symbol=symbol,
                qty=qty,
                action=signal.action,
                entry_price=entry_price,
                stop_loss=(
                    Decimal(str(signal.stop_loss)) if signal.stop_loss is not None else None
                ),
                take_profit=(
                    Decimal(str(signal.take_profit)) if signal.take_profit is not None else None
                ),
                strategy_id=strategy_id,
                notes=f"Auto-trade from {strategy_name} scan",
            )
            results.append(
                {
                    "symbol": symbol,
                    "side": _side_for_action(signal.action),
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                    "stop_loss": position.stop_loss,
                    "take_profit": position.take_profit,
                    "position_id": str(position.id),
                    "error": None,
                }
            )
            # Prevent doubling up within the same batch.
            open_symbols.add(symbol)
        except (ValueError, RuntimeError) as exc:
            logger.warning("Auto-trade failed for %s: %s", symbol, exc)
            results.append(
                {
                    "symbol": symbol,
                    "side": _side_for_action(signal.action),
                    "error": str(exc),
                }
            )

    return results
