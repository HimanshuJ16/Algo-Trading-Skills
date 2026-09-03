"""Vendor-specific corporate action adjustment methodology reconciliation.

Models how market data vendors back-adjust historical price and volume series for
corporate actions, and reconciles two vendor series to surface methodology divergence.

Methodology sources (see references/standards.md for full citations):

* CRSP, *Data Definitions* ("Factor to Adjust Price"): for stock dividends and splits the
  factor is the number of additional shares per old share; **for ordinary cash dividends
  the Factor to Adjust Price is set to zero**; for spin-offs, non-total liquidating
  distributions and rights it is the distributed cash amount divided by the price on the
  ex-distribution date.
* CRSP, *CRSP Calculations* ("Adjusted Data"): "Split events always include stock splits,
  stock dividends, and other distributions with price factors such as spin-offs, stock
  distributions, and rights. **Shares and volumes are only adjusted using stock splits and
  stock dividends.** Split events are applied on the Ex-Distribution Date."
* Xignite/QUODD, *Corporate Actions Handling in GlobalHistorical v3*: dividend adjustment
  factor = (previous day closing price - dividend amount) / (previous day closing price);
  "If there are multiple corporate actions on the same EX date, individual adjustment
  factors are multiplied"; "Volume is only adjusted for the corporate events that change
  the shares outstanding of the security on the EX date."

Two independent cumulative factors are therefore maintained:

    F_price[t] = product of price factors of every action with ex_date > t
    F_share[t] = product of price factors of *share-count changing* actions with ex_date > t

    P_adj[t] = P_raw[t] * F_price[t]
    V_adj[t] = V_raw[t] / F_share[t]

Cash dividends and spin-offs move F_price only; they never rescale volume.
"""

import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class CorporateActionType(Enum):
    """Corporate action classes distinguished by how vendors adjust price and volume."""

    #: Ordinary cash dividend. Price-only adjustment, and only under total-return
    #: methodologies (Bloomberg ``adjustmentNormal`` axis).
    CASH_DIVIDEND = "CASH_DIVIDEND"
    #: Special / extraordinary cash distribution. Price-only adjustment, applied by
    #: price-return methodologies as well (Bloomberg ``adjustmentAbnormal`` axis).
    SPECIAL_DIVIDEND = "SPECIAL_DIVIDEND"
    #: Forward split. ``split_ratio`` is new shares per old share, so a 2-for-1 split is
    #: ``split_ratio=2.0`` and yields a price factor of 1/2 = 0.5.
    STOCK_SPLIT = "STOCK_SPLIT"
    #: Reverse split. Still expressed as new shares per old share, so a 1-for-10 reverse
    #: split is ``split_ratio=0.1`` and yields a price factor of 1/0.1 = 10.0.
    REVERSE_SPLIT = "REVERSE_SPLIT"
    #: Spin-off. ``cash_amount`` carries the per-share value of the distributed entity.
    #: Price-only adjustment: a spin-off changes company assets, not shares outstanding.
    SPIN_OFF = "SPIN_OFF"


class VendorMethodology(Enum):
    """Vendor adjustment conventions.

    ``CRSP_TOTAL_RETURN`` and ``BLOOMBERG_PROPORTIONAL`` both denote a *total-return*
    convention in which ordinary cash dividends are folded into the price series with a
    proportional factor. Note that this is **not** the same as CRSP's own ``CFACPR``
    adjusted price series: CRSP sets the Factor to Adjust Price to zero for ordinary cash
    dividends and delivers dividend income through its return series instead.
    """

    #: Splits, ordinary cash dividends, special dividends and spin-offs all adjust price.
    CRSP_TOTAL_RETURN = "CRSP_TOTAL_RETURN"
    #: Same three axes enabled, matching Bloomberg DPDF with normal + abnormal + split on.
    BLOOMBERG_PROPORTIONAL = "BLOOMBERG_PROPORTIONAL"
    #: Price-return convention: every action adjusts price *except* ordinary cash
    #: dividends. Special dividends and spin-offs still adjust price.
    SPLIT_ONLY_PRICE_RETURN = "SPLIT_ONLY_PRICE_RETURN"
    #: Raw exchange prints; no adjustment at all.
    RAW_UNADJUSTED = "RAW_UNADJUSTED"

    @classmethod
    def _missing_(cls, value: object) -> Optional["VendorMethodology"]:
        """Accept the legacy ``"SPLIT_ONLY"`` serialization of the price-return mode."""
        if value == "SPLIT_ONLY":
            return cls.SPLIT_ONLY_PRICE_RETURN
        return None


