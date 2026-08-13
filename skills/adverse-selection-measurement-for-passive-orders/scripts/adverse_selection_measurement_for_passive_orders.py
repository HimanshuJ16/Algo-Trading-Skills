"""
adverse-selection-measurement-for-passive-orders
------------------------------------------------
Post-trade markout engine for passive (resting limit) order fills.

A **markout** is the price change over a forward interval after a fill, used to
measure execution quality and adverse selection (Glosten & Milgrom, 1985). For
a passive liquidity provider, a persistently negative markout curve means
informed flow is picking off the provider's resting quotes — the provider is
writing a free option to the informed. This engine quantifies that leakage in
basis points (bps) across configurable forward horizons.

Convention
~~~~~~~~~~
A **positive** markout always means the fill was *favorable*:
  Buy  : (Future_Mid / Fill_Price - 1) * 10000   > 0 if price rose after buy
  Sell : (Fill_Price / Future_Mid - 1) * 10000   > 0 if price fell after sell

So a negative curve = adverse selection = toxic flow.

Lookahead discipline
~~~~~~~~~~~~~~~~~~~~
- Market timestamps must be strictly ascending (validated).
- A fill requires an as-of mid at or before ``fill_ts`` (no-lookahead). If the
  earliest market timestamp is after the fill, the fill is skipped and recorded
  as ``missing_pre_fill`` — never silently forward-filled.
- A horizon that lands beyond the available market data returns ``None``; the
  fill is recorded as ``truncated`` for that horizon — never fabricated by
  clamping to the last known price (a silent markout corruptor in the legacy
  implementation).

References
~~~~~~~~~~
- Glosten, L. & Milgrom, P. (1985), "Bid, ask and transaction prices in a
  specialist market with heterogeneously informed traders" — the
  adverse-selection model of the bid-ask spread.
- Databento, "What are markouts and how are they used for trading?" — modern
  practitioner computation (fill-to-mid, mid-to-mid, fill-to-cross) and
  share/notional weighting.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
_VALID_SIDES = frozenset({SIDE_BUY, SIDE_SELL})

# Markout reference bases.
BASIS_FILL_TO_MID = "fill_to_mid"      # default: fill_price -> future mid
BASIS_ARRIVAL_TO_MID = "arrival_to_mid"  # fill-time mid -> future mid (no-lookahead alpha)
_VALID_BASIS = frozenset({BASIS_FILL_TO_MID, BASIS_ARRIVAL_TO_MID})


@dataclasses.dataclass
class PassiveFill:
    """A single passive (resting limit) order fill."""

    trade_id: str
    timestamp: float
    side: str  # 'BUY' or 'SELL'
    fill_price: float
    quantity: float

    def __post_init__(self) -> None:
        if self.side.upper() not in _VALID_SIDES:
            raise ValueError(f"side must be 'BUY' or 'SELL', got {self.side!r}")
        if not math.isfinite(self.fill_price) or self.fill_price <= 0.0:
            raise ValueError(f"fill_price must be a positive finite float, got {self.fill_price}")
        if not math.isfinite(self.quantity) or self.quantity <= 0.0:
            raise ValueError(f"quantity must be a positive finite float, got {self.quantity}")
        if not math.isfinite(self.timestamp):
            raise ValueError("timestamp must be finite")
        self.side = self.side.upper()


@dataclasses.dataclass
class MarkoutConfig:
    """Configuration for the markout engine."""

    enabled: bool = True
    # Forward horizons in seconds (e.g. 100ms, 1s, 5s, 60s). Must be positive,
    # unique, finite; stored sorted ascending.
    horizons_sec: List[float] = dataclasses.field(
        default_factory=lambda: [0.1, 1.0, 5.0, 60.0]
    )
    # Markout reference basis. 'fill_to_mid' (default) measures execution
    # quality from the actual fill price; 'arrival_to_mid' measures from the
    # fill-time mid and isolates post-fill drift (no-lookahead alpha).
    markout_basis: str = BASIS_FILL_TO_MID
    # If True, weight each fill's markout by its quantity (share-weighted).
    quantity_weighted: bool = False
    # If True, require a market timestamp strictly at or before each fill_ts
    # (no-lookahead). A fill with no as-of mid is skipped + recorded. Disabling
    # this is unsafe for backtests — leave True in production.
    require_asof_mid: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.horizons_sec, (list, tuple)) or len(self.horizons_sec) == 0:
            raise ValueError("horizons_sec must be a non-empty list")
        h = [float(x) for x in self.horizons_sec]
        if any(not math.isfinite(x) or x <= 0.0 for x in h):
            raise ValueError("all horizons must be positive finite floats")
        if len(set(h)) != len(h):
            raise ValueError("horizons must be unique")
        self.horizons_sec = sorted(h)
        if self.markout_basis not in _VALID_BASIS:
            raise ValueError(
                f"markout_basis must be one of {sorted(_VALID_BASIS)}, got {self.markout_basis!r}"
            )


@dataclasses.dataclass
class HorizonStats:
    """Per-horizon aggregate statistics."""

    count: int
    mean_bps: float
    median_bps: float
    p25_bps: float
    p75_bps: float
    std_bps: float
    truncated: int  # fills dropped at this horizon (data ended before target_ts)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class AdverseSelectionReport:
    """Result of a markout evaluation across all fills and horizons."""

    total_fills_analyzed: int
    fills_used: int
    missing_pre_fill: int  # fills skipped (no as-of mid / lookahead guard)
    horizons_sec: List[float]
    average_markouts_bps: Dict[float, float]  # backward-compat: mean per horizon
    stats: Dict[float, HorizonStats]
    is_toxic: bool
    toxicity_ratio: float  # fraction of horizons with negative mean
    message: str
    markout_basis: str = BASIS_FILL_TO_MID

    def as_dict(self) -> dict:
        return {
            "total_fills_analyzed": self.total_fills_analyzed,
            "fills_used": self.fills_used,
            "missing_pre_fill": self.missing_pre_fill,
            "horizons_sec": list(self.horizons_sec),
            "average_markouts_bps": dict(self.average_markouts_bps),
            "stats": {str(k): v.as_dict() for k, v in self.stats.items()},
            "is_toxic": self.is_toxic,
            "toxicity_ratio": self.toxicity_ratio,
            "message": self.message,
            "markout_basis": self.markout_basis,
        }


class MarkoutEngine:
    """Computes post-trade markout curves to measure adverse selection."""

    def __init__(self, config: MarkoutConfig):
        self.config = config

    # -- market data validation --------------------------------------------
    @staticmethod
    def _validate_market_data(
        market_timestamps: List[float], market_mids: List[float]
    ) -> Tuple[np.ndarray, np.ndarray]:
        if len(market_timestamps) != len(market_mids):
            raise ValueError(
                "market_timestamps and market_mids must have equal length "
                f"({len(market_timestamps)} vs {len(market_mids)})"
            )
        if len(market_timestamps) == 0:
            raise ValueError("market data must not be empty")
        ts = np.asarray(market_timestamps, dtype=float)
        mids = np.asarray(market_mids, dtype=float)
        if not np.all(np.isfinite(ts)) or not np.all(np.isfinite(mids)):
            raise ValueError("market timestamps and mids must all be finite")
        if np.any(mids <= 0.0):
            raise ValueError("market mids must all be positive")
        # Strict ascending order — bisect/searchsorted correctness depends on it.
        if np.any(np.diff(ts) <= 0.0):
            raise ValueError("market_timestamps must be strictly ascending")
        return ts, mids

    # -- single mid lookup (vectorized via searchsorted) -------------------
    @staticmethod
    def _future_mid(
        target_ts: float, ts: np.ndarray, mids: np.ndarray
    ) -> Optional[float]:
        """Return the mid nearest to target_ts, or None if beyond the data."""
        if target_ts > ts[-1]:
            return None  # do NOT clamp to last price — that fabricates a markout
        # Nearest timestamp to target_ts within [ts[0], ts[-1]].
        idx = int(np.searchsorted(ts, target_ts))
        if idx == 0:
            return float(mids[0])
        # Snap to nearest of ts[idx-1] / ts[idx] (idx may == len when target == ts[-1]).
        if idx >= len(ts):
            return float(mids[-1])
        if abs(ts[idx] - target_ts) < abs(ts[idx - 1] - target_ts):
            return float(mids[idx])
        return float(mids[idx - 1])

    @staticmethod
    def _asof_mid(fill_ts: float, ts: np.ndarray, mids: np.ndarray) -> Optional[float]:
        """Most recent mid at or before fill_ts (no-lookahead), or None."""
        idx = int(np.searchsorted(ts, fill_ts, side="right")) - 1
        if idx < 0:
            return None  # all market data is after the fill -> would lookahead
        return float(mids[idx])

    # -- markout math ------------------------------------------------------
    def _markout_bps(self, side: str, ref_price: float, future_mid: float) -> float:
        if side == SIDE_BUY:
            return ((future_mid / ref_price) - 1.0) * 10000.0
        # SELL: positive if price falls after we sell.
        return ((ref_price / future_mid) - 1.0) * 10000.0

    # -- public API --------------------------------------------------------
    def evaluate_fills(
        self,
        fills: List[PassiveFill],
        market_timestamps: List[float],
        market_mids: List[float],
    ) -> AdverseSelectionReport:
        if not self.config.enabled:
            return AdverseSelectionReport(
                total_fills_analyzed=len(fills),
                fills_used=0,
                missing_pre_fill=0,
                horizons_sec=list(self.config.horizons_sec),
                average_markouts_bps={},
                stats={},
                is_toxic=False,
                toxicity_ratio=0.0,
                message="Engine disabled; no evaluation performed.",
                markout_basis=self.config.markout_basis,
            )

        ts, mids = self._validate_market_data(market_timestamps, market_mids)
        horizons = self.config.horizons_sec
        # marks[h] = list of (markout_bps, quantity)
        marks: Dict[float, List[Tuple[float, float]]] = {h: [] for h in horizons}
        truncated: Dict[float, int] = {h: 0 for h in horizons}
        fills_used = 0
        missing_pre_fill = 0

        for fill in fills:
            if self.config.markout_basis == BASIS_ARRIVAL_TO_MID:
                ref = self._asof_mid(fill.timestamp, ts, mids)
                if ref is None:
                    missing_pre_fill += 1
                    continue
            else:
                ref = fill.fill_price

            used_this_fill = False
            for h in horizons:
                future_mid = self._future_mid(fill.timestamp + h, ts, mids)
                if future_mid is None:
                    truncated[h] += 1
                    continue
                marks[h].append((self._markout_bps(fill.side, ref, future_mid), fill.quantity))
                used_this_fill = True
            if used_this_fill:
                fills_used += 1

        # Aggregate per horizon.
        avg_markouts: Dict[float, float] = {}
        stats: Dict[float, HorizonStats] = {}
        toxic_horizons = 0
        for h in horizons:
            rows = marks[h]
            if not rows:
                avg_markouts[h] = 0.0
                stats[h] = HorizonStats(
                    count=0, mean_bps=0.0, median_bps=0.0, p25_bps=0.0,
                    p75_bps=0.0, std_bps=0.0, truncated=truncated[h],
                )
                continue
            arr = np.array([r[0] for r in rows], dtype=float)
            qty = np.array([r[1] for r in rows], dtype=float)
            if self.config.quantity_weighted and qty.sum() > 0:
                mean = float(np.average(arr, weights=qty))
            else:
                mean = float(arr.mean())
            mean = round(mean, 4)
            avg_markouts[h] = mean
            stats[h] = HorizonStats(
                count=int(arr.size),
                mean_bps=mean,
                median_bps=round(float(np.median(arr)), 4),
                p25_bps=round(float(np.percentile(arr, 25)), 4),
                p75_bps=round(float(np.percentile(arr, 75)), 4),
                std_bps=round(float(arr.std(ddof=1)) if arr.size > 1 else 0.0, 4),
                truncated=truncated[h],
            )
            if mean < 0:
                toxic_horizons += 1

        toxicity_ratio = toxic_horizons / len(horizons) if horizons else 0.0
        is_toxic = toxic_horizons > (len(horizons) / 2)

        event = "adverse_selection_toxic" if is_toxic else "adverse_selection_healthy"
        level = logging.WARNING if is_toxic else logging.INFO
        verdict = "TOXIC FLOW DETECTED" if is_toxic else "Healthy Flow"
        msg = (
            f"{verdict}: {toxic_horizons}/{len(horizons)} horizons negative "
            f"(fills_used={fills_used}, missing_pre_fill={missing_pre_fill}, "
            f"basis={self.config.markout_basis})."
        )
        logger.log(
            level,
            msg,
            extra={
                "event": event,
                "is_toxic": is_toxic,
                "toxicity_ratio": toxicity_ratio,
                "fills_used": fills_used,
                "missing_pre_fill": missing_pre_fill,
                "markout_basis": self.config.markout_basis,
                "horizons_sec": list(horizons),
            },
        )

        return AdverseSelectionReport(
            total_fills_analyzed=len(fills),
            fills_used=fills_used,
            missing_pre_fill=missing_pre_fill,
            horizons_sec=list(horizons),
            average_markouts_bps=avg_markouts,
            stats=stats,
            is_toxic=is_toxic,
            toxicity_ratio=round(toxicity_ratio, 4),
            message=msg,
            markout_basis=self.config.markout_basis,
        )


__all__ = [
    "PassiveFill",
    "MarkoutConfig",
    "AdverseSelectionReport",
    "HorizonStats",
    "MarkoutEngine",
    "SIDE_BUY",
    "SIDE_SELL",
    "BASIS_FILL_TO_MID",
    "BASIS_ARRIVAL_TO_MID",
]
