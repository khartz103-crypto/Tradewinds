"""Seed the database with default data (admin user, strategy, risk settings).

Usage: python -m app.seed
"""

import asyncio
import os

from passlib.hash import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import Strategy, User, RiskSettings, PaperAccount


async def seed() -> None:
    """Insert default data if it does not already exist."""
    async with async_session() as session:  # type: AsyncSession
        # --- Admin user ---
        result = await session.execute(select(User).where(User.email == settings.seed_admin_email))
        existing = result.scalar_one_or_none()
        if existing is None:
            admin = User(
                email=settings.seed_admin_email,
                hashed_password=bcrypt.hash(settings.seed_admin_password[:72]),
                is_admin=True,
                is_active=True,
            )
            session.add(admin)
            await session.flush()
            admin_id = admin.id
            print(f"[seed] Created admin user: {settings.seed_admin_email}")
        else:
            admin_id = existing.id
            # Always update the password hash in case the previous seed ran with
            # a broken bcrypt version (e.g. passlib/bcrypt incompatibility).
            existing.hashed_password = bcrypt.hash(settings.seed_admin_password[:72])
            session.add(existing)
            print(f"[seed] Admin user already exists (password updated): {settings.seed_admin_email}")

        # --- Trend-following strategy ---
        result = await session.execute(
            select(Strategy).where(Strategy.name == "trend_following")
        )
        existing_strategy = result.scalar_one_or_none()
        if existing_strategy is None:
            strategy = Strategy(
                name="trend_following",
                display_name="Trend Following",
                description=(
                    "A momentum-based strategy that enters long positions when the short-term "
                    "moving average crosses above the long-term moving average, and exits when it "
                    "crosses below. Uses ADX to filter for trending markets."
                ),
                config={
                    "min_signals": 3,
                    "short_window": 20,
                    "long_window": 50,
                    "adx_threshold": 25,
                    "adx_period": 14,
                    "volume_factor": 1.5,
                },
            )
            session.add(strategy)
            await session.flush()
            print("[seed] Created trend_following strategy")
        else:
            print("[seed] trend_following strategy already exists")

        # --- Mean-reversion strategy ---
        result = await session.execute(select(Strategy).where(Strategy.name == "mean_reversion"))
        existing_strategy = result.scalar_one_or_none()
        if existing_strategy is None:
            session.add(Strategy(
                name="mean_reversion",
                display_name="Mean Reversion",
                description="A contrarian strategy that buys oversold conditions (RSI < 30, near lower Bollinger Band) and sells overbought conditions. Works best in range-bound markets.",
                config={"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
                        "bb_period": 20, "bb_std": 2.0, "min_signals": 2,
                        "atr_period": 14, "atr_stop_mult": 2.0, "atr_target_mult": 3.0},
            ))
            await session.flush()
            print("[seed] Created mean_reversion strategy")
        else:
            print("[seed] mean_reversion strategy already exists")

        # --- Momentum pullback strategy ---
        result = await session.execute(
            select(Strategy).where(Strategy.name == "momentum_pullback")
        )
        existing_strategy = result.scalar_one_or_none()
        if existing_strategy is None:
            session.add(Strategy(
                name="momentum_pullback",
                display_name="Momentum Pullback",
                description=(
                    "Enters long positions on pullbacks within a confirmed uptrend "
                    "(50-day SMA above 200-day SMA): waits for price to pull back "
                    "3-10% from a recent high, touch the 20-day SMA or lower "
                    "Bollinger Band, and begin to stabilize. Uses a 2xATR stop and "
                    "4xATR target (1:2 risk/reward). Mirrors for shorts in downtrends."
                ),
                config={
                    "trend_fast_period": 50,
                    "trend_slow_period": 200,
                    "recent_high_period": 60,
                    "pullback_min_pct": 0.03,
                    "pullback_max_pct": 0.10,
                    "bollinger_period": 20,
                    "bollinger_std": 2.0,
                    "atr_period": 14,
                    "atr_stop_mult": 2.0,
                    "atr_target_mult": 4.0,
                },
            ))
            await session.flush()
            print("[seed] Created momentum_pullback strategy")
        else:
            print("[seed] momentum_pullback strategy already exists")

        # --- Trend breakout strategy ---
        result = await session.execute(select(Strategy).where(Strategy.name == "breakout"))
        existing_strategy = result.scalar_one_or_none()
        if existing_strategy is None:
            session.add(Strategy(
                name="breakout",
                display_name="Trend Breakout",
                description=(
                    "A simple momentum strategy that buys a new 20-day high when price is "
                    "above the 200-day SMA, or shorts a new 20-day low below it. Uses a "
                    "2xATR stop and 4xATR target (1:2 risk/reward)."
                ),
                config={
                    "trend_period": 200,
                    "breakout_period": 20,
                    "atr_period": 14,
                    "atr_stop_mult": 2.0,
                    "atr_target_mult": 4.0,
                    "min_bars": 300,
                },
            ))
            await session.flush()
            print("[seed] Created breakout strategy")
        else:
            print("[seed] breakout strategy already exists")

        # --- Default risk settings for admin ---
        result = await session.execute(
            select(RiskSettings).where(RiskSettings.user_id == admin_id)
        )
        existing_risk = result.scalar_one_or_none()
        if existing_risk is None:
            risk = RiskSettings(user_id=admin_id)
            session.add(risk)
            await session.flush()
            print("[seed] Created default risk settings for admin")
        else:
            print("[seed] Risk settings already exist for admin")

        # --- Default paper account for admin ($100,000) ---
        result = await session.execute(
            select(PaperAccount).where(PaperAccount.user_id == admin_id)
        )
        existing_pa = result.scalar_one_or_none()
        if existing_pa is None:
            pa = PaperAccount(user_id=admin_id)
            session.add(pa)
            await session.flush()
            print("[seed] Created paper account for admin ($100,000)")
        else:
            print("[seed] Paper account already exists for admin")

        await session.commit()

    print("[seed] Done.")


if __name__ == "__main__":
    asyncio.run(seed())