class ReconciliationError(Exception):
    """Base exception for Vendor Adjustment Reconciliation Engine errors."""


class AdjustmentValidationError(ReconciliationError):
    """Raised when price bars or corporate action records are unusable as supplied."""


#: Actions that change shares outstanding and therefore rescale historical volume.
SHARE_CHANGING_ACTIONS = frozenset(
    {CorporateActionType.STOCK_SPLIT, CorporateActionType.REVERSE_SPLIT}
)
#: Price-only distributions applied by price-return as well as total-return methodologies.
ABNORMAL_DISTRIBUTIONS = frozenset(
    {CorporateActionType.SPECIAL_DIVIDEND, CorporateActionType.SPIN_OFF}
)
#: Price-only distributions applied only by total-return methodologies.
ORDINARY_CASH_DISTRIBUTIONS = frozenset({CorporateActionType.CASH_DIVIDEND})

# methodology -> (apply share-count changes, apply ordinary cash, apply abnormal cash)
_METHODOLOGY_AXES: Dict[VendorMethodology, Tuple[bool, bool, bool]] = {
    VendorMethodology.CRSP_TOTAL_RETURN: (True, True, True),
    VendorMethodology.BLOOMBERG_PROPORTIONAL: (True, True, True),
    VendorMethodology.SPLIT_ONLY_PRICE_RETURN: (True, False, True),
    VendorMethodology.RAW_UNADJUSTED: (False, False, False),
}


@dataclass
class CorporateAction:
    """A single corporate action applied on its ex-distribution date.

    Args:
        event_id: Vendor or internal identifier for the event.
        symbol: Instrument the action belongs to; must match the bars it is applied to.
        ex_date: Ex-distribution date. The adjustment applies to bars strictly *before*
            this date; the ex-date bar itself is already quoted post-action.
        action_type: See :class:`CorporateActionType`.
        cash_amount: Cash per share for dividends, or per-share value of the distributed
            entity for a spin-off. Ignored for splits.
        split_ratio: New shares per old share (2.0 for a 2-for-1 forward split, 0.1 for a
            1-for-10 reverse split). Ignored for non-split actions.
        cum_price: Closing price on the last session *before* ``ex_date``, used as the
            denominator of the proportional distribution factor.
    """

    event_id: str
    symbol: str
    ex_date: datetime.date
    action_type: CorporateActionType
    cash_amount: float = 0.0
    split_ratio: float = 1.0
    cum_price: float = 0.0


@dataclass
class PriceBar:
    symbol: str
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ReconciliationDivergence:
    date: datetime.date
    symbol: str
    vendor_a_price: float
    vendor_b_price: float
    absolute_diff: float
    percentage_diff: float
    is_anomaly: bool
    #: Set when the pair could not be compared numerically (non-finite or non-positive
    #: mid price). Such dates are always flagged rather than silently passed.
    reason: str = "TOLERANCE_BREACH"


@dataclass
class ReconciliationReport:
    symbol: str
    vendor_a_name: str
    vendor_b_name: str
    total_bars_compared: int
    divergence_count: int
    max_divergence_pct: float
    mean_divergence_pct: float
    divergences: List[ReconciliationDivergence] = field(default_factory=list)
    status: str = "PASSED"
    #: Dates present in vendor A's series but absent from vendor B's, and vice versa.
    dates_only_in_a: int = 0
    dates_only_in_b: int = 0
    #: Compared dates as a percentage of the union of both vendors' dates. A high
    #: divergence-free score over a low coverage percentage is not a clean reconciliation.
    coverage_pct: float = 100.0


