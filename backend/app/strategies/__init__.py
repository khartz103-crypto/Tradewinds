"""Strategy framework — base classes, registry, and signal model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.schemas.market_data import Bar


# ── Signal ──────────────────────────────────────────────────────────────

@dataclass
class StrategySignal:
    """A trading signal produced by a strategy's analysis of price bars."""

    symbol: str
    action: Literal["buy", "sell", "hold"]
    confidence: float  # 0–100
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    indicators: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Base Strategy ───────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """Abstract base for all trading strategies.

    Subclasses must define ``name``, ``display_name``, and implement
    ``analyze()``.

    Strategy configuration is loaded from a DB model whose ``config`` JSON
    field is passed to the constructor as *config*.
    """

    name: str
    display_name: str

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def analyze(
        self, symbol: str, bars: list[Bar]
    ) -> StrategySignal | None:
        """Analyze *bars* for *symbol* and return a signal, or ``None``."""
        ...


# ── Registry ────────────────────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}


def get_strategy(name: str, config: dict | None = None) -> BaseStrategy:
    """Factory: return a configured instance of a registered strategy.

    Raises ``KeyError`` if *name* is not registered.
    """
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise KeyError(
            f"Strategy '{name}' is not registered. "
            f"Available: {list(STRATEGY_REGISTRY)}"
        )
    return cls(config=config)


def register_strategy(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """Decorator / explicit call: register a strategy class."""
    STRATEGY_REGISTRY[cls.name] = cls
    return cls
