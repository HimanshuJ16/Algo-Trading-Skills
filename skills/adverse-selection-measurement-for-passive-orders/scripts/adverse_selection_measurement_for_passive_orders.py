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

Mid selection: prevailing (as-of), never nearest
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Every mid this engine uses — the fill-time reference mid *and* each horizon's
future mid — is the **prevailing** mid: the most recent observation at or
before the timestamp it is being used for. This is the practitioner convention
(an as-of / horizon join), and it is not interchangeable with nearest-in-time
snapping: nearest may resolve to an observation *after* the target, silently
lengthening the effective horizon. With a 1.9 s gap in the mid series a
"100 ms markout" computed nearest-neighbour is really a 1.9 s markout, which
collapses the latency-arbitrage vs directional-drift diagnosis this engine
exists to make.

Refusal discipline — never fabricate a mid
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Market timestamps must be strictly ascending (validated). Beyond that the
engine refuses, rather than invents, a price in three cases:

- **No as-of mid.** The market series starts after ``fill_ts``: there is no
  no-lookahead reference. The fill is skipped and recorded in
  ``missing_pre_fill`` (guarded by ``require_asof_mid``, which applies to
  *both* bases).
- **Horizon outside the window.** ``fill_ts + h`` is past the last market
  timestamp (or before the first): the horizon returns ``None`` and the fill
  is recorded in ``stats[h].truncated`` — never clamped to the last known
  price, which fabricates a ``0 bps`` markout and hides the gap.
- **Stale mid.** With ``max_mid_staleness_sec`` set, a prevailing mid older
  than that bound is refused and recorded in ``stats[h].stale`` /
  ``stale_asof_mid``. A markout measured against a mid from before a session
  gap or a halt is fabricated in the same way a clamped one is; the bound is
  off by default because no universal value exists — derive it from the
  instrument's own quote-update cadence.

A horizon that produced no markout is **not evidence of healthy flow**. The
toxicity verdict is computed over *evaluable* horizons only, and an evaluation
where nothing was evaluable reports INSUFFICIENT DATA, never "Healthy Flow".

References
~~~~~~~~~~
- Glosten, L. R. & Milgrom, P. R. (1985), "Bid, ask and transaction prices in a
  specialist market with heterogeneously informed traders", Journal of
  Financial Economics 14(1), 71-100 — the adverse-selection model of the
  bid-ask spread.
