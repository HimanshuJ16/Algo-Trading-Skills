"""Credit/debit card panel spend -> implied revenue -> earnings surprise signal.

Scales vendor panel spend (e.g. Yodlee, Bloomberg Second Measure, Earnest
Analytics, Facteus) to implied company revenue via a calibration multiplier
gamma, computes seasonality-aligned YoY growth decomposed into ticket-size
and transaction-volume components, and emits directional consensus-surprise
signals against point-in-time Wall Street consensus.

Limitations (documented, deliberate):
- `confidence_score` is a naive linear heuristic rank in [0, 1], NOT a
  calibrated probability; never use it for position sizing.
- The engine has no notion of data availability lag or vendor revisions.
  Backtests must enforce the vendor's actual delivery lag and use
  as-delivered (point-in-time) panel snapshots — see the related skill
  `backtesting-alt-data-strategies-with-realistic-availability-lag`.
- The +/-2.5% default surprise threshold is an engineering default, not a
  validated constant; calibrate it to the panel's measured prediction error.
- gamma must be recalibrated against reported 10-Q revenue whenever panel
  composition shifts; a stale gamma silently biases every signal.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


_FISCAL_QUARTER_RE = re.compile(r"^\s*(?:FY)?(\d{4})[-\s]?Q([1-4])\s*$", re.IGNORECASE)


def _parse_fiscal_quarter(label: str) -> Optional[int]:
    """
    Parse a 'YYYY-Qn' fiscal-quarter label into an absolute quarter index.

    Returns None when the label does not follow that convention, so callers
    using a different labelling scheme are not rejected outright.
    """
    match = _FISCAL_QUARTER_RE.match(label)
    if match is None:
        return None
    year, quarter = int(match.group(1)), int(match.group(2))
    return year * 4 + (quarter - 1)


def validate_yoy_alignment(current_quarter: str, prior_year_quarter: str) -> None:
    """
    Enforce the documented t vs t-4 seasonality alignment for YoY comparisons.

    Comparing a quarter against anything other than the same quarter of the
    prior fiscal year produces a number that is not YoY growth (a t vs t-1
    pair is sequential growth contaminated by seasonality). Raises ValueError
    when both labels parse as 'YYYY-Qn' and are not exactly four quarters
    apart; logs a warning and skips the check when either label uses another
    convention, since alignment then cannot be verified from the label alone.
    """
    current_index = _parse_fiscal_quarter(current_quarter)
    prior_index = _parse_fiscal_quarter(prior_year_quarter)
    if current_index is None or prior_index is None:
        logger.warning(
            "Cannot verify t vs t-4 seasonality alignment for %r vs %r: labels are "
            "not in 'YYYY-Qn' form. Confirm fiscal alignment upstream.",
            current_quarter, prior_year_quarter,
        )
        return
    offset = current_index - prior_index
    if offset != 4:
        raise ValueError(
            "YoY comparison requires seasonality-aligned quarters (t vs t-4): "
            f"{current_quarter!r} is {offset} quarter(s) after {prior_year_quarter!r}"
        )


@dataclass
class QuarterlyPanelData:
    ticker: str
    fiscal_quarter: str               # e.g. '2025-Q1'
    panel_spend_usd: float            # Raw panel spend sample
    panel_transaction_count: int      # Number of panel transactions
    consensus_revenue_usd: float      # Point-in-time Wall Street consensus revenue estimate

    def __post_init__(self) -> None:
        if not self.ticker or not self.ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if not self.fiscal_quarter or not self.fiscal_quarter.strip():
            raise ValueError("fiscal_quarter must be a non-empty string")
        _require_finite("panel_spend_usd", self.panel_spend_usd)
        if self.panel_spend_usd < 0:
            raise ValueError(f"panel_spend_usd cannot be negative, got {self.panel_spend_usd}")
        if self.panel_transaction_count < 0:
            raise ValueError(
                f"panel_transaction_count cannot be negative, got {self.panel_transaction_count}"
            )
        _require_finite("consensus_revenue_usd", self.consensus_revenue_usd)
        if self.consensus_revenue_usd <= 0:
            raise ValueError(
                f"consensus_revenue_usd must be positive, got {self.consensus_revenue_usd}"
            )


@dataclass
class GrowthDecomposition:
    """Multiplicative YoY decomposition: (1+g_revenue) = (1+g_ticket)(1+g_volume)."""
    ticket_size_growth_pct: float
    transaction_volume_growth_pct: float


@dataclass
class TransactionSignalResult:
    ticker: str
    fiscal_quarter: str
    implied_revenue_usd: float
    yoy_growth_pct: float             # NaN when no prior-year data was supplied
    predicted_surprise_pct: float
    signal: str                        # 'BEAT_BUY', 'MISS_SELL', 'NEUTRAL'
    confidence_score: float            # heuristic rank in [0,1], NOT a probability
    ticket_size_growth_pct: float = float("nan")
    transaction_volume_growth_pct: float = float("nan")


class CreditCardTransactionSignalEngine:
    """
    Alternative Data pipeline engine for scaling credit card panel spend,
    computing YoY revenue growth (with ticket/volume decomposition), and
    predicting earnings consensus surprises.
    """
    def __init__(self, panel_scaling_multiplier: float = 45.0, surprise_threshold_pct: float = 2.5):
        _require_finite("panel_scaling_multiplier", panel_scaling_multiplier)
        if panel_scaling_multiplier <= 0:
            raise ValueError(
                f"panel_scaling_multiplier must be > 0, got {panel_scaling_multiplier}"
            )
        _require_finite("surprise_threshold_pct", surprise_threshold_pct)
        if surprise_threshold_pct < 0:
            raise ValueError(
                f"surprise_threshold_pct must be >= 0, got {surprise_threshold_pct}"
            )
        self.panel_scaling_multiplier = panel_scaling_multiplier
        self.surprise_threshold_pct = surprise_threshold_pct

    def estimate_implied_revenue(self, panel_spend_usd: float) -> float:
        """
        Scales sample panel spend to total implied company revenue:
        R_implied = panel_spend * gamma. Recalibrate gamma against reported
        10-Q revenue whenever panel composition shifts.
        """
        _require_finite("panel_spend_usd", panel_spend_usd)
        if panel_spend_usd < 0:
            raise ValueError("Panel spend cannot be negative.")
        return float(panel_spend_usd * self.panel_scaling_multiplier)

    def calculate_yoy_growth(self, current_implied_revenue: float,
                             prior_year_implied_revenue: float) -> float:
        """
        Year-over-Year growth in percent between seasonality-aligned
        quarters (t vs t-4). A non-positive prior-year base is a data
        error, not zero growth — raises ValueError instead of returning
        a silently wrong 0.0.
        """
        _require_finite("current_implied_revenue", current_implied_revenue)
        _require_finite("prior_year_implied_revenue", prior_year_implied_revenue)
        if current_implied_revenue < 0:
            raise ValueError(
                f"current_implied_revenue cannot be negative, got {current_implied_revenue}"
            )
        if prior_year_implied_revenue <= 0:
            raise ValueError(
                "prior_year_implied_revenue must be positive to compute YoY growth, "
                f"got {prior_year_implied_revenue}"
            )
        return round(float(
            (current_implied_revenue - prior_year_implied_revenue)
            / prior_year_implied_revenue * 100.0
        ), 2)

    @staticmethod
    def decompose_growth(current: QuarterlyPanelData,
                         prior_year: QuarterlyPanelData) -> GrowthDecomposition:
        """
        Decomposes panel YoY spend growth into average ticket-size growth
        and transaction-volume growth, linked multiplicatively:
        (1 + g_spend) = (1 + g_ticket) * (1 + g_volume). The gamma scaling
        multiplier cancels, so this is computed from raw panel quantities.

        Diverging volume growth with stable ticket growth suggests a panel
        composition shift rather than a true demand signal.
        """
        validate_yoy_alignment(current.fiscal_quarter, prior_year.fiscal_quarter)
        if current.panel_transaction_count <= 0 or prior_year.panel_transaction_count <= 0:
            raise ValueError(
                "decompose_growth requires panel_transaction_count > 0 in both periods "
                f"(got {prior_year.panel_transaction_count}, {current.panel_transaction_count})"
            )
        if prior_year.panel_spend_usd <= 0:
            raise ValueError(
                "decompose_growth requires prior_year.panel_spend_usd > 0, "
                f"got {prior_year.panel_spend_usd}"
            )
        current_ticket = current.panel_spend_usd / current.panel_transaction_count
        prior_ticket = prior_year.panel_spend_usd / prior_year.panel_transaction_count
        ticket_growth = (current_ticket / prior_ticket - 1.0) * 100.0
        volume_growth = (
            current.panel_transaction_count / prior_year.panel_transaction_count - 1.0
        ) * 100.0
        return GrowthDecomposition(
            ticket_size_growth_pct=round(float(ticket_growth), 4),
            transaction_volume_growth_pct=round(float(volume_growth), 4),
        )

    def generate_signal(
        self,
        current_data: QuarterlyPanelData,
        prior_year_data: Optional[QuarterlyPanelData] = None
    ) -> TransactionSignalResult:
        """
        Processes panel data and generates earnings beat/miss trading
        signals against point-in-time consensus.

        yoy_growth_pct is NaN when no prior-year data is supplied; a real
        0.0 always reflects computed flat growth between aligned quarters.
        Prior-year data must be the same ticker and exactly four quarters
        earlier (see `validate_yoy_alignment`).
        """
        implied_rev = self.estimate_implied_revenue(current_data.panel_spend_usd)

        yoy_growth = float("nan")
        decomposition: Optional[GrowthDecomposition] = None
        if prior_year_data is not None:
            if prior_year_data.ticker != current_data.ticker:
                raise ValueError(
                    f"Prior-year ticker {prior_year_data.ticker!r} does not match "
                    f"current ticker {current_data.ticker!r}"
                )
            validate_yoy_alignment(current_data.fiscal_quarter, prior_year_data.fiscal_quarter)
            prior_implied_rev = self.estimate_implied_revenue(prior_year_data.panel_spend_usd)
            yoy_growth = self.calculate_yoy_growth(implied_rev, prior_implied_rev)
            try:
                decomposition = self.decompose_growth(current_data, prior_year_data)
            except ValueError as exc:
                logger.warning(
                    "Growth decomposition unavailable for %s %s: %s",
                    current_data.ticker, current_data.fiscal_quarter, exc,
                )

        # Consensus Surprise calculation: ((Implied - Consensus) / Consensus) * 100
        surprise_pct = (
            (implied_rev - current_data.consensus_revenue_usd)
            / current_data.consensus_revenue_usd * 100.0
        )
        surprise_pct_rounded = round(float(surprise_pct), 2)

        # Classify on the unrounded surprise: rounding first would promote a
        # 2.496% surprise to the +2.5% threshold it has not actually reached.
        if surprise_pct >= self.surprise_threshold_pct:
            signal = "BEAT_BUY"
            # Naive linear heuristic rank; NOT a calibrated probability.
            confidence = min(1.0, round(0.50 + abs(surprise_pct_rounded) / 20.0, 2))
        elif surprise_pct <= -self.surprise_threshold_pct:
            signal = "MISS_SELL"
            confidence = min(1.0, round(0.50 + abs(surprise_pct_rounded) / 20.0, 2))
        else:
            signal = "NEUTRAL"
            confidence = 0.50

        logger.info(
            f"Credit Card Signal [{current_data.ticker} {current_data.fiscal_quarter}]: "
            f"Implied=${implied_rev/1e6:.1f}M vs Consensus=${current_data.consensus_revenue_usd/1e6:.1f}M "
            f"(Surprise={surprise_pct_rounded:+.2f}%) -> {signal}"
        )

        return TransactionSignalResult(
            ticker=current_data.ticker,
            fiscal_quarter=current_data.fiscal_quarter,
            implied_revenue_usd=round(implied_rev, 2),
            yoy_growth_pct=yoy_growth,
            predicted_surprise_pct=surprise_pct_rounded,
            signal=signal,
            confidence_score=confidence,
            ticket_size_growth_pct=(
                decomposition.ticket_size_growth_pct if decomposition else float("nan")
            ),
            transaction_volume_growth_pct=(
                decomposition.transaction_volume_growth_pct if decomposition else float("nan")
            ),
        )
