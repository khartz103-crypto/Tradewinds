"""Paper trading engine — simulates order execution, manages positions, enforces risk limits.

All functions accept an async SQLAlchemy session and use Decimal for money values.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.models.paper_account import PaperAccount
from app.models.position import Position, PositionSide, PositionStatus
from app.models.risk_settings import RiskSettings
from app.models.trade import OrderType, Trade, TradeStatus
from app.services.market_data import get_latest_quote

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _pnl_long(entry: Decimal, exit: Decimal, qty: Decimal) -> Decimal:
    return (exit - entry) * qty


def _pnl_short(entry: Decimal, exit: Decimal, qty: Decimal) -> Decimal:
    return (entry - exit) * qty


async def _get_risk_settings(db: AsyncSession, user_id: UUID) -> RiskSettings:
    """Return the user's risk settings, creating defaults if missing."""
    result = await db.execute(
        select(RiskSettings).where(RiskSettings.user_id == user_id)
    )
    rs = result.scalar_one_or_none()
    if rs is None:
        rs = RiskSettings(user_id=user_id)
        db.add(rs)
        await db.flush()
    return rs


async def _get_paper_account(db: AsyncSession, user_id: UUID) -> PaperAccount:
    """Return the user's paper account, creating one with $100k default if missing."""
    result = await db.execute(
        select(PaperAccount).where(PaperAccount.user_id == user_id)
    )
    pa = result.scalar_one_or_none()
    if pa is None:
        pa = PaperAccount(user_id=user_id)
        db.add(pa)
        await db.flush()
    return pa


def _mid_price(quote) -> Decimal | None:
    """Best-effort price from a quote: last > bid/ask midpoint."""
    if quote.last is not None:
        return quote.last
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / Decimal("2")
    if quote.bid is not None:
        return quote.bid
    if quote.ask is not None:
        return quote.ask
    return None


# ── public API ─────────────────────────────────────────────────────────────


