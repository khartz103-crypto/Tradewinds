"""Active paper-position monitoring and stop/target enforcement."""
from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position import Position, PositionStatus
from app.services.market_data import get_latest_quote
from app.services.paper_trading import _mid_price, close_position_at_market

logger = logging.getLogger(__name__)


async def manage_positions(db: AsyncSession, user_id: UUID) -> dict:
    """Mark open positions and close triggered stops/targets.

    A missing quote is fail-safe: the position is neither updated nor closed.
    """
    try:
        result = await db.execute(select(Position).where(
            Position.user_id == user_id, Position.status == PositionStatus.OPEN
        ))
        # Keep scheduler resilient to a transient/partial DB adapter failure.
        if not hasattr(result, "scalars"):
            logger.warning("Unable to enumerate open positions; skipping management cycle")
            return {"closed": [], "monitored": 0}
        positions = list(result.scalars().all())
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Unable to enumerate open positions; skipping management cycle")
        return {"closed": [], "monitored": 0}
    closed = []
    for position in positions:
        try:
            quote = await get_latest_quote(db, position.symbol)
            price = _mid_price(quote)
        except Exception as exc:
            logger.warning("Unable to quote %s; leaving position untouched: %s", position.symbol, exc)
            continue
        if price is None:
            logger.warning("Empty quote for %s; leaving position untouched", position.symbol)
            continue

        position.current_price = price
        side = position.side.value if hasattr(position.side, "value") else str(position.side)
        reason = None
        if position.stop_loss is not None and (
            (side == "long" and price <= position.stop_loss)
            or (side == "short" and price >= position.stop_loss)
        ):
            reason = "stop-loss"
        elif position.take_profit is not None and (
            (side == "long" and price >= position.take_profit)
            or (side == "short" and price <= position.take_profit)
        ):
            reason = "take-profit"
        if reason:
            closed_position = await close_position_at_market(db, user_id, position.id, price)
            pnl = closed_position.pnl or Decimal("0")
            action = "sell" if side == "long" else "buy"
            closed.append({"symbol": position.symbol, "action": action, "pnl": pnl, "reason": reason})
            logger.info("Position manager closed %s (%s), P&L=%s", position.symbol, reason, pnl)
    await db.flush()
    return {"closed": closed, "monitored": len(positions)}
