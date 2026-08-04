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
                    "min_signals": 4,
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