async def open_position(
    db: AsyncSession,
    user_id: UUID,
    symbol: str,
    qty: Decimal,
    action: str = "buy",
    entry_price: Decimal | None = None,
    stop_loss: Decimal | None = None,
    take_profit: Decimal | None = None,
    strategy_id: UUID | None = None,
    notes: str | None = None,
) -> Position:
    """Open a new paper-trading position with risk checks.

    Args:
        db: Active DB session.
        user_id: The user opening the position.
        symbol: Ticker symbol, e.g. ``"AAPL"``.
        qty: Number of shares/contracts.
        action: ``"buy"``/``"long"`` opens a long; ``"sell"``/``"short"`` opens a short.
        entry_price: Execution price. If ``None``, the latest market quote is fetched.
        stop_loss: Price at which to auto-close for a loss.
        take_profit: Price at which to auto-close for a gain.
        strategy_id: Optional strategy that generated this trade.
        notes: Free-text notes.

    Returns:
        The newly created ``Position`` (already flushed to the DB).

    Raises:
        ValueError: If a risk limit would be violated or the quantity is not positive.
        RuntimeError: If the quote fetch fails.
    """
    if qty <= 0:
        raise ValueError("Quantity must be positive")

    # 1. Determine execution price — caller-provided entry price wins; otherwise
    #    fall back to the latest market quote.
    if entry_price is None:
        try:
            quote = await get_latest_quote(db, symbol)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch quote for {symbol}: {exc}"
            ) from exc

        price = _mid_price(quote)
        if price is None:
            raise RuntimeError(f"No usable price for {symbol} — quote is empty")
        entry_price = price

    if entry_price <= 0:
        raise ValueError("Entry price must be positive")

    # 2. Map action → position side ("buy"/"long" → long, "sell"/"short" → short)
    side_str = "long" if action.lower() in ("buy", "long") else "short"

    # 3. Load risk settings
    risk = await _get_risk_settings(db, user_id)

    # 3. Count current open positions
    open_count_result = await db.execute(
        select(func.count()).select_from(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    open_count: int = open_count_result.scalar_one()

    if open_count >= risk.max_open_positions:
        raise ValueError(
            f"Cannot open position: at max open positions ({risk.max_open_positions})"
        )

    # 4. Calculate position value and total exposure
    position_value = qty * entry_price

    open_positions_result = await db.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    existing_open: list[Position] = list(open_positions_result.scalars().all())
    total_existing_value = sum(
        (p.quantity * p.current_price) for p in existing_open
    )
    new_total_exposure = total_existing_value + position_value

    # Get paper account for portfolio size
    paper_account = await _get_paper_account(db, user_id)
    portfolio_value = paper_account.current_balance + total_existing_value

    if portfolio_value > Decimal("0"):
        exposure_pct = (new_total_exposure / portfolio_value) * Decimal("100")
        if exposure_pct > risk.max_portfolio_exposure_pct:
            raise ValueError(
                f"Cannot open position: exposure {exposure_pct:.2f}% exceeds "
                f"limit {risk.max_portfolio_exposure_pct}%"
            )

    # 5. Create Position record
    position_side = PositionSide.LONG if side_str == "long" else PositionSide.SHORT
    position = Position(
        user_id=user_id,
        symbol=symbol.upper(),
        side=position_side,
        quantity=qty,
        entry_price=entry_price,
        current_price=entry_price,
        status=PositionStatus.OPEN,
        strategy_id=strategy_id,
        notes=notes,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    db.add(position)
    await db.flush()

    # 6. Create opening Trade record
    trade = Trade(
        user_id=user_id,
        position_id=position.id,
        symbol=symbol.upper(),
        side=side_str,
        quantity=qty,
        price=entry_price,
        order_type=OrderType.MARKET,
        status=TradeStatus.FILLED,
        filled_at=datetime.now(timezone.utc),
        strategy_id=strategy_id,
        is_paper=True,
    )
    db.add(trade)
    await db.flush()

    logger.info(
        "Opened %s %s @ %s qty=%s (position=%s)",
        side_str.upper(),
        symbol,
        entry_price,
        qty,
        position.id,
    )
    return position


async def close_position_at_market(
    db: AsyncSession,
    user_id: UUID,
    position_id: UUID,
    current_price: Decimal,
) -> Position:
    """Close a paper position at an already-fetched market price."""
    result = await db.execute(select(Position).where(
        Position.id == position_id, Position.user_id == user_id
    ))
    position = result.scalar_one_or_none()
    if position is None:
        raise ValueError(f"Position {position_id} not found")
    if position.status == PositionStatus.CLOSED:
        raise ValueError(f"Position {position_id} is already closed")
    if current_price <= 0:
        raise ValueError("Exit price must be positive")
    pnl = (_pnl_long(position.entry_price, current_price, position.quantity)
           if position.side == PositionSide.LONG
           else _pnl_short(position.entry_price, current_price, position.quantity))
    now = datetime.now(timezone.utc)
    position.status = PositionStatus.CLOSED
    position.exit_price = current_price
    position.exit_date = now
    position.current_price = current_price
    position.pnl = pnl
    db.add(Trade(
        user_id=user_id, position_id=position.id, symbol=position.symbol,
        side="sell" if position.side == PositionSide.LONG else "buy",
        quantity=position.quantity, price=current_price, order_type=OrderType.MARKET,
        status=TradeStatus.FILLED, filled_at=now, strategy_id=position.strategy_id,
        is_paper=True,
    ))
    account = await _get_paper_account(db, user_id)
    account.current_balance += pnl
    await db.flush()
    return position


async def close_position(
    db: AsyncSession,
    position_id: UUID,
    user_id: UUID,
) -> Position:
    """Close an existing paper-trading position at current market price.

    Args:
        db: Active DB session.
        position_id: ID of the position to close.
        user_id: Must match the position's owner.

    Returns:
        The updated (closed) ``Position``.

    Raises:
        ValueError: If the position is not found, not owned by user, or already closed.
        RuntimeError: If the quote fetch fails.
    """
    result = await db.execute(
        select(Position).where(
            Position.id == position_id, Position.user_id == user_id
        )
    )
    position = result.scalar_one_or_none()

    if position is None:
        raise ValueError(f"Position {position_id} not found")
    if position.status == PositionStatus.CLOSED:
        raise ValueError(f"Position {position_id} is already closed")

    # Fetch latest quote
    try:
        quote = await get_latest_quote(db, position.symbol)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch quote for {position.symbol}: {exc}"
        ) from exc

    exit_price = _mid_price(quote)
    if exit_price is None:
        raise RuntimeError(f"No usable price for {position.symbol} — quote is empty")

    # Calculate P&L
    if position.side == PositionSide.LONG:
        pnl = _pnl_long(position.entry_price, exit_price, position.quantity)
    else:
        pnl = _pnl_short(position.entry_price, exit_price, position.quantity)

    now = datetime.now(timezone.utc)

    position.status = PositionStatus.CLOSED
    position.exit_price = exit_price
    position.exit_date = now
    position.current_price = exit_price
    position.pnl = pnl

    # Create closing Trade record
    close_side = "sell" if position.side == PositionSide.LONG else "buy"
    trade = Trade(
        user_id=user_id,
        position_id=position.id,
        symbol=position.symbol,
        side=close_side,
        quantity=position.quantity,
        price=exit_price,
        order_type=OrderType.MARKET,
        status=TradeStatus.FILLED,
        filled_at=now,
        strategy_id=position.strategy_id,
        is_paper=True,
    )
    db.add(trade)
    await db.flush()

    # Update paper account cash balance
    paper_account = await _get_paper_account(db, user_id)
    paper_account.current_balance += pnl

    logger.info(
        "Closed position %s (%s) — P&L: %s, balance: %s",
        position_id,
        position.symbol,
        pnl,
        paper_account.current_balance,
    )
    return position


async def update_positions(
    db: AsyncSession,
    user_id: UUID,
) -> list[Position]:
    """Refresh current prices for all open positions and enforce stop-loss / take-profit.

    For each open position:
      1. Fetch latest quote and update ``current_price``.
      2. If ``current_price <= stop_loss`` (long) or ``>= stop_loss`` (short), close as loss.
      3. If ``current_price >= take_profit`` (long) or ``<= take_profit`` (short), close as gain.

    Returns the complete list of positions for this user (still-open + newly closed).
    """
    open_result = await db.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    open_positions: list[Position] = list(open_result.scalars().all())

    # Pre-fetch all quotes (best-effort; if a quote fails we skip that position)
    quotes: dict[str, Decimal | None] = {}
    for pos in open_positions:
        try:
            quote = await get_latest_quote(db, pos.symbol)
            quotes[pos.symbol] = _mid_price(quote)
        except Exception:
            logger.warning("Failed to fetch quote for %s — skipping update", pos.symbol)
            quotes[pos.symbol] = None

    for pos in open_positions:
        price = quotes.get(pos.symbol)
        if price is None:
            continue

        pos.current_price = price

        # Check stop-loss
        trigger_close = False
        close_reason = ""

        if pos.stop_loss is not None:
            if pos.side == PositionSide.LONG and price <= pos.stop_loss:
                trigger_close = True
                close_reason = f"stop-loss @ {pos.stop_loss}"
            elif pos.side == PositionSide.SHORT and price >= pos.stop_loss:
                trigger_close = True
                close_reason = f"stop-loss @ {pos.stop_loss}"

        if not trigger_close and pos.take_profit is not None:
            if pos.side == PositionSide.LONG and price >= pos.take_profit:
                trigger_close = True
                close_reason = f"take-profit @ {pos.take_profit}"
            elif pos.side == PositionSide.SHORT and price <= pos.take_profit:
                trigger_close = True
                close_reason = f"take-profit @ {pos.take_profit}"

        if trigger_close:
            # Calculate P&L
            if pos.side == PositionSide.LONG:
                pnl = _pnl_long(pos.entry_price, price, pos.quantity)
            else:
                pnl = _pnl_short(pos.entry_price, price, pos.quantity)

            now = datetime.now(timezone.utc)
            pos.status = PositionStatus.CLOSED
            pos.exit_price = price
            pos.exit_date = now
            pos.pnl = pnl

            close_side = "sell" if pos.side == PositionSide.LONG else "buy"
            trade = Trade(
                user_id=user_id,
                position_id=pos.id,
                symbol=pos.symbol,
                side=close_side,
                quantity=pos.quantity,
                price=price,
                order_type=OrderType.MARKET,
                status=TradeStatus.FILLED,
                filled_at=now,
                strategy_id=pos.strategy_id,
                is_paper=True,
            )
            db.add(trade)

            # Update paper account
            paper_account = await _get_paper_account(db, user_id)
            paper_account.current_balance += pnl

            logger.info(
                "Auto-closed %s via %s — P&L: %s",
                pos.symbol,
                close_reason,
                pnl,
            )

    await db.flush()

    # Return all positions (open + newly closed)
    all_result = await db.execute(
        select(Position)
        .where(Position.user_id == user_id)
        .order_by(Position.entry_date.desc())
    )
    return list(all_result.scalars().all())


async def get_portfolio_summary(
    db: AsyncSession,
    user_id: UUID,
) -> dict:
    """Compute a portfolio summary for the dashboard.

    Returns a dict with keys:
      - account_value
      - buying_power
      - today_pnl
      - total_return_pct
      - open_positions
      - daily_pnl
      - circuit_breaker_active
      - initial_balance
      - current_balance
    """
    paper_account = await _get_paper_account(db, user_id)
    risk = await _get_risk_settings(db, user_id)

    # Open positions
    open_result = await db.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    open_positions: list[Position] = list(open_result.scalars().all())

    # Sum of open position values (mark-to-market)
    position_values = sum(
        p.quantity * p.current_price for p in open_positions
    )

    # Unrealized P&L
    unrealized_pnl = Decimal("0")
    for p in open_positions:
        if p.side == PositionSide.LONG:
            unrealized_pnl += _pnl_long(p.entry_price, p.current_price, p.quantity)
        else:
            unrealized_pnl += _pnl_short(p.entry_price, p.current_price, p.quantity)

    account_value = paper_account.current_balance + position_values

    # Total return since inception
    initial = paper_account.initial_balance
    total_return_pct = Decimal("0")
    if initial > Decimal("0"):
        total_return_pct = ((account_value - initial) / initial) * Decimal("100")

    # Today's P&L: realized from positions closed today + unrealized
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    closed_today_result = await db.execute(
        select(func.coalesce(func.sum(Position.pnl), Decimal("0"))).where(
            Position.user_id == user_id,
            Position.status == PositionStatus.CLOSED,
            Position.exit_date >= today_start,
        )
    )
    realized_today = closed_today_result.scalar_one()
    today_pnl = realized_today + unrealized_pnl

    # Circuit breaker check — daily loss as % of initial balance
    circuit_breaker_active = False
    if risk.circuit_breaker_enabled and initial > Decimal("0"):
        daily_loss_pct = abs(today_pnl) / initial * Decimal("100") if today_pnl < Decimal("0") else Decimal("0")
        if daily_loss_pct >= risk.max_daily_loss_pct:
            circuit_breaker_active = True

    return {
        "account_value": account_value,
        "buying_power": paper_account.current_balance,
        "today_pnl": today_pnl,
        "total_return_pct": total_return_pct,
        "open_positions": len(open_positions),
        "daily_pnl": today_pnl,
        "circuit_breaker_active": circuit_breaker_active,
        "initial_balance": initial,
        "current_balance": paper_account.current_balance,
    }


async def get_open_positions(
    db: AsyncSession,
    user_id: UUID,
) -> list[Position]:
    """Return all currently open positions for a user."""
    try:
        result = await db.execute(
            select(Position)
            .where(
                Position.user_id == user_id,
                Position.status == PositionStatus.OPEN,
            )
            .order_by(Position.entry_date.desc())
        )
        return list(result.scalars().all())
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Unable to load open positions")
        return []


async def get_closed_positions(
    db: AsyncSession,
    user_id: UUID,
    limit: int = 50,
) -> list[Position]:
    """Return closed positions for a user, newest first."""
    result = await db.execute(
        select(Position)
        .where(
            Position.user_id == user_id,
            Position.status == PositionStatus.CLOSED,
        )
        .order_by(Position.exit_date.desc().nulls_last())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_trade_history(
    db: AsyncSession,
    user_id: UUID,
    limit: int = 100,
) -> list[Trade]:
    """Return trade history for a user, newest first."""
    result = await db.execute(
        select(Trade)
        .where(Trade.user_id == user_id)
        .order_by(Trade.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