class VendorAdjustmentReconciliationEngine:
    """Institutional Vendor-Specific Adjustment Methodology Reconciliation Engine.

    Reconciles historical price series across market data vendors, models corporate action
    adjustment factor equations (cash dividends, special dividends, spin-offs, forward and
    reverse splits), detects vendor adjustment methodology divergences, and produces audit
    reconciliation reports.

    The engine holds no mutable state; instances are safe to reuse across symbols.
    """

    def __init__(self) -> None:
        logger.debug("Initialized Vendor-Specific Adjustment Methodology Reconciliation Engine")

    # ------------------------------------------------------------------ validation

    @staticmethod
    def _validate_bars(bars: Sequence[PriceBar]) -> List[PriceBar]:
        """Rejects duplicate dates, non-finite prices, negative volume and mixed symbols."""
        sorted_bars = sorted(bars, key=lambda b: b.date)
        seen: Dict[datetime.date, str] = {}
        symbols = set()

        for bar in sorted_bars:
            if bar.date in seen:
                raise AdjustmentValidationError(
                    f"Duplicate price bar for {bar.symbol} on {bar.date}; "
                    "de-duplicate the series before adjusting."
                )
            seen[bar.date] = bar.symbol
            symbols.add(bar.symbol)

            for label, value in (
                ("open", bar.open),
                ("high", bar.high),
                ("low", bar.low),
                ("close", bar.close),
            ):
                if not math.isfinite(value) or value <= 0.0:
                    raise AdjustmentValidationError(
                        f"Non-finite or non-positive {label} price {value!r} for "
                        f"{bar.symbol} on {bar.date}."
                    )
            if not math.isfinite(bar.volume) or bar.volume < 0.0:
                raise AdjustmentValidationError(
                    f"Invalid volume {bar.volume!r} for {bar.symbol} on {bar.date}."
                )

        if len(symbols) > 1:
            raise AdjustmentValidationError(
                f"Price bars span multiple symbols {sorted(symbols)}; adjust one symbol at a time."
            )
        return sorted_bars

    @staticmethod
    def _validate_action(action: CorporateAction, bar_symbol: Optional[str]) -> None:
        """Validates a corporate action that is about to be applied."""
        if bar_symbol is not None and action.symbol != bar_symbol:
            raise AdjustmentValidationError(
                f"Corporate action {action.event_id} belongs to {action.symbol!r} but the "
                f"price series is {bar_symbol!r}; cross-symbol adjustment is never valid."
            )

        if action.action_type in SHARE_CHANGING_ACTIONS:
            if not math.isfinite(action.split_ratio) or action.split_ratio <= 0.0:
                raise AdjustmentValidationError(
                    f"Corporate action {action.event_id} ({action.action_type.value}) has "
                    f"split_ratio={action.split_ratio!r}; expected new shares per old share > 0 "
                    "(2.0 for a 2-for-1 forward split, 0.1 for a 1-for-10 reverse split)."
                )
            return

        # Proportional distributions: cash dividends, special dividends, spin-offs.
        if not math.isfinite(action.cash_amount) or action.cash_amount <= 0.0:
            raise AdjustmentValidationError(
                f"Corporate action {action.event_id} ({action.action_type.value}) has "
                f"cash_amount={action.cash_amount!r}; a distributed value > 0 is required "
                "(for a spin-off this is the per-share value of the distributed entity)."
            )
        if not math.isfinite(action.cum_price) or action.cum_price <= 0.0:
            raise AdjustmentValidationError(
                f"Corporate action {action.event_id} ({action.action_type.value}) has "
                f"cum_price={action.cum_price!r}; the last close before the ex-date is "
                "required as the proportional factor denominator."
            )

    # ------------------------------------------------------------------ factors

    def _applicable_actions(
        self,
        actions: Sequence[CorporateAction],
        methodology: VendorMethodology,
        as_of: Optional[datetime.date],
    ) -> List[CorporateAction]:
        """Filters actions to those the methodology adjusts for and that have gone ex."""
        apply_shares, apply_ordinary, apply_abnormal = _METHODOLOGY_AXES[methodology]
        selected: List[CorporateAction] = []

        for action in actions:
            if as_of is not None and action.ex_date > as_of:
                logger.debug(
                    "Skipping %s (%s) with ex_date %s after as_of %s",
                    action.event_id, action.action_type.value, action.ex_date, as_of,
                )
                continue

            if action.action_type in SHARE_CHANGING_ACTIONS:
                include = apply_shares
            elif action.action_type in ORDINARY_CASH_DISTRIBUTIONS:
                include = apply_ordinary
            elif action.action_type in ABNORMAL_DISTRIBUTIONS:
                include = apply_abnormal
            else:  # pragma: no cover - guards future enum members
                raise AdjustmentValidationError(
                    f"Unsupported corporate action type {action.action_type!r}; "
                    "extend _METHODOLOGY_AXES before using it."
                )

            if include:
                selected.append(action)

        return selected

    def _group_factors_by_ex_date(
        self, actions: Sequence[CorporateAction]
    ) -> Dict[datetime.date, Tuple[float, float]]:
        """Collapses actions sharing an ex-date into one (price factor, share factor) pair.

        Following the Xignite/QUODD convention, factors for distinct actions on the same
        ex-date are multiplied, while multiple cash distributions of the same class on one
        ex-date are summed into a single proportional factor.
        """
        split_factor: Dict[datetime.date, float] = {}
        cash_by_class: Dict[Tuple[datetime.date, str], Tuple[float, float]] = {}

        for action in actions:
            if action.action_type in SHARE_CHANGING_ACTIONS:
                split_factor[action.ex_date] = (
                    split_factor.get(action.ex_date, 1.0) * (1.0 / action.split_ratio)
                )
                continue

            klass = (
                "ORDINARY"
                if action.action_type in ORDINARY_CASH_DISTRIBUTIONS
                else action.action_type.value
            )
            key = (action.ex_date, klass)
            total_cash, cum_price = cash_by_class.get(key, (0.0, action.cum_price))
            if cum_price != action.cum_price:
                logger.warning(
                    "Conflicting cum_price for %s distributions on %s (%s vs %s); using %s.",
                    klass, action.ex_date, cum_price, action.cum_price, cum_price,
                )
            cash_by_class[key] = (total_cash + action.cash_amount, cum_price)

        grouped: Dict[datetime.date, Tuple[float, float]] = {
            ex_date: (factor, factor) for ex_date, factor in split_factor.items()
        }

        for (ex_date, klass), (total_cash, cum_price) in cash_by_class.items():
            if total_cash >= cum_price:
                raise AdjustmentValidationError(
                    f"Aggregate {klass} distribution {total_cash} on {ex_date} is not less "
                    f"than the cum-date price {cum_price}; the proportional factor "
                    "1 - D/P would be non-positive and would invert the price series."
                )
            price_factor = 1.0 - (total_cash / cum_price)
            prev_price, prev_share = grouped.get(ex_date, (1.0, 1.0))
            # Distributions move price only; shares outstanding are unchanged.
            grouped[ex_date] = (prev_price * price_factor, prev_share)

        return grouped

    def calculate_adjustment_factors(
        self,
        bars: List[PriceBar],
        actions: List[CorporateAction],
        methodology: VendorMethodology,
        as_of: Optional[datetime.date] = None,
    ) -> Dict[datetime.date, Tuple[float, float]]:
        """Calculates cumulative price and volume adjustment factors per bar date.

        The factor for a bar is the product of the factors of every applicable action whose
        ex-date is strictly after that bar's date, so an ex-date that has no matching bar
        (market holiday, data gap, or an ex-date beyond the end of the series) is still
        applied to all earlier history.

        Args:
            bars: Raw unadjusted OHLCV bars for a single symbol.
            actions: Corporate actions for that symbol.
            methodology: Vendor convention to emulate.
            as_of: When supplied, actions with an ex-date after this date are ignored.
                Use it to keep announced-but-not-yet-effective actions out of history.

        Returns:
            Mapping of bar date to ``(cumulative_price_factor, cumulative_volume_factor)``
            such that ``P_adj = P_raw * price_factor`` and ``V_adj = V_raw * volume_factor``.

        Raises:
            AdjustmentValidationError: If bars or applicable actions are unusable.
        """
        if not bars:
            return {}

        sorted_bars = self._validate_bars(bars)
        factors: Dict[datetime.date, Tuple[float, float]] = {}

        if methodology == VendorMethodology.RAW_UNADJUSTED:
            return {bar.date: (1.0, 1.0) for bar in sorted_bars}

        bar_symbol = sorted_bars[0].symbol
        applicable = self._applicable_actions(actions, methodology, as_of)
        for action in applicable:
            self._validate_action(action, bar_symbol)

        last_bar_date = sorted_bars[-1].date
        for action in applicable:
            if action.ex_date > last_bar_date:
                logger.warning(
                    "Corporate action %s (%s) has ex_date %s after the last bar %s; the "
                    "entire history will be adjusted. Pass as_of= if it has not gone ex.",
                    action.event_id, action.action_type.value, action.ex_date, last_bar_date,
                )

        grouped = self._group_factors_by_ex_date(applicable)
        ex_dates_desc = sorted(grouped.keys(), reverse=True)

        cum_price_fac = 1.0
        cum_share_fac = 1.0
        idx = 0

        for bar in reversed(sorted_bars):
            # Fold in every action that goes ex strictly after this bar.
            while idx < len(ex_dates_desc) and ex_dates_desc[idx] > bar.date:
                price_fac, share_fac = grouped[ex_dates_desc[idx]]
                cum_price_fac *= price_fac
                cum_share_fac *= share_fac
                idx += 1

            factors[bar.date] = (cum_price_fac, 1.0 / cum_share_fac)

        return factors

    def adjust_price_series(
        self,
        bars: List[PriceBar],
        actions: List[CorporateAction],
        methodology: VendorMethodology,
        as_of: Optional[datetime.date] = None,
        price_decimals: Optional[int] = None,
        volume_decimals: Optional[int] = None,
    ) -> List[PriceBar]:
        """Adjusts a raw price series using the specified vendor methodology.

        Prices are multiplied by the cumulative price factor; volume is multiplied by the
        cumulative volume factor, which only ever reflects share-count changing actions.

        Args:
            bars: Raw unadjusted OHLCV bars for a single symbol.
            actions: Corporate actions for that symbol.
            methodology: Vendor convention to emulate.
            as_of: See :meth:`calculate_adjustment_factors`.
            price_decimals: Optional rounding for presentation. Left as ``None`` by default
                because rounding every adjusted bar accumulates tracking error over long
                histories and distorts volume-weighted calculations.
            volume_decimals: Optional rounding for adjusted volume.

        Returns:
            Adjusted bars sorted by date.
        """
        factors = self.calculate_adjustment_factors(bars, actions, methodology, as_of=as_of)

        def _round(value: float, decimals: Optional[int]) -> float:
            return value if decimals is None else round(value, decimals)

        adjusted_bars = [
            PriceBar(
                symbol=bar.symbol,
                date=bar.date,
                open=_round(bar.open * factors[bar.date][0], price_decimals),
                high=_round(bar.high * factors[bar.date][0], price_decimals),
                low=_round(bar.low * factors[bar.date][0], price_decimals),
                close=_round(bar.close * factors[bar.date][0], price_decimals),
                volume=_round(bar.volume * factors[bar.date][1], volume_decimals),
            )
            for bar in sorted(bars, key=lambda b: b.date)
        ]

        return adjusted_bars

    # ------------------------------------------------------------------ reconciliation

    @staticmethod
    def _index_series(series: Sequence[PriceBar], vendor_name: str) -> Dict[datetime.date, PriceBar]:
        indexed: Dict[datetime.date, PriceBar] = {}
        for bar in series:
            if bar.date in indexed:
                raise AdjustmentValidationError(
                    f"Vendor '{vendor_name}' series contains duplicate bars for {bar.date}; "
                    "de-duplicate before reconciling."
                )
            indexed[bar.date] = bar
        return indexed

    def reconcile_vendor_series(
        self,
        symbol: str,
        series_a: List[PriceBar],
        vendor_a_name: str,
        series_b: List[PriceBar],
        vendor_b_name: str,
        tolerance_pct: float = 0.5,
        min_coverage_pct: float = 0.0,
    ) -> ReconciliationReport:
        """Reconciles two vendor adjusted price series on their common dates.

        Closing prices are compared on the mid-price percentage difference. Pairs that
        cannot be compared numerically (a non-finite close, or a non-positive mid price
        with a non-zero difference) are flagged rather than silently passed, because
        ``nan > tolerance`` is ``False`` and would otherwise report a clean reconciliation.
        Such dates carry an infinite percentage difference, are excluded from
        ``mean_divergence_pct`` (which is ``nan`` if no date was comparable), and are
        distinguished from ordinary breaches by ``ReconciliationDivergence.reason``.

        Args:
            symbol: Instrument being reconciled.
            series_a: Adjusted bars from the first vendor.
            vendor_a_name: Label for the first vendor.
            series_b: Adjusted bars from the second vendor.
            vendor_b_name: Label for the second vendor.
            tolerance_pct: Percentage difference above which a date is an anomaly.
            min_coverage_pct: When > 0, the report fails if the compared dates are less
                than this percentage of the union of both vendors' dates. Divergence-free
                agreement over a small overlap is not evidence of a clean series.

        Raises:
            ReconciliationError: If the two series share no dates.
            AdjustmentValidationError: If ``tolerance_pct`` is invalid or a series contains
                duplicate dates.
        """
        if not math.isfinite(tolerance_pct) or tolerance_pct < 0.0:
            raise AdjustmentValidationError(
                f"tolerance_pct must be a finite non-negative percentage, got {tolerance_pct!r}."
            )
        if not math.isfinite(min_coverage_pct) or not 0.0 <= min_coverage_pct <= 100.0:
            raise AdjustmentValidationError(
                f"min_coverage_pct must be within [0, 100], got {min_coverage_pct!r}."
            )

        map_a = self._index_series(series_a, vendor_a_name)
        map_b = self._index_series(series_b, vendor_b_name)

        common_dates = sorted(set(map_a).intersection(map_b))
        if not common_dates:
            raise ReconciliationError(
                f"No common dates found between '{vendor_a_name}' and '{vendor_b_name}'."
            )

        union_size = len(set(map_a).union(map_b))
        coverage_pct = len(common_dates) / union_size * 100.0
        only_in_a = len(set(map_a) - set(map_b))
        only_in_b = len(set(map_b) - set(map_a))

        divergences: List[ReconciliationDivergence] = []
        all_pct_diffs: List[float] = []
        comparable_pct_diffs: List[float] = []
        uncomparable = 0

        for day in common_dates:
            price_a = map_a[day].close
            price_b = map_b[day].close
            mean_price = (price_a + price_b) / 2.0

            if not (math.isfinite(price_a) and math.isfinite(price_b)):
                abs_diff = float("inf")
                pct_diff = float("inf")
                reason = "NON_FINITE_PRICE"
            else:
                abs_diff = abs(price_a - price_b)
                if abs_diff == 0.0:
                    pct_diff, reason = 0.0, "TOLERANCE_BREACH"
                elif mean_price <= 0.0:
                    pct_diff, reason = float("inf"), "NON_POSITIVE_MID_PRICE"
                else:
                    pct_diff, reason = abs_diff / mean_price * 100.0, "TOLERANCE_BREACH"

            all_pct_diffs.append(pct_diff)
            if math.isfinite(pct_diff):
                comparable_pct_diffs.append(pct_diff)
            else:
                uncomparable += 1

            if pct_diff > tolerance_pct:
                divergences.append(
                    ReconciliationDivergence(
                        date=day,
                        symbol=symbol,
                        vendor_a_price=price_a,
                        vendor_b_price=price_b,
                        absolute_diff=abs_diff,
                        percentage_diff=pct_diff,
                        is_anomaly=True,
                        reason=reason,
                    )
                )

        max_div = max(all_pct_diffs, default=0.0)
        mean_div = (
            sum(comparable_pct_diffs) / len(comparable_pct_diffs)
            if comparable_pct_diffs
            else float("nan")
        )

        coverage_failed = coverage_pct < min_coverage_pct
        status = "FAILED" if (divergences or coverage_failed) else "PASSED"

        if coverage_pct < 100.0:
            logger.warning(
                "Vendor Reconciliation [%s]: compared %d of %d union dates (%.2f%% coverage); "
                "%d dates only in %s, %d only in %s.",
                symbol, len(common_dates), union_size, coverage_pct,
                only_in_a, vendor_a_name, only_in_b, vendor_b_name,
            )
        if uncomparable:
            logger.warning(
                "Vendor Reconciliation [%s]: %d date(s) could not be compared numerically.",
                symbol, uncomparable,
            )

        logger.info(
            "Vendor Reconciliation [%s]: %s vs %s -> Status=%s, TotalCompared=%d, "
            "Divergences=%d, MaxDiff=%s%%, Coverage=%.2f%%",
            symbol, vendor_a_name, vendor_b_name, status, len(common_dates),
            len(divergences), f"{max_div:.4f}", coverage_pct,
        )

        return ReconciliationReport(
            symbol=symbol,
            vendor_a_name=vendor_a_name,
            vendor_b_name=vendor_b_name,
            total_bars_compared=len(common_dates),
            divergence_count=len(divergences),
            max_divergence_pct=max_div,
            mean_divergence_pct=mean_div,
            divergences=divergences,
            status=status,
            dates_only_in_a=only_in_a,
            dates_only_in_b=only_in_b,
            coverage_pct=coverage_pct,
        )