- Databento, "What are markouts and how are they used for trading?"
  (https://databento.com/microstructure/markout) — practitioner bases
  (mid-to-mid, trade-to-mid / fill-to-mid, trade-to-trade / fill-to-cross),
  markout curves across horizons, and share/notional weighting.
- QuestDB, "Post-trade markout analysis"
  (https://questdb.com/docs/cookbook/sql/finance/markout/) — the horizon mid is
  resolved by an ASOF match at ``trade_timestamp + offset``.
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
BASIS_ARRIVAL_TO_MID = "arrival_to_mid"  # fill-time mid -> future mid (industry: "mid-to-mid")
_VALID_BASIS = frozenset({BASIS_FILL_TO_MID, BASIS_ARRIVAL_TO_MID})

# Why a prevailing-mid lookup returned no price. Never fabricate one instead.
MID_OK = "ok"
MID_NO_DATA = "no_data"  # target outside [first, last] market timestamp
MID_STALE = "stale"      # an observation exists but is older than the bound


@dataclasses.dataclass
class PassiveFill:
    """A single passive (resting limit) order fill."""

    trade_id: str
    timestamp: float
    side: str  # 'BUY' or 'SELL'
    fill_price: float
    quantity: float

    def __post_init__(self) -> None:
        if not isinstance(self.side, str):
            raise ValueError(
                f"side must be the string 'BUY' or 'SELL', got {type(self.side).__name__}"
            )
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
    # fill-time mid and isolates post-fill drift (the industry's "mid-to-mid").
    markout_basis: str = BASIS_FILL_TO_MID
    # If True, weight each fill's markout by its quantity (share-weighted).
    # NOTE: weighting applies to ``mean_bps`` only — median/p25/p75/std remain
    # unweighted order statistics of the per-fill markouts.
    quantity_weighted: bool = False
    # If True, require a market observation at or before each fill_ts before
    # scoring the fill (no-lookahead). Applies to BOTH bases: under
    # 'fill_to_mid' the as-of mid is not the reference price, but its absence
    # means the fill precedes the market-data window and every horizon would be
    # measured against unrelated later data. A fill with no as-of mid is
    # skipped and recorded in ``missing_pre_fill``. Leave True in production.
    require_asof_mid: bool = True
    # Optional bound (seconds) on how old a prevailing mid may be relative to
    # the timestamp it is used for. None (default) disables the check. There is
    # no authoritative universal value — set it from the instrument's own
    # quote-update cadence (a bound far above the typical inter-quote gap only
    # catches session gaps and halts; one near it discards live data).
    max_mid_staleness_sec: Optional[float] = None

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
        if self.max_mid_staleness_sec is not None:
            s = float(self.max_mid_staleness_sec)
            if not math.isfinite(s) or s <= 0.0:
                raise ValueError(
                    "max_mid_staleness_sec must be a positive finite float or None, "
                    f"got {self.max_mid_staleness_sec}"
                )
            self.max_mid_staleness_sec = s


@dataclasses.dataclass
class HorizonStats:
    """Per-horizon aggregate statistics.

    ``mean_bps`` honours ``quantity_weighted``; the order statistics
    (``median_bps``, ``p25_bps``, ``p75_bps``) and ``std_bps`` are always
    unweighted.
    """

    count: int
    mean_bps: float
    median_bps: float
    p25_bps: float
    p75_bps: float
    std_bps: float
    truncated: int  # fills with no market observation at fill_ts + h (window too short)
    stale: int = 0  # fills whose prevailing mid at fill_ts + h exceeded the staleness bound

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
    toxicity_ratio: float  # fraction of EVALUABLE horizons with negative mean
    message: str
    markout_basis: str = BASIS_FILL_TO_MID
    # Horizons that produced at least one markout. The verdict is computed over
    # these only — a horizon with no data is not evidence of healthy flow.
    evaluable_horizons: int = 0
    # Fills skipped because the as-of reference mid was staler than the bound.
    stale_asof_mid: int = 0

    @property
    def has_sufficient_data(self) -> bool:
        """False when no horizon produced a markout — ``is_toxic`` is then
        vacuous and must not be read as a healthy-flow finding."""
        return self.evaluable_horizons > 0

    def as_dict(self) -> dict:
        return {
            "total_fills_analyzed": self.total_fills_analyzed,
            "fills_used": self.fills_used,
            "missing_pre_fill": self.missing_pre_fill,
            "stale_asof_mid": self.stale_asof_mid,
            "horizons_sec": list(self.horizons_sec),
            "evaluable_horizons": self.evaluable_horizons,
            "average_markouts_bps": dict(self.average_markouts_bps),
            "stats": {str(k): v.as_dict() for k, v in self.stats.items()},
            "is_toxic": self.is_toxic,
            "has_sufficient_data": self.has_sufficient_data,
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
        # Strict ascending order — searchsorted correctness depends on it.
        if np.any(np.diff(ts) <= 0.0):
            raise ValueError("market_timestamps must be strictly ascending")
        return ts, mids

    # -- prevailing (as-of) mid lookups ------------------------------------
    @staticmethod
    def _prevailing_index(target_ts: float, ts: np.ndarray) -> int:
        """Index of the last market observation at or before ``target_ts``.

        Returns -1 when every observation is after ``target_ts``.
        """
        return int(np.searchsorted(ts, target_ts, side="right")) - 1

    @classmethod
    def _horizon_mid(
        cls,
        target_ts: float,
        ts: np.ndarray,
        mids: np.ndarray,
        max_staleness_sec: Optional[float],
    ) -> Tuple[Optional[float], str]:
        """Prevailing mid at ``target_ts`` for a markout horizon.

        Uses the last observation at or before ``target_ts`` — the as-of
        convention — rather than the nearest in time, which may resolve to an
        observation *after* the target and silently lengthen the effective
        horizon.

        Returns ``(None, MID_NO_DATA)`` when ``target_ts`` lies outside the
        market-data window (truncation) and ``(None, MID_STALE)`` when the
        prevailing observation is older than ``max_staleness_sec``. It never
        clamps to the first or last known price.
        """
        if target_ts < ts[0] or target_ts > ts[-1]:
            return None, MID_NO_DATA
        idx = cls._prevailing_index(target_ts, ts)
        if idx < 0:
            return None, MID_NO_DATA
        if max_staleness_sec is not None and (target_ts - float(ts[idx])) > max_staleness_sec:
            return None, MID_STALE
        return float(mids[idx]), MID_OK

    @classmethod
    def _asof_mid(
        cls,
        fill_ts: float,
        ts: np.ndarray,
        mids: np.ndarray,
        max_staleness_sec: Optional[float] = None,
    ) -> Tuple[Optional[float], str]:
        """Most recent mid at or before ``fill_ts`` (no-lookahead reference).

        Unlike :meth:`_horizon_mid` a fill *after* the last observation still
        has a prevailing mid; only the staleness bound can reject it.
        """
        idx = cls._prevailing_index(fill_ts, ts)
        if idx < 0:
            return None, MID_NO_DATA  # all market data is after the fill -> would lookahead
        if max_staleness_sec is not None and (fill_ts - float(ts[idx])) > max_staleness_sec:
            return None, MID_STALE
        return float(mids[idx]), MID_OK

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
                evaluable_horizons=0,
                stale_asof_mid=0,
            )

        ts, mids = self._validate_market_data(market_timestamps, market_mids)
        horizons = self.config.horizons_sec
        staleness = self.config.max_mid_staleness_sec
        arrival_basis = self.config.markout_basis == BASIS_ARRIVAL_TO_MID
        # The as-of mid is the reference price under 'arrival_to_mid' and the
        # no-lookahead guard under 'fill_to_mid'; either way its absence
        # disqualifies the fill.
        needs_asof = arrival_basis or self.config.require_asof_mid

        # marks[h] = list of (markout_bps, quantity)
        marks: Dict[float, List[Tuple[float, float]]] = {h: [] for h in horizons}
        truncated: Dict[float, int] = {h: 0 for h in horizons}
        stale: Dict[float, int] = {h: 0 for h in horizons}
        fills_used = 0
        missing_pre_fill = 0
        stale_asof_mid = 0

        for fill in fills:
            asof, asof_reason = self._asof_mid(fill.timestamp, ts, mids, staleness)
            if asof is None and needs_asof:
                if asof_reason == MID_STALE:
                    stale_asof_mid += 1
                else:
                    missing_pre_fill += 1
                continue
            ref = asof if arrival_basis else fill.fill_price

            used_this_fill = False
            for h in horizons:
                future_mid, reason = self._horizon_mid(fill.timestamp + h, ts, mids, staleness)
                if future_mid is None:
                    if reason == MID_STALE:
                        stale[h] += 1
                    else:
                        truncated[h] += 1
                    continue
                marks[h].append((self._markout_bps(fill.side, ref, future_mid), fill.quantity))
                used_this_fill = True
            if used_this_fill:
                fills_used += 1

        # Aggregate per horizon. A horizon with no markouts is NOT evidence of
        # healthy flow — it is excluded from the verdict entirely.
        avg_markouts: Dict[float, float] = {}
        stats: Dict[float, HorizonStats] = {}
        toxic_horizons = 0
        evaluable_horizons = 0
        for h in horizons:
            rows = marks[h]
            if not rows:
                avg_markouts[h] = 0.0
                stats[h] = HorizonStats(
                    count=0, mean_bps=0.0, median_bps=0.0, p25_bps=0.0,
                    p75_bps=0.0, std_bps=0.0, truncated=truncated[h], stale=stale[h],
                )
                continue
            evaluable_horizons += 1
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
                stale=stale[h],
            )
            if mean < 0:
                toxic_horizons += 1

        toxicity_ratio = toxic_horizons / evaluable_horizons if evaluable_horizons else 0.0
        is_toxic = evaluable_horizons > 0 and toxic_horizons > (evaluable_horizons / 2)

        audit = (
            f"fills_used={fills_used}, missing_pre_fill={missing_pre_fill}, "
            f"stale_asof_mid={stale_asof_mid}, basis={self.config.markout_basis}"
        )
        if evaluable_horizons == 0:
            event = "adverse_selection_insufficient_data"
            level = logging.WARNING
            msg = (
                f"INSUFFICIENT DATA: no horizon produced a markout "
                f"(0/{len(horizons)} horizons evaluable; {audit}). "
                "is_toxic=False here means 'not measured', NOT healthy flow — "
                "extend the market-data window and re-run."
            )
        else:
            event = "adverse_selection_toxic" if is_toxic else "adverse_selection_healthy"
            level = logging.WARNING if is_toxic else logging.INFO
            verdict = "TOXIC FLOW DETECTED" if is_toxic else "Healthy Flow"
            unevaluable = len(horizons) - evaluable_horizons
            suffix = f", {unevaluable} horizon(s) not evaluable" if unevaluable else ""
            msg = (
                f"{verdict}: {toxic_horizons}/{evaluable_horizons} evaluable horizons "
                f"negative{suffix} ({audit})."
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
                "stale_asof_mid": stale_asof_mid,
                "evaluable_horizons": evaluable_horizons,
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
            evaluable_horizons=evaluable_horizons,
            stale_asof_mid=stale_asof_mid,
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
    "MID_OK",
    "MID_NO_DATA",
    "MID_STALE",
]
